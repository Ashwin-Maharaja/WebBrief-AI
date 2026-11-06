# WebBrief AI

An AI-powered website summarizer that extracts readable content from any webpage (including JavaScript-rendered single page applications) and generates concise, high-value structured summaries for both short and long-form content across multiple summarization modes.

## Version
**v0.10 — UX Improvements & Application Settings**

## Features in v0.10
- **Summary History & Persistence (`/history`)**:
  - **Local SQLite Storage**: Saves generated summaries into a lightweight local SQLite database (`webbrief_history.db`), automatically git-ignored and self-contained with in-memory fallback for serverless environments.
  - **Rich History Entries**: Stores website title, source URL, formatted timestamp, summarization mode & display name, and full structured JSON data.
  - **Zero-Reload Reopen**: Reopen any previous summary instantaneously into the primary dashboard with full structured cards without re-scraping or invoking AI APIs.
  - **Entry Management**: Delete individual entries or purge entire history with confirmation modals.
  - **Recent Summaries Shelf**: Quick-access shelf on the homepage displaying recent summaries.
- **Dark & Light Theme Management**:
  - **Dual Theme Engine**: High-contrast, meticulously designed light and dark themes styled with CSS custom properties.
  - **FOUC Prevention**: Inline `<head>` initialization script reads preference from `localStorage` (`webbrief_theme`) before DOM render, preventing theme flickering.
  - **Theme Toggle**: Accessible switch available in the top navigation header and application settings page.
- **Enhanced Summary Interactions**:
  - **Full Markdown Copy**: One-click button to copy complete structured summaries formatted with Markdown headings and bullet points.
  - **Section-Level Copying**: Dedicated copy buttons on every section (Overview, Key Points, Important Details, Conclusion) with visual "Copied!" feedback.
  - **Quick Workflow**: Convenient "+ New Summary" button resets input and scrolls smoothly to focus on the URL bar.
- **Dedicated Application Settings (`/settings`)**:
  - **AI Provider**: Displays active AI provider (OpenAI).
  - **API Key Management**: Displays BYOK configuration status and allows updating or resetting API credentials.
  - **Default Summarization Mode**: Persists preferred summarization mode (Quick, Detailed, Bullet-Point, Technical).
  - **Theme Selector**: Visual theme selector with live switching.
  - **History Dashboard**: Live count of saved summaries and one-click clear history tool.
  - **App Information**: Version details (`v0.10`) and platform specs.
- **BYOK Security (from v0.9)**:
  - First-run setup wizard (`/setup`), zero-token key verification (`models.list()`), password masking with toggle, safe `.env` write with overwrite confirmation, and cookie fallback on serverless.
- **Dynamic JavaScript Scraping Fallback (from v0.8)**: Hybrid static requests + Playwright headless Chromium fallback for SPA/client-rendered websites with asset blocking (images, fonts, stylesheets, media).
- **Enterprise SSRF Protection (from v0.8)**: Blocks `localhost`, private network IP ranges, cloud metadata endpoints (`169.254.169.254`), non-HTTP schemes, and unverified redirects.
- **Multiple Summarization Modes (from v0.7)**: Quick, Detailed, Bullet-Point, and Technical modes.
- **Chunking & Map-Reduce Pipeline (from v0.6)**: Paragraph-aware chunking and hierarchical intermediate reduction for long-form content.
- **Structured 5-Section Architecture (from v0.5)**: Consistent format across all modes (Title, Overview, Key Points, Important Details, Conclusion).

## Project Structure
```
WebBrief AI/
├── app.py              # Flask application routes, BYOK onboarding enforcement, history endpoints, and coordination
├── byok.py             # Key validation, safe .env persistence, and serverless compatibility
├── chunker.py          # Paragraph-aware chunking and boundary management
├── history.py          # SQLite summary history persistence layer (CRUD operations)
├── scraper.py          # Dual static/dynamic scraper, SSRF protection, and Playwright fallback
├── summarizer.py       # Multi-mode prompt engine, Map-Reduce pipeline, and schema validation
├── static/
│   └── style.css       # Responsive styling, light/dark themes, animations, mode cards
├── templates/
│   ├── index.html      # Primary interactive web interface with mode selector, recent shelf, and result cards
│   ├── history.html    # Dedicated summary history view with search, reopen, and delete
│   ├── result.html     # Structured summary view template with mode badging
│   ├── setup.html      # First-run BYOK onboarding wizard & key update modal
│   └── settings.html   # Application settings: theme, default mode, key management, history stats
├── .env.example        # Environment variable template
├── .gitignore          # Git ignore configuration (includes .env, *.db)
├── requirements.txt    # Lean dependencies including Playwright
└── README.md           # Documentation
```

## Getting Started

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```
*(Dependencies: Flask, requests, beautifulsoup4, openai, python-dotenv, playwright)*

### 2. Install Browser Binaries (for Dynamic Rendering Fallback)
```bash
playwright install chromium
```

### 3. Run the Application
```bash
python app.py
```
Visit `http://127.0.0.1:5000` in your web browser.
- On your first run, WebBrief AI will automatically launch the **First-Run Setup Wizard** at `/setup`.
- Enter your personal OpenAI API key to validate and save your configuration.
- Alternatively, you can pre-configure credentials by copying `.env.example` to `.env`:
```bash
OPENAI_API_KEY=your_openai_api_key_here
```
