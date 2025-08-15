# QuickGPT

A tiny Windows popup to chat with ChatGPT from anywhere, toggled by a global hotkey. It stays out of the way, appears with a smooth animation, and keeps a minimal local history.

## Features

- Global hotkey (default: `Ctrl+Alt+Space`) to show/hide the popup
- Frameless, always‑on‑top, rounded UI with animated rainbow border while generating
- System tray icon with Show/Hide and Quit
- Multi‑turn chat; Enter sends, Shift+Enter inserts newline; Esc hides
- Model dropdown to switch between `gpt-5` and `o4-mini`
- Emoji top‑bar buttons:
  - `👁/🙈` show/hide system messages in the transcript
  - `🧹` clear chat history (also clears persisted history)
  - `❌` hide the popup
- Minimal chat history persisted at `%APPDATA%/QuickGPT/history.json`
- Uses `OPENAI_API_KEY`; optional `MODEL`, `QUICKGPT_HOTKEY`, `QUICKGPT_DEBUG`

## Requirements

- Windows 10/11
- Python 3.10+
- Packages: `PySide6`, `keyboard`, `openai`, `python-dotenv` (optional but recommended)

Install dependencies:

```bash
pip install PySide6 keyboard openai python-dotenv
```

## Quickstart

1. Set your OpenAI API key (recommended via a `.env` file in the project folder):

```env
# .env
OPENAI_API_KEY=sk-...
# Optional defaults
MODEL=gpt-5
QUICKGPT_HOTKEY=ctrl+alt+space
QUICKGPT_DEBUG=0
```

2. Run the app:

```bash
python main.py
```

3. Use the hotkey (`Ctrl+Alt+Space` by default) to show/hide the popup.

## Usage

- Type into the input field and press `Enter` to send; `Shift+Enter` makes a newline.
- Use the top bar:
  - Model dropdown to switch between `gpt-5` and `o4-mini` (persists across restarts)
  - `👁/🙈` toggles system messages (e.g., “Thinking…”, errors)
  - `🧹` clears on‑screen and persisted history
  - `❌` hides the popup (app continues running in the tray)
- The animated rainbow border appears only while generating and fades in/out smoothly.

## Configuration

Environment variables (or `.env`):

- `OPENAI_API_KEY` (required): your OpenAI API key
- `MODEL` (optional): default model, `gpt-5` or `o4-mini` (UI can change per run)
- `QUICKGPT_HOTKEY` (optional): e.g., `ctrl+alt+space`, `ctrl+shift+g`
- `QUICKGPT_DEBUG` (optional): set to `1` to print debug lines in the transcript

History is stored at:

- `%APPDATA%/QuickGPT/history.json`

## Troubleshooting

- Hotkey doesn’t work:
  - Some shortcuts require elevated privileges on Windows; try running a terminal as Administrator or choose a different key combo.
  - The app uses a native Windows hotkey first; if it fails, it falls back to the `keyboard` package.
- No output / API errors:
  - Ensure `OPENAI_API_KEY` is set and valid.
  - Verify internet connectivity and that your key has access to the selected model.
- Emojis look small or misaligned:
  - Windows emoji rendering varies by font; increasing DPI/scaling or font size in code can help. Ask if you want me to adjust sizes.

