# ChatMap Agent

ChatMap Agent is a small Tkinter desktop app that turns long conversation logs into a structured summary and a Mermaid-based topic graph using the Gemini API.

It can export:

- Markdown reports
- Mermaid graph text
- A standalone ChatMap HTML viewer with search, filters, node details, decisions, open questions, and next actions

## Requirements

- Python 3.10 or later
- Gemini API key

Tkinter is included with most Python installs. On some Linux environments, you may need to install the Tkinter package from your OS package manager.

## Setup

```powershell
cd chatmap
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set your Gemini API key:

```env
GEMINI_API_KEY=your_actual_api_key
```

## Run

```powershell
python app_tkinter.py
```

You can also paste the API key directly into the app instead of using `.env`.

## Basic Workflow

1. Paste a conversation log into the left text area, or open a `.txt` / `.md` log file.
2. Click `解析する`.
3. Review the summary, Mermaid graph, JSON, and chunks.
4. Export the result with `Markdown保存` or `ChatMap HTML保存`.

## Notes

- `ChatMap HTML保存` uses Mermaid from a CDN, so the exported HTML viewer needs internet access unless you modify it to bundle Mermaid locally.
- Gemini may occasionally return `503 UNAVAILABLE` when the selected model is under high demand. The app retries automatically, but if it keeps failing, wait a bit or try another model in the `Model` field.
- Do not commit `.env`. It is ignored by `.gitignore`.

## License

No license has been selected yet. Add a `LICENSE` file before publishing if you want others to reuse or modify this code under explicit terms.
