"""
QuickGPT – a tiny Windows popup to access ChatGPT from anywhere.

Features
- Global hotkey (default: Ctrl+Alt+Space) toggles a compact popup in the bottom-right.
- Frameless, always-on-top, rounded UI with light blur feel (via stylesheet).
- System tray icon with Show/Hide and Quit.
- Multi-turn chat; press Enter to send, Shift+Enter for newline.
- Esc hides the popup quickly.
- Persists minimal chat history to %APPDATA%/QuickGPT/history.json.
- Uses OPENAI_API_KEY from environment; optional MODEL env var (defaults to gpt-5).

Requirements
pip install PySide6 keyboard openai python-dotenv

Troubleshooting
- Set QUICKGPT_DEBUG=1 to see debug logs inside the popup.
- Avoid bare hotkeys like just "enter" or "space".
"""

import json
import html
import os
import sys
import threading
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

# Quiet noisy Qt warnings on some Windows setups
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

# Global hotkey fallback lib (if native fails)
try:
    import keyboard  # type: ignore
except Exception:
    keyboard = None

# --- Native Windows global hotkey (reliable) ---
import ctypes
from ctypes import wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312

VK_MAP: dict[str, int] = {
    "space": 0x20,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "enter": 0x0D,
}
VK_MAP.update({f"f{i}": 0x70 + i - 1 for i in range(1, 25)})
for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK_MAP[ch.lower()] = ord(ch)

user32 = ctypes.windll.user32

APP_NAME = "QuickGPT"
DEFAULT_MODEL = os.getenv("MODEL", "gpt-5")
HISTORY_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP_NAME
HISTORY_PATH = HISTORY_DIR / "history.json"
HOTKEY = os.getenv("QUICKGPT_HOTKEY", "ctrl+alt+space")
DEBUG = os.getenv("QUICKGPT_DEBUG", "0") in ("1", "true", "True")


def parse_hotkey(s: str) -> tuple[int | None, int | None]:
    parts = [p.strip().lower() for p in s.split("+") if p.strip()]
    mods = 0
    key: int | None = None
    for p in parts:
        if p in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif p == "alt":
            mods |= MOD_ALT
        elif p == "shift":
            mods |= MOD_SHIFT
        elif p in ("win", "meta", "super"):
            mods |= MOD_WIN
        else:
            key = VK_MAP.get(p)
    return mods, key


# OpenAI SDK (fallback to requests if not available)
try:
    from openai import OpenAI  # new SDK style
except Exception:
    OpenAI = None
    import requests


class Worker(QtCore.QObject):
    finished = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, messages: list[dict], model: str):
        super().__init__()
        self.messages = messages
        self.model = model

    def run(self):
        try:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set. Set it in your environment or a .env file."
                )

            if OpenAI is not None:
                client = OpenAI(api_key=api_key)
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    temperature=1,
                )
                text = resp.choices[0].message.content or ""
            else:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": self.model,
                    "messages": self.messages,
                    "temperature": 0.7,
                }
                r = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                r.raise_for_status()
                data = r.json()
                text = data["choices"][0]["message"]["content"]

            self.finished.emit(text)
        except Exception as e:
            self.error.emit(str(e))


