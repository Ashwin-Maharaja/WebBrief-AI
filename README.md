# WebBrief AI

An AI-powered website summarizer that extracts readable content from any webpage (including JavaScript-rendered single page applications) and generates concise, high-value structured summaries for both short and long-form content across multiple summarization modes.

## Version
**v0.9 — BYOK Onboarding & Security**

## Features in v0.9
- **Secure Bring Your Own Key (BYOK) Onboarding**:
  - **First-Run Setup Wizard (`/setup`)**: Automatically detects whether an active OpenAI API key is configured. If not, seamlessly redirects visitors to a dedicated onboarding wizard.
  - **Zero-Token Key Validation**: Verifies credentials against OpenAI's authentication server using a minimal 0-token verification call (`client.models.list()`) before saving.
  - **Password Masking & Visibility Toggle**: API keys are masked by default (`type="password"`) with an interactive Show/Hide toggle.
  - **Sanitized Error Messaging**: Friendly user feedback for invalid keys, billing/quota limitations, network timeouts, and permissions without echoing the submitted key or leaking credentials.
  - **Protected Local Persistence**: Safely writes validated keys to a local `.env` file (`OPENAI_API_KEY`) with restricted file permissions (`chmod 600` on POSIX systems).
  - **Overwrite Protection**: Detects existing `.env` files and requires explicit user confirmation before replacing active credentials.
- **Dedicated Key Management (`/settings`)**:
  - Displays real-time configuration status, masked credential indicator (`••••••••••••••••`), and active storage mechanism.
  - Provides options to update the API key or safely clear saved credentials.
  - Never displays previously saved keys in cleartext and never transmits credentials to frontend JavaScript.
- **Local & Serverless Deployment Compatibility**:
  - **Local / Self-Hosted**: Automatically creates and persists `.env` configurations across application restarts with zero third-party auth requirements (no Google OAuth required).
  - **Hosted / Serverless (Vercel, AWS Lambda, Netlify)**: Gracefully detects read-only or ephemeral filesystems and maintains session-level BYOK using cryptographically signed, `HttpOnly`, `SameSite=Lax` cookies.
- **Dynamic JavaScript Scraping Fallback (from v0.8)**: Hybrid static requests + Playwright headless Chromium fallback for SPA/client-rendered websites with asset blocking (images, fonts, stylesheets, media).
- **Enterprise SSRF Protection (from v0.8)**: Blocks `localhost`, private network IP ranges, cloud metadata endpoints (`169.254.169.254`), non-HTTP schemes, and unverified redirects.
- **Multiple Summarization Modes (from v0.7)**: Quick, Detailed, Bullet-Point, and Technical modes.
- **Chunking & Map-Reduce Pipeline (from v0.6)**: Paragraph-aware chunking and hierarchical intermediate reduction for long-form content.
- **Structured 5-Section Architecture (from v0.5)**: Consistent format across all modes (Title, Overview, Key Points, Important Details, Conclusion).

## Project Structure
```
WebBrief AI/
├── app.py              # Flask application routes, BYOK onboarding enforcement, and coordination
├── byok.py             # Key validation, safe .env persistence, and serverless compatibility
├── chunker.py          # Paragraph-aware chunking and boundary management
├── scraper.py          # Dual static/dynamic scraper, SSRF protection, and Playwright fallback
├── summarizer.py       # Multi-mode prompt engine, Map-Reduce pipeline, and schema validation
├── static/
│   └── style.css       # Responsive styling, mode cards, section styling, animations
├── templates/
│   ├── index.html      # Primary interactive web interface with mode selector
│   ├── result.html     # Structured summary view template with mode badging
│   ├── setup.html      # First-run BYOK onboarding wizard & key update modal
│   └── settings.html   # Key management, configuration status, and credential reset
├── .env.example        # Environment variable template
├── .gitignore          # Git ignore configuration
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
