# Liberty Restoration — Inventory Bot

A local web app for creating insurance inventory lists from job-site photos. Field staff point it at a folder of photos; the app uses Claude AI to identify each item, then produces an Excel spreadsheet with item descriptions, quantities, estimated values, and ages.

---

## How It Works

1. **Import** — Select a folder of photos from a completed job site.
2. **Group** — Review the auto-detected groupings. Click ⊕ between photos to merge multiple shots of the same item into one entry. The estimated API cost is shown before you commit.
3. **Review** — Processing runs in the background and results stream in as each item completes. Edit any field inline. Use "Search All Prices" to auto-fill current market values via Google.
4. **Export** — Download a finished `.xlsx` spreadsheet ready to submit.

---

## Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com) (Claude vision)
- *(Optional)* Google Custom Search API key + Search Engine ID for price lookups

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/isaacallen73/Contents-Bot.git
cd Contents-Bot

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python app.py
```

The app opens automatically in your default browser at `http://localhost:5000`.

On first launch you'll be prompted for your Anthropic API key. Keys are stored locally in `config.json` (never committed).

---

## Configuration

All settings live in `config.json` (auto-created, git-ignored):

| Key | Description |
|---|---|
| `anthropic_api_key` | Required. Claude API key from console.anthropic.com |
| `model` | Claude model to use (default: `claude-haiku-4-5`) |
| `google_oauth_client_id` | Required for login. Google OAuth client ID |
| `google_oauth_client_secret` | Required for login. Google OAuth client secret |
| `google_search_api_key` | Optional. Enables "Search Prices" feature |
| `google_search_cx` | Optional. Google Custom Search Engine ID |

You can also change the model in the app's **Settings** screen (gear icon on home page). Haiku is recommended — it's significantly cheaper for high-volume jobs.

---

## Google OAuth Setup

The app requires a `@liberty-restoration.com` Google account to log in. One-time setup per machine:

1. Go to [Google Cloud Console](https://console.cloud.google.com) and create a project (or use an existing one).
2. Navigate to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**.
3. Set **Application type** to **Web application**.
4. Under **Authorized redirect URIs**, add: `http://localhost:5000/auth/callback`
5. Copy the **Client ID** and **Client Secret**.
6. Add them to `config.json`:
   ```json
   {
     "google_oauth_client_id": "...",
     "google_oauth_client_secret": "..."
   }
   ```

Users must sign in with a `@liberty-restoration.com` Google account. Anyone else is blocked.

### Usage Tracking

Every login, session, and export is logged to `usage/usage_log.jsonl`. To view a summary, visit `http://localhost:5000/api/usage` while logged in.

---

## Project Structure

```
Contents-Bot/
├── app.py                  # Flask server, API routes, SSE streaming
├── processor/
│   ├── claude_vision.py    # Claude API calls, vision prompt, item extraction
│   └── price_search.py     # Google Custom Search price lookup
├── templates/
│   └── index.html          # Single-page app shell
├── static/
│   ├── app.js              # All frontend logic
│   ├── style.css           # Styles
│   └── img/
│       └── logo.jpg        # Brand logo
├── sessions/               # Per-job data (git-ignored)
├── config.json             # API keys and settings (git-ignored)
└── requirements.txt
```

---

## Dependencies

```
flask
anthropic
openpyxl
pillow
requests
```

Install with `pip install -r requirements.txt`.

---

## AI Model & Prompt

Photos are sent to Claude (vision) with a prompt that:

- Identifies the **single primary item** in the photo
- Extracts: category, manufacturer, item name, model/serial, quantity, estimated price, and age
- Ignores hands, gloves, background surfaces, and incidental objects
- Returns structured JSON

The model and prompt are in `processor/claude_vision.py`. The default model is `claude-haiku-4-5` (fast, cost-effective). Switch to `claude-opus-4-6` in Settings for more accurate results on difficult items.

**Approximate cost:** ~$0.002–$0.005 per photo with Haiku. The Group screen shows an estimated total before you commit.

---

## Sessions

Each inventory job is saved as a session in `sessions/<uuid>/`:

```
sessions/
└── abc123.../
    ├── session.json    # metadata, status
    ├── groups.json     # photo groupings
    └── items.json      # extracted item data
```

Sessions persist between app restarts and appear on the home screen under "Recent Sessions."

---

## Known Limitations / Roadmap

- [ ] Delete / Split / Merge buttons on individual review rows
- [ ] "Flag bad analysis" button + feedback log for prompt refinement
- [ ] Standalone `.exe` via PyInstaller (no Python install required)
- [ ] OAuth login — restrict access to Liberty Restoration org members, track per-user usage
- [ ] README improvements as the app matures

---

## License

Internal tool — Liberty Restoration use only.