class GradientBorderWidget(QtWidgets.QWidget):
    def __init__(self, radius: int = 16, border_width: int = 3, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._radius = radius
        self._border_width = border_width
        self._angle = 0
        self._ring_opacity = 0.0

    def set_angle(self, angle: int):
        self._angle = angle % 360
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        rect = self.rect().adjusted(1, 1, -1, -1)
        outer = QtGui.QPainterPath()
        outer.addRoundedRect(rect, self._radius, self._radius)

        inner = QtGui.QPainterPath()
        inner.addRoundedRect(
            rect.adjusted(self._border_width, self._border_width, -self._border_width, -self._border_width),
            max(0, self._radius - self._border_width),
            max(0, self._radius - self._border_width),
        )

        ring = outer.subtracted(inner)

        center = rect.center()
        grad = QtGui.QConicalGradient(center, self._angle)
        grad.setColorAt(0.0, QtGui.QColor("#ff0000"))
        grad.setColorAt(0.17, QtGui.QColor("#ff7f00"))
        grad.setColorAt(0.33, QtGui.QColor("#ffff00"))
        grad.setColorAt(0.50, QtGui.QColor("#00ff00"))
        grad.setColorAt(0.67, QtGui.QColor("#0000ff"))
        grad.setColorAt(0.83, QtGui.QColor("#4b0082"))
        grad.setColorAt(1.0, QtGui.QColor("#ff0000"))

        painter.setOpacity(self._ring_opacity)
        painter.fillPath(ring, QtGui.QBrush(grad))

    def get_ring_opacity(self) -> float:
        return self._ring_opacity

    def set_ring_opacity(self, value: float):
        self._ring_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    ringOpacity = QtCore.Property(float, get_ring_opacity, set_ring_opacity)


class ChatPopup(QtWidgets.QFrame):
    submitted = QtCore.Signal(str)
    model_changed = QtCore.Signal(str)
    clear_clicked = QtCore.Signal()
    toggle_system_clicked = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.resize(560, 400)

        self.container = GradientBorderWidget()
        self.container.setObjectName("container")

        # Top bar with close button
        self.topbar_widget = QtWidgets.QWidget()
        self.topbar_widget.setObjectName("topbar")
        topbar = QtWidgets.QHBoxLayout(self.topbar_widget)
        topbar.setContentsMargins(0, 0, 0, 0)
        topbar.setSpacing(6)
        self.title = QtWidgets.QLabel(APP_NAME)
        self.title.setStyleSheet("color:#ddd; font-weight:600;")
        topbar.addWidget(self.title)

        # Model selector (top-right)
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setObjectName("modelCombo")
        models = ["gpt-5", "o4-mini"]
        if DEFAULT_MODEL not in models:
            models.insert(0, DEFAULT_MODEL)
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(DEFAULT_MODEL)

        topbar.addStretch(1)
        topbar.addWidget(self.model_combo)

        # Clear chat button
        self.clear_btn = QtWidgets.QPushButton("🧹")
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.setFixedSize(28, 28)
        self.clear_btn.setCursor(QtCore.Qt.PointingHandCursor)
        topbar.addWidget(self.clear_btn)

        # Toggle system messages button
        self.sys_btn = QtWidgets.QPushButton("👁")
        self.sys_btn.setObjectName("sysBtn")
        self.sys_btn.setFixedSize(28, 28)
        self.sys_btn.setCursor(QtCore.Qt.PointingHandCursor)
        topbar.addWidget(self.sys_btn)

        # Close button
        self.close_btn = QtWidgets.QPushButton("❌")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        topbar.addWidget(self.close_btn)

        self.output = QtWidgets.QTextEdit()
        self.output.setReadOnly(True)
        self.output.setObjectName("output")
        self.output.setPlaceholderText("Ask me anything…")

        self.input = QtWidgets.QPlainTextEdit()
        self.input.setObjectName("input")
        self.input.setPlaceholderText("Type, then press Enter to send (Shift+Enter = newline)")
        self.input.installEventFilter(self)

        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setCursor(QtCore.Qt.PointingHandCursor)

        layout = QtWidgets.QVBoxLayout(self.container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.topbar_widget)
        layout.addWidget(self.output, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.send_btn)
        layout.addLayout(bottom)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(self.container)

        self.send_btn.clicked.connect(self._on_send)
        self.close_btn.clicked.connect(self.hide_with_anim)
        self.clear_btn.clicked.connect(self.clear_clicked.emit)
        self.sys_btn.clicked.connect(self.toggle_system_clicked.emit)
        self.model_combo.currentTextChanged.connect(self.model_changed.emit)

        self._apply_styles()

        self._rainbow_timer = QtCore.QTimer(self)
        self._rainbow_timer.timeout.connect(self._update_rainbow)
        self._rainbow_angle = 0
        
    def _apply_styles(self):
        self.setStyleSheet(
            """
            QFrame { background: transparent; }
            #container {
                background: rgba(30,30,35,235);
                border-radius: 16px;
            }
            #topbar { margin-top: 2px; }
            #closeBtn {
                background: transparent;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 16px;
            }
            #closeBtn:hover { background: rgba(255,255,255,0.08); color: #ffffff; }
            #closeBtn:pressed { background: rgba(255,255,255,0.14); }
            #clearBtn {
                background: transparent;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 16px;
            }
            #clearBtn:hover { background: rgba(255,255,255,0.08); color: #ffffff; }
            #clearBtn:pressed { background: rgba(255,255,255,0.14); }
            #sysBtn {
                background: transparent;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 16px;
            }
            #sysBtn:hover { background: rgba(255,255,255,0.08); color: #ffffff; }
            #sysBtn:pressed { background: rgba(255,255,255,0.14); }
            #output {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                padding: 8px;
                color: #f1f1f1;
                font-size: 14px;
            }
            #input {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 12px;
                padding: 8px;
                color: #f1f1f1;
                font-size: 14px;
                min-height: 44px;
                max-height: 100px;
            }
            #modelCombo {
                color: #e6e6e6;
                font-size: 12px;
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                padding: 4px 8px;
                min-height: 24px;
            }
            #modelCombo::drop-down { border: none; }
            QPushButton#sendBtn {
                background: #6C82FF;
                color: white;
                border: none;
                border-radius: 12px;
                padding: 10px 16px;
                font-weight: 600;
            }
            QPushButton#sendBtn:hover { background: #7A8DFF; }
            QPushButton#sendBtn:pressed { background: #5A72FF; }
            """
        )

    # Animated show/hide to enhance hotkey UX
    def show_bottom_right(self):
        screen = QtWidgets.QApplication.primaryScreen()
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = geo.right() - w - 16
        y = geo.bottom() - h - 16

        # Stop any ongoing animation
        if hasattr(self, "_anim_group") and self._anim_group is not None:
            try:
                self._anim_group.stop()
            except Exception:
                pass

        # Start from slightly lower and transparent
        self.setWindowOpacity(0.0)
        self.setGeometry(x, y + 20, w, h)
        self.show()
        self.activateWindow()
        self.raise_()
        self.input.setFocus()

        # Animate to final position and full opacity
        pos_anim = QtCore.QPropertyAnimation(self, b"geometry")
        pos_anim.setDuration(160)
        pos_anim.setStartValue(QtCore.QRect(x, y + 20, w, h))
        pos_anim.setEndValue(QtCore.QRect(x, y, w, h))
        pos_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        op_anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        op_anim.setDuration(160)
        op_anim.setStartValue(0.0)
        op_anim.setEndValue(1.0)

        self._anim_group = QtCore.QParallelAnimationGroup(self)
        self._anim_group.addAnimation(pos_anim)
        self._anim_group.addAnimation(op_anim)
        self._anim_group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def hide_with_anim(self):
        # Stop any ongoing animation
        if hasattr(self, "_anim_group") and self._anim_group is not None:
            try:
                self._anim_group.stop()
            except Exception:
                pass

        geo = self.geometry()
        x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()

        pos_anim = QtCore.QPropertyAnimation(self, b"geometry")
        pos_anim.setDuration(140)
        pos_anim.setStartValue(QtCore.QRect(x, y, w, h))
        pos_anim.setEndValue(QtCore.QRect(x, y + 20, w, h))
        pos_anim.setEasingCurve(QtCore.QEasingCurve.InCubic)

        op_anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        op_anim.setDuration(140)
        op_anim.setStartValue(1.0)
        op_anim.setEndValue(0.0)

        self._anim_group = QtCore.QParallelAnimationGroup(self)
        self._anim_group.addAnimation(pos_anim)
        self._anim_group.addAnimation(op_anim)
        self._anim_group.finished.connect(lambda: (self.hide(), self.setWindowOpacity(1.0)))
        self._anim_group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    # Use animated hide for Escape too, and Enter to send
    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                if event.modifiers() & QtCore.Qt.ShiftModifier:
                    return False
                self._on_send()
                return True
            elif key == QtCore.Qt.Key_Escape:
                self.hide_with_anim()
                return True
        return super().eventFilter(obj, event)

    def _on_send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.submitted.emit(text)

    def start_rainbow(self):
        self._rainbow_angle = 0
        self._update_rainbow()
        # Fade in the ring
        try:
            self._ring_anim.stop()
        except Exception:
            pass
        self._ring_anim = QtCore.QPropertyAnimation(self.container, b"ringOpacity", self)
        self._ring_anim.setDuration(220)
        self._ring_anim.setStartValue(0.0)
        self._ring_anim.setEndValue(1.0)
        self._ring_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._ring_anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
        self._rainbow_timer.start(80)

    def stop_rainbow(self):
        self._rainbow_timer.stop()
        # Fade out the ring
        try:
            self._ring_anim.stop()
        except Exception:
            pass
        self._ring_anim = QtCore.QPropertyAnimation(self.container, b"ringOpacity", self)
        self._ring_anim.setDuration(200)
        self._ring_anim.setStartValue(self.container.get_ring_opacity())
        self._ring_anim.setEndValue(0.0)
        self._ring_anim.setEasingCurve(QtCore.QEasingCurve.InCubic)
        self._ring_anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def _update_rainbow(self):
        self._rainbow_angle = (self._rainbow_angle + 5) % 360
        # Update gradient border angle
        self.container.set_angle(self._rainbow_angle)

    


class SystemTray(QtWidgets.QSystemTrayIcon):
    def __init__(self, app, popup):
        icon = QtGui.QIcon.fromTheme("chat")
        if icon.isNull():
            # Fallback simple icon
            pix = QtGui.QPixmap(64, 64)
            pix.fill(QtGui.QColor("#6C82FF"))
            painter = QtGui.QPainter(pix)
            painter.setPen(QtCore.Qt.white)
            painter.setFont(QtGui.QFont("Segoe UI", 28, QtGui.QFont.Bold))
            painter.drawText(pix.rect(), QtCore.Qt.AlignCenter, "G")
            painter.end()
            icon = QtGui.QIcon(pix)

        super().__init__(icon)
        self.app = app
        self.popup = popup
        menu = QtWidgets.QMenu()
        act_toggle = menu.addAction("Show/Hide")
        act_quit = menu.addAction("Quit")
        act_toggle.triggered.connect(self.toggle)
        act_quit.triggered.connect(app.quit)
        self.setContextMenu(menu)
        self.setToolTip(APP_NAME)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            self.toggle()

    def toggle(self):
        if self.popup.isVisible():
            self.popup.hide_with_anim()
        else:
            self.popup.show_bottom_right()


class WinHotkeyFilter(QtCore.QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback  # expects (mods, vk)

    def nativeEventFilter(self, etype, msg):
        # Listen for WM_HOTKEY and pass mods/vk to callback
        try:
            if etype != "windows_generic_MSG":
                return False, 0
            msg_ptr = int(msg)
            class MSG(ctypes.Structure):
                _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt_x", wintypes.LONG), ("pt_y", wintypes.LONG)]
            m = MSG.from_address(msg_ptr)
            if m.message == WM_HOTKEY:
                lparam = int(m.lParam)
                mods = lparam & 0xFFFF           # LOWORD
                vk = (lparam >> 16) & 0xFFFF     # HIWORD
                self.callback(mods, vk)
        except Exception:
            pass
        return False, 0


class QuickGPT(QtWidgets.QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        QtGui.QGuiApplication.setQuitOnLastWindowClosed(False)
        self.setApplicationName(APP_NAME)

        self.debug = DEBUG
        self.working = False
        self.show_system = True
        self.transcript: list[tuple[str, str]] = []

        self.popup = ChatPopup()
        self.model = DEFAULT_MODEL
        # Ensure popup reflects current model on boot
        try:
            self.popup.model_combo.setCurrentText(self.model)
        except Exception:
            pass
        self.popup.model_changed.connect(self._on_model_changed)
        self.popup.toggle_system_clicked.connect(self._toggle_system)
        self.popup.clear_clicked.connect(self._clear_chat)
        self.tray = SystemTray(self, self.popup)
        self.tray.show()

        self.messages: list[dict] = []
        self._load_history()

        self.popup.submitted.connect(self._handle_user_message)

        # --- Global hotkey registration (native first, then keyboard fallback) ---
        self._hotkey_id = 1
        self._native_ok = False
        mods, vk = parse_hotkey(HOTKEY)
        if mods is not None and vk is not None:
            try:
                if user32.RegisterHotKey(None, self._hotkey_id, mods, vk):
                    self._native_ok = True
                    self._append_system(f"Global hotkey registered (native): {HOTKEY}")
                    self._filter = WinHotkeyFilter(self._on_hotkey)
                    QtWidgets.QApplication.instance().installNativeEventFilter(self._filter)
                else:
                    err = ctypes.GetLastError()
                    self._append_system(f"Native hotkey failed (code {err}). Will try fallback library.")
            except Exception as e:
                self._append_system(f"Native hotkey error: {e}. Will try fallback library.")
        else:
            self._append_system(f"Invalid QUICKGPT_HOTKEY '{HOTKEY}'. Example: ctrl+alt+space or ctrl+shift+g")

        if not self._native_ok:
            if keyboard is not None:
                try:
                    keyboard.add_hotkey(HOTKEY, lambda: self._on_hotkey(0, 0))
                    self._append_system(f"Global hotkey registered (fallback): {HOTKEY}")
                except Exception as e:
                    self._append_system(f"Fallback hotkey '{HOTKEY}' failed: {e}. Try running as Administrator or pick another shortcut.")
            else:
                self._append_system("The 'keyboard' package is missing; global hotkey disabled. Install it with: pip install keyboard")

        self.aboutToQuit.connect(self._cleanup_hotkey)

    def _on_hotkey(self, mods: int, vk: int):
        # If user chose a bare Enter/Space hotkey, ignore while typing
        if self.popup.isVisible() and self.popup.input.hasFocus():
            if vk in (VK_MAP.get("enter"), VK_MAP.get("space")) and mods == 0:
                if self.debug:
                    self._append_system("[debug] Ignored hotkey while typing (bare Enter/Space)")
                return
        if self.debug:
            self._append_system(f"[debug] WM_HOTKEY fired mods={mods} vk={vk}")
        self._toggle_popup()

    def _toggle_popup(self):
        # Don’t hide while a request is running
        if self.working and self.popup.isVisible():
            if self.debug:
                self._append_system("[debug] Toggle blocked while working")
            return
        QtCore.QTimer.singleShot(0, self.tray.toggle)

    # Chat workflow
    def _handle_user_message(self, text: str):
        if self.debug:
            self._append_system(f"[debug] Submit: {text[:60]}")
        self._append_user(text)
        self._save_history()
        self._ask_assistant()

    def _ask_assistant(self):
        self.working = True
        self.popup.start_rainbow()
        self._append_system("Thinking…")
        messages = ([{"role": "system", "content": "You are a concise, helpful assistant. Your name is QuickGPT. Avoid typing long paragraphs in one go. Quick, concise sentences are best."}] + self.messages[-20:])
        worker = Worker(messages, self.model)
        thread = QtCore.QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Store active references to ensure cleanup in main-thread slots
        self._active_thread = thread
        self._active_worker = worker
        worker.finished.connect(self._on_reply)
        worker.error.connect(self._on_error)
        thread.start()

    @QtCore.Slot(str)
    def _on_reply(self, reply: str):
        self.popup.stop_rainbow()
        self._remove_last_system_placeholder()
        self._append_assistant(reply)
        self._save_history()
        self.working = False
        worker = getattr(self, "_active_worker", None)
        thread = getattr(self, "_active_thread", None)
        if worker is not None:
            worker.deleteLater()
            self._active_worker = None
        if thread is not None:
            thread.quit()
            thread.wait()
            thread.deleteLater()
            self._active_thread = None

    @QtCore.Slot(str)
    def _on_error(self, err: str):
        self.popup.stop_rainbow()
        self._remove_last_system_placeholder()
        self._append_system(f"Error: {err}")
        self.working = False
        worker = getattr(self, "_active_worker", None)
        thread = getattr(self, "_active_thread", None)
        if worker is not None:
            worker.deleteLater()
            self._active_worker = None
        if thread is not None:
            thread.quit()
            thread.wait()
            thread.deleteLater()
            self._active_thread = None

    # History helpers
    def _load_history(self):
        try:
            if HISTORY_PATH.exists():
                data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                self.messages = data.get("messages", [])
                # restore last model if present
                self.model = data.get("model", self.model)
                try:
                    self.popup.model_combo.setCurrentText(self.model)
                except Exception:
                    pass
                if self.messages:
                    for m in self.messages[-10:]:
                        if m["role"] == "user":
                            self._append_line("You", m["content"]) 
                        elif m["role"] == "assistant":
                            self._append_line("Assistant", m["content"]) 
        except Exception:
            self.messages = []

    def _save_history(self):
        try:
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            HISTORY_PATH.write_text(
                json.dumps({"messages": self.messages, "model": self.model}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # UI append helpers
    def _append_line(self, who: str, text: str):
        self.transcript.append((who, text))
        self._render_transcript()

    def _append_user(self, text: str):
        self.messages.append({"role": "user", "content": text})
        self._append_line("You", text)

    def _append_assistant(self, text: str):
        self.messages.append({"role": "assistant", "content": text})
        self._append_line("Assistant", text)

    def _append_system(self, text: str):
        self._append_line("System", text)

    def _remove_last_system_placeholder(self):
        for i in range(len(self.transcript) - 1, -1, -1):
            who, txt = self.transcript[i]
            if who == "System" and txt == "Thinking…":
                del self.transcript[i]
                break
        self._render_transcript()

    def _cleanup_hotkey(self):
        try:
            if getattr(self, "_native_ok", False):
                user32.UnregisterHotKey(None, self._hotkey_id)
        except Exception:
            pass

    # Model selection handler
    def _on_model_changed(self, model: str):
        self.model = model
        # Save selection promptly so it persists across restarts
        self._save_history()

    # Clear chat history handler
    def _clear_chat(self):
        self.messages = []
        self.transcript = []
        self.popup.output.clear()
        self._append_system("History cleared.")
        self._save_history()

    def _toggle_system(self):
        self.show_system = not self.show_system
        # Update emoji to indicate current state
        try:
            self.popup.sys_btn.setText("👁" if self.show_system else "🙈")
        except Exception:
            pass
        self._render_transcript()

    def _render_transcript(self):
        self.popup.output.clear()
        for who, text in self.transcript:
            if not self.show_system and who == "System":
                continue
            role = who.lower()
            color_map = {
                "system": "#FFB86C",
                "you": "#80C7FF",
                "assistant": "#C3E88D",
            }
            color = color_map.get(role, "#f1f1f1")
            safe_text = html.escape(text).replace("\n", "<br>")
            safe_who = html.escape(who)
            html_line = f"<span style='color:{color}; font-weight:600'>{safe_who}:</span> <span style='color:#e8e8e8'>{safe_text}</span><br><br>"
            cursor = self.popup.output.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            cursor.insertHtml(html_line)
        self.popup.output.ensureCursorVisible()


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    app = QuickGPT(sys.argv)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
