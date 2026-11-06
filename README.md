# WebBrief AI

> **Production Release — v1.1**  
> An intelligent, multi-mode website summarizer powered by OpenAI and Flask. Converts any webpage—from standard blogs to JavaScript-rendered Single Page Applications—into structured, high-value briefs in seconds with PDF document export and personalized prompt directives.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Features](#2-features)
3. [Visual Previews](#3-visual-previews)
4. [Technology Stack](#4-technology-stack)
5. [System Architecture](#5-system-architecture)
6. [Repository Structure](#6-repository-structure)
7. [Installation Instructions](#7-installation-instructions)
8. [Virtual Environment Setup](#8-virtual-environment-setup)
9. [Dependency Installation](#9-dependency-installation)
10. [First-Run Setup Wizard](#10-first-run-setup-wizard)
11. [OpenAI API Key Configuration](#11-openai-api-key-configuration)
12. [BYOK Architecture & Security Model](#12-byok-architecture--security-model)
13. [Running Locally](#13-running-locally)
14. [Vercel & Cloud Deployment](#14-vercel--cloud-deployment)
15. [Environment Variables Reference](#15-environment-variables-reference)
16. [Troubleshooting](#16-troubleshooting)
17. [Known Limitations](#17-known-limitations)
18. [Future Roadmap](#18-future-roadmap)
19. [Contributing Guidelines](#19-contributing-guidelines)
20. [License](#20-license)

---

## 1. Project Overview

WebBrief AI simplifies reading long articles, technical papers, corporate releases, and dynamic websites by distilling raw web content into accurate, five-section structured summaries. 

Built with enterprise-grade SSRF defense and a **Bring Your Own Key (BYOK)** security architecture, WebBrief AI guarantees that personal API credentials remain private, stored locally or within cryptographically signed session cookies—never shared, never logged, and never exposed to client-side scripts.

---

## 2. Features

- **Bring Your Own Key (BYOK) Security**:
  - Zero developer key sharing. Every user configures their own OpenAI credentials.
  - Minimal zero-token model verification (`models.list()`) validates keys before persisting.
  - Safe local `.env` writing with automatic overwrite protection.
  - Cryptographically signed, `HttpOnly`, `SameSite=Lax` cookie fallback for serverless deployments.
  - Passwords masked by default with an interactive reveal toggle.
- **Dual-Engine Scraping with Dynamic Fallback**:
  - **Static Engine**: Fast HTTP requests via `requests` + `BeautifulSoup4` with boilerplate element decomposition (`<nav>`, `<footer>`, `<aside>`, `<script>`, `<style>`).
  - **Dynamic Fallback**: Automatic Playwright headless Chromium invocation for JavaScript-rendered SPAs (React, Vue, Angular) whenever static extraction yields inadequate content.
  - **Asset Optimization**: Blocks media, images, stylesheets, and custom fonts during browser rendering to minimize bandwidth and rendering latency.
- **Enterprise-Grade SSRF Protection**:
  - Rejects loopback (`127.0.0.1`, `localhost`), private LAN subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local IPs, IPv6 loopbacks, and cloud metadata services (`169.254.169.254`).
  - Re-evaluates redirect histories and final destination addresses against DNS resolution checks before dispatching requests.
- **Four Distinct Summarization Modes**:
  - **Quick Summary**: High-level overview and primary takeaways in under 3 sentences.
  - **Detailed Summary**: Deep context, background, mechanisms, and nuanced supporting details.
  - **Bullet-Point Summary**: High-density scannable takeaways formatted purely as clean bullet points.
  - **Technical Summary**: Strictly preserves domain terminology, specifications, constraints, and architecture parameters.
- **Hierarchical Map-Reduce for Long Articles**:
  - Natural paragraph-aware chunking with token-safe sliding overlap.
  - Intermediate map reduction merges multi-chunk content into a single cohesive structured response without losing nuance.
- **Professional PDF Document Export (New in v1.1)**:
  - Generates downloadable, beautifully styled multi-page PDF summary briefs via ReportLab.
  - Formatted document header with website title, source URL (clickable hyperlinks), mode badge, and timestamp.
  - Clean typographic hierarchy for Overview, Key Points, Important Details, and Conclusion.
  - Two-pass `NumberedCanvas` engine for automatic "Page X of Y" footers and running headers.
  - Robust XML escaping and Unicode text handling (accents, symbols, smart quotes).
- **Custom Prompt Directives & Personalization (New in v1.1)**:
  - Dedicated custom instructions field in `/settings` allowing users to guide summary emphasis (e.g., pricing, API security, technical tradeoffs).
  - Harmonious integration: Combines user guidance with Quick, Detailed, Bullet-Point, and Technical modes without overriding core factuality or JSON schema rules.
  - Length validation and input sanitization (up to 1,000 characters).
  - Quick enable/disable toggle switch and instant reset to defaults.
  - Dual persistence model: SQLite preferences table for self-hosted instances; `localStorage` sync for hosted/serverless platforms.
- **Summary History & SQLite Storage**:
  - Local database (`webbrief_history.db`) stores title, URL, mode, formatted timestamp, and full structured JSON data.
  - Zero-reload summary reopening directly into the dashboard without re-scraping.
  - Single-entry deletion and full history purging.
- **Modern User Experience**:
  - Dark and Light themes with instant toggle and anti-FOUC initialization script.
  - Section-level copy buttons ("Copied!" visual feedback) and complete Markdown brief export.
  - Dedicated `/settings` dashboard for managing mode preferences, theme, and API credentials.
  - Responsive, fluid interface across desktop, tablet, and mobile.

---

## 3. Visual Previews

### Primary Interface & Mode Selector
```
+-------------------------------------------------------------------------+
| [O] WebBrief AI  v1.0             [History]  [Settings]  [* Mode: Dark]  |
| Turn any webpage into an accurate, structured AI summary in seconds.    |
+-------------------------------------------------------------------------+
| Webpage URL: [ https://example.com/deep-dive-article                 ]  |
|                                                                         |
| Summarization Mode:                                                     |
| [⚡ Quick]       [📖 Detailed]     [📋 Bullet-Point]   [⚙️ Technical]    |
|                                                                         |
| [> Bring Your Own Key (Custom Override)]                                |
|                                                                         |
| [               Summarize Webpage (Playwright + OpenAI)               ] |
+-------------------------------------------------------------------------+
```

### Structured Output Cards
```
+-------------------------------------------------------------------------+
|  ✦ Quantum Computing Breakthrough                                      |
|  [Detailed Summary]  [Structured Brief]  Source: https://example.com...  |
|                                                                         |
|  [Copy Full Markdown Summary]  [+ New Summary]                          |
|                                                                         |
|  ## Overview                                               [Copy Section]|
|  Researchers achieve fault-tolerant logical qubits with neutral atoms... |
|                                                                         |
|  ## Key Points                                            [Copy Section]|
|  • 48 logical qubits sustained over multi-step quantum circuits.        |
|  • Two-qubit gate fidelities exceeded 99.5% across the array.           |
|                                                                         |
|  ## Important Details                                     [Copy Section]|
|  • Coherence times were extended by a factor of 4x using dual lasers.   |
|  • Compatible with existing optical tweezers control systems.           |
|                                                                         |
|  ## Conclusion                                            [Copy Section]|
|  Marks a transition from noisy intermediate-scale to fault tolerance.   |
+-------------------------------------------------------------------------+
```

---

## 4. Technology Stack

- **Backend Runtime**: Python 3.10+
- **Web Framework**: Flask 3.1+ (Jinja2 templates, signed `HttpOnly` sessions)
- **AI Engine**: OpenAI Python SDK (`gpt-5.6-luna` / modern completions with JSON enforcement)
- **Web Scraping**:
  - Static: `requests` + `BeautifulSoup4`
  - Dynamic: `playwright` (Chromium headless with request routing)
- **Persistence**: SQLite3 (native standard library) with in-memory session fallback
- **Frontend**: Vanilla JavaScript (ES6+), CSS Custom Properties (zero heavy frontend frameworks)
- **Production Server**: Gunicorn WSGI (Linux/Docker) or Vercel Python runtime

---

## 5. System Architecture

```
                    [ Incoming User Request ]
                                |
                                v
                   +--------------------------+
                   | Flask Routing & Security |
                   |  - Enforce BYOK Filter   |
                   |  - CSRF/Session Security |
                   +--------------------------+
                                |
               +----------------+----------------+
               |                                 |
         [ Static Path ]                  [ Dynamic Path ]
               v                                 v
      +-----------------+              +--------------------+
      |  Requests GET   |              | Playwright Headless|
      | - 15s Timeout   |              | - Headless Chrome  |
      | - SSRF & Redirect              | - Abort Assets     |
      |   Validation    |              | - SSRF Interceptor |
      +-----------------+              +--------------------+
               |                                 |
               +----------------+----------------+
                                |
                                v
                   +--------------------------+
                   | HTML Cleaning & Extract  |
                   | - Strip Scripts & Styles |
                   | - Semantic Containers    |
                   +--------------------------+
                                |
             [ Character Count > 12,000 chars? ]
                    /                  \
                (Yes)                  (No)
                  /                      \
                 v                        v
      +---------------------+   +--------------------+
      | Chunking Pipeline   |   | Single-Pass Engine |
      | - Map Phase         |   | - JSON Directive   |
      | - Reduce Phase      |   | - Mode Prompting   |
      +---------------------+   +--------------------+
                 \                        /
                  \                      /
                   v                    v
                 +------------------------+
                 | Structured JSON Brief  |
                 | - Schema Validation    |
                 | - Auto-Save to SQLite  |
                 +------------------------+
```

---

## 6. Repository Structure

```
WebBrief AI/
├── api/
│   └── index.py            # Vercel serverless function entrypoint bridge
├── app.py                  # Core Flask application, routing, and controller logic
├── byok.py                 # Key validation, safe .env persistence, and serverless logic
├── chunker.py              # Paragraph-aware chunking and token overlap management
├── history.py              # Local SQLite summary persistence layer (CRUD)
├── pdf_exporter.py         # ReportLab multi-page PDF generation engine with NumberedCanvas
├── scraper.py              # Dual static/dynamic scraper, SSRF firewall, Playwright engine
├── summarizer.py           # Multi-mode prompt engine, Map-Reduce pipeline, JSON parser
├── static/
│   └── style.css           # Responsive stylesheet with light/dark theme variables
├── templates/
│   ├── index.html          # Primary application dashboard, mode selector, and result cards
│   ├── history.html        # Dedicated summary library view (search, reopen, delete)
│   ├── result.html         # Standalone result view with mode badging
│   ├── setup.html          # First-run BYOK onboarding wizard & credential updater
│   └── settings.html       # Application settings, theme manager, and system status
├── .env.example            # Environment variable template
├── .gitignore              # Git ignore rules (protects credentials and local DBs)
├── requirements.txt        # Production Python dependencies (includes Gunicorn & Playwright)
├── vercel.json             # Vercel Serverless deployment configuration
└── README.md               # Comprehensive documentation
```

---

## 7. Installation Instructions

### Prerequisites
- Python 3.10, 3.11, or 3.12 installed on your machine.
- Git installed.
- An active OpenAI API key.

### Clone the Repository
```bash
git clone https://github.com/your-username/webbrief-ai.git
cd webbrief-ai
```

---

## 8. Virtual Environment Setup

### macOS / Linux
```bash
python3 -m venv venv
source venv/bin/activate
```

### Windows (PowerShell)
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### Windows (Command Prompt)
```cmd
python -m venv venv
.\venv\Scripts\activate.bat
```

---

## 9. Dependency Installation

Install all required Python libraries:
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

*(Optional but recommended)*: Install Playwright's Chromium browser binary for JavaScript-heavy website fallback:
```bash
playwright install chromium
```
*Note: If Playwright binaries are omitted, WebBrief AI continues running using its static scraping engine and will notify you cleanly if a site strictly requires JavaScript rendering.*

---

## 10. First-Run Setup Wizard

1. Start the Flask development server:
   ```bash
   python app.py
   ```
2. Navigate to `http://127.0.0.1:5000` in your web browser.
3. If no `OPENAI_API_KEY` is detected in your environment, WebBrief AI automatically redirects you to the **Setup Wizard** at `/setup`.
4. Enter your personal OpenAI API key (format: `sk-...`).
5. WebBrief AI executes a zero-token test request against OpenAI to verify authentication.
6. Upon validation, your key is safely saved into a local `.env` file and you are returned to the main dashboard.

---

## 11. OpenAI API Key Configuration

You can configure your API key in any of three ways:

1. **Interactive Setup Wizard**: Guided configuration on first launch (`/setup`).
2. **Pre-Configured Environment File**: Copy `.env.example` to `.env` and insert your key:
   ```bash
   cp .env.example .env
   ```
   Edit `.env`:
   ```ini
   OPENAI_API_KEY=sk-your-actual-api-key-here
   ```
3. **Shell Environment Variable**:
   ```bash
   export OPENAI_API_KEY="sk-your-actual-api-key-here"
   ```

---

## 12. BYOK Architecture & Security Model

WebBrief AI strictly enforces a **Zero-Credential Exposure** architecture:

- **Isolated Storage**: Saved keys reside exclusively in your local `.env` or in cryptographically signed, encrypted session cookies (`HttpOnly`, `SameSite=Lax`).
- **No Client Leakage**: API keys are never rendered in frontend templates, never injected into HTML attributes, and never transmitted in client JSON payloads.
- **Overwrite Confirmation**: Replacing an existing `.env` configuration requires explicit user confirmation to prevent accidental overwrites.
- **SSRF Defense**:
  - DNS pre-resolution restricts requests to public IP addresses.
  - Re-evaluates destination URLs across 301/302 redirects.
  - Blocks loopback (`127.0.0.1`), metadata endpoints (`169.254.169.254`), and private RFC 1918 subnets.
- **Strict Error Sanitization**: API errors returned from OpenAI or scrapers are mapped to friendly messages without exposing credentials or internal call traces.

---

## 13. Running Locally

### Development Mode
```bash
python app.py
```
Visit `http://127.0.0.1:5000` in your browser.

### Production Mode (Gunicorn - Linux / macOS)
```bash
gunicorn --bind 127.0.0.1:5000 --workers 4 app:app
```

### Production Mode (Waitress - Windows)
```bash
pip install waitress
waitress-serve --listen=127.0.0.1:5000 app:app
```

---

## 14. Vercel & Cloud Deployment

WebBrief AI is configured for one-click deployment on **Vercel Serverless Functions**.

### 1. Deploy via Vercel CLI
```bash
npm install -g vercel
vercel login
vercel
```

### 2. Deploy via GitHub Integration
1. Push your repository to GitHub.
2. In the Vercel Dashboard, click **Add New Project** and select your repository.
3. Vercel automatically detects `vercel.json` and `api/index.py`.
4. Under **Environment Variables**, configure:
   - `FLASK_SECRET_KEY`: A random 32-character secret string.
   - `SESSION_COOKIE_SECURE`: `true`
5. Click **Deploy**.

### Serverless Architecture Notes
- In serverless environments, users enter their personal API keys via the onboarding wizard.
- Keys are preserved in secure session cookies for the duration of their browsing session rather than written to the ephemeral serverless filesystem.
- Headless Chromium browser automation is optimized for local/containerized deployments; on standard serverless tiers without custom binary layers, WebBrief AI uses its static extraction engine.

---

## 15. Environment Variables Reference

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `OPENAI_API_KEY` | Optional* | None | OpenAI API key (*prompted in wizard if missing) |
| `FLASK_SECRET_KEY` | Recommended | Random Fallback | Secret key used to sign session cookies |
| `FLASK_DEBUG` | Optional | `0` | Set `1` to enable Flask debugger (development only) |
| `PORT` | Optional | `5000` | Local web server listening port |
| `HOST` | Optional | `127.0.0.1` | Local web server binding host |
| `SESSION_COOKIE_SECURE` | Optional | `false` | Set `true` in production when serving over HTTPS |

---

## 16. Troubleshooting

### Issue: "OpenAI API key not configured"
- **Solution**: Visit `/setup` and submit a valid OpenAI API key, or check that your `.env` contains `OPENAI_API_KEY=sk-...`.

### Issue: "Access to internal, loopback, or private addresses is restricted"
- **Solution**: WebBrief AI blocks requests to `localhost`, `127.0.0.1`, and private IP ranges to prevent SSRF vulnerabilities. Ensure you supply public internet URLs.

### Issue: "Dynamic browser rendering is not available on this server"
- **Solution**: Run `playwright install chromium` in your virtual environment to download the required headless browser binaries.

### Issue: "Website returned an error (HTTP 403 / 401)"
- **Solution**: Some websites restrict automated web scrapers. WebBrief AI automatically attempts browser emulation, but sites with aggressive anti-bot captchas (Cloudflare Under Attack mode) may block scraping.

---

## 17. Known Limitations

- **Aggressive Cloudflare / CAPTCHA Walls**: Websites that require solving interactive CAPTCHAs cannot be scraped automatically.
- **Paywalled Content**: WebBrief AI extracts only publicly visible content and does not bypass paywalls or logins.
- **Serverless Browser Binaries**: Vercel Serverless Functions have file size limits that prevent bundling full Chromium binaries without external container layers.

---

## 18. Future Roadmap

- [ ] Export summaries to PDF and Notion.
- [ ] Multi-URL batch summarization.
- [ ] Comparative summary generation (compare two articles side-by-side).
- [ ] User-defined custom prompt directives in Settings.
- [ ] Local vector embedding search across saved summary history.

---

## 19. Contributing Guidelines

Contributions are welcome! Please follow these steps:

1. **Fork the Repository**.
2. **Create a Feature Branch**: `git checkout -b feature/amazing-feature`.
3. **Write Tests**: Ensure any changes include accompanying tests in `tests/`.
4. **Run Test Suites**:
   ```bash
   python -m unittest discover -s scratch -p "test_*.py"
   ```
5. **Commit Your Changes**: Follow conventional commits (e.g., `feat: ...`, `fix: ...`).
6. **Open a Pull Request**.

---

## 20. License

Distributed under the **MIT License**. See `LICENSE` for more information.

---

*(c) 2026 WebBrief AI — Built with security, performance, and transparency.*
