# Assignment 1: PagePilot

PagePilot is a simple Chrome extension that makes the page you are visiting easier to understand. It captures the active tab, sends the page text to a local Python API, and shows:

- a short AI summary of the page
- related content suggestions based on the website and page text
- the most relevant links on the page
- a quick page intent label

The extension uses React for the popup UI and Python for the analysis service. If you add a Gemini Flash API key, the backend will use it for smarter summaries.

## Folder Layout

- `extension/` - React Chrome extension popup
- `backend/` - FastAPI service that calls Gemini Flash or falls back to local analysis

## Run It Locally

### Backend

```bash
cd assignment1/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY="your_key_here"
uvicorn app:app --reload --port 8000
```

### Extension

```bash
cd assignment1/extension
npm install
npm run build
```

Then load `assignment1/extension/dist` in `chrome://extensions/` with Developer mode enabled.
