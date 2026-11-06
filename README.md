# WebBrief AI

> **Production Release — v1.4**  
> An intelligent, multi-mode website summarizer powered by OpenAI and Flask. Converts any webpage—from standard blogs to JavaScript-rendered Single Page Applications—into structured, high-value briefs in seconds with Local Knowledge Retrieval & Semantic Search, Comparative Research, Notion Integration, Multi-URL Batch Summarization, PDF document export, and personalized prompt directives.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Features](#2-features)
3. [Vector Database Architecture & Evaluation](#3-vector-database-architecture--evaluation)
4. [Visual Previews](#4-visual-previews)
5. [Technology Stack](#5-technology-stack)
6. [System Architecture](#6-system-architecture)
7. [Repository Structure](#7-repository-structure)
8. [Installation Instructions](#8-installation-instructions)
9. [Virtual Environment Setup](#9-virtual-environment-setup)
10. [Dependency Installation](#10-dependency-installation)
11. [First-Run Setup Wizard](#11-first-run-setup-wizard)
12. [OpenAI API Key Configuration](#12-openai-api-key-configuration)
13. [BYOK Architecture & Security Model](#13-byok-architecture--security-model)
14. [Running Locally](#14-running-locally)
15. [Vercel & Cloud Deployment](#15-vercel--cloud-deployment)
16. [Environment Variables Reference](#16-environment-variables-reference)
17. [Troubleshooting](#17-troubleshooting)
18. [Known Limitations](#18-known-limitations)
19. [Future Roadmap](#19-future-roadmap)
20. [Contributing Guidelines](#20-contributing-guidelines)
21. [License](#21-license)

---

## 1. Project Overview

WebBrief AI simplifies reading long articles, technical papers, corporate releases, and dynamic websites by distilling raw web content into accurate, five-section structured summaries. 

Built with enterprise-grade SSRF defense and a **Bring Your Own Key (BYOK)** security architecture, WebBrief AI guarantees that personal API credentials remain private, stored locally or within cryptographically signed session cookies—never shared, never logged, and never exposed to client-side scripts.

---

## 2. Features

- **Local Knowledge Retrieval & Semantic Search (New in v1.4)**:
  - **Natural-Language Vector Search**: Query your personal summary library with questions (e.g. *"What are the ethical risks of AI training data discussed in my articles?"*) without relying on keyword matches.
  - **Embedded SQLite Vector Storage**: Vectors are packed as binary float blobs (`struct.pack`) directly in `webbrief_history.db`. Zero separate database servers, zero native C++ compiler requirements, and 100% ACID persistence across all application restarts.
  - **OpenAI `text-embedding-3-small` Integration**: 1536-dimensional semantic representations generated via your existing BYOK credentials at negligible cost ($0.00002 / 1,000 tokens).
  - **SHA-256 Content-Hash Caching**: Computes deterministic hashes of summary texts to guarantee that embeddings are generated exactly once per version—completely eliminating duplicate OpenAI API calls and billing.
  - **Exact Cosine Similarity Ranking**: Pure Python mathematical dot-product retrieval (< 0.05ms per comparison) with user-controlled relevance thresholds (default 35%) and result limits.
  - **Attributed Excerpt Extraction**: Surfaces the exact matching excerpt alongside match percentage pills (`● 94% Match`) and one-click direct summary navigation.
  - **Index Rebuilding & Maintenance**: Single-click "Rebuild Vector Index" in `/settings` to re-embed all historical documents, with automatic deletion cascading on summary removal.
- **Comparative Research & Dual-Source Synthesis (v1.3)**:
  - **Side-by-Side Dual URL Submission**: Input two independent website URLs to run deep comparative fact-checking and analysis.
  - **Shared Ground & Consensus Points**: Automatically isolates common arguments, shared premises, and mutual points of consensus.
  - **Contradictions & Disagreements**: Identifies and highlights direct factual conflicts, opposing claims, or incompatible conclusions with alert callouts.
  - **Differing Perspectives & Editorial Framing**: Outlines variations in tone, framing, target audience, and analytical methodology.
  - **Comparative Claims Table**: Structured topic-by-topic comparison mapping Source A's claims directly against Source B's claims.
  - **Unique Information**: Flags critical facts and context that appear exclusively in one article and are omitted by the other.
  - **Conclusions Comparison & Synthesis**: Objective comparative assessment evaluating divergence or alignment without inventing unverified facts.
  - **Comparative PDF Export**: Download dedicated, multi-page ReportLab comparative briefs with custom headers, tables, and conflict callouts.
  - **Dual-URL History Tracking**: Saves comparative entries to SQLite history, tagged with `⚖️ Comparison` badges.
- **Notion Integration & Export (v1.3)**:
  - **Official Notion REST API Integration**: Connects via Notion Internal Integration Secrets (`secret_...` or `ntn_...`).
  - **BYOK Credential Isolation**: Masked token storage, zero logging, and instant disconnect capability in Settings.
  - **Structured Block Formatting**: Exports both individual and comparative summaries into native Notion blocks (callouts, dividers, headings, bullet lists, bold text, and hyperlinks).
  - **Destination Page Picker**: Automatically queries accessible shared parent pages via Notion's Search API, with support for direct Page ID entry.
- **Multi-URL Batch Summarization (v1.2)**:
  - **Dynamic Input Rows**: Tab-based switcher between Single, Batch, Compare, and Search modes. Manage up to 10 URLs per batch with real-time counters.
  - **Pre-Flight Validation & Duplicate Detection**: Validates each URL independently, displays field-specific error hints, and flags duplicate URLs before submission.
  - **Isolated Concurrency Engine**: Runs concurrent processing using `ThreadPoolExecutor` (bounded to 2–3 workers). Scraping or OpenAI API failures on one URL never terminate or impact the remaining batch.
  - **Real-Time Progress Tracking**: Visual progress bar, percentage completion, counter (`X / Y Completed`), status pills (Total, In Progress, Completed, Failed), and active URL status text.
  - **Interactive Accordion Results**: Organized expandable/collapsible cards for each website with title, source link, mode pill, and execution status (`✓ Completed` or `✕ Failed`).
  - **Export All as PDF & Single Downloads**: One-click combined multi-article PDF export compiling all batch briefs into a single numbered report, or individual PDF downloads per card.
- **Bring Your Own Key (BYOK) Security**:
  - Zero developer key sharing. Every user configures their own OpenAI credentials.
  - Minimal zero-token model verification (`models.list()`) validates keys before persisting.
  - Safe local `.env` writing with automatic overwrite protection.
  - Cryptographically signed, `HttpOnly`, `SameSite=Lax` cookie fallback for serverless deployments.
- **Dual-Engine Scraping with Dynamic Fallback**:
  - **Static Engine**: Fast HTTP requests via `requests` + `BeautifulSoup4` with boilerplate element decomposition.
  - **Dynamic Fallback**: Automatic Playwright headless Chromium invocation for JavaScript-rendered SPAs (React, Vue, Angular).
- **Four Distinct Summarization Modes**: Quick, Detailed, Bullet-Point, and Technical.
- **Hierarchical Map-Reduce for Long Articles**: Natural chunking with token-safe overlap and multi-pass synthesis.
- **Professional PDF Document Export (v1.1)**: ReportLab multi-page briefs with running headers and `NumberedCanvas` page counts.
- **Custom Prompt Directives (v1.1)**: Guiding instructions persisted in SQLite or browser `localStorage`.
- **Summary History & SQLite Storage**: Full search, reopen, export, and delete capabilities.

---

## 3. Vector Database Architecture & Evaluation

In designing WebBrief AI v1.4's semantic retrieval layer, multiple vector database solutions were formally evaluated:

| Architecture | Dependencies & Footprint | Portability (Windows / Serverless) | Persistence & Recovery | Recommendation |
| :--- | :--- | :--- | :--- | :--- |
| **ChromaDB** | Heavy (>200MB, onnxruntime, tokenizers, posthog, hnswlib) | High failure rate in serverless runtimes & Windows without Visual C++ build tools | Separate local directory; prone to SQLite >= 3.35 version lockouts | ❌ Overkill for personal summary collections |
| **FAISS (faiss-cpu)** | Medium (>40MB binary wheel) | Prebuilt wheel mismatches on Windows; separate index serialization synchronization | Stored as separate binary file out of sync with relational SQLite database | ❌ High installation friction |
| **SQLite + Binary Float Vectors (Chosen)** | **Zero additional dependencies** (standard Python `struct` + `sqlite3`) | **100% universal compatibility** across Windows, macOS, Linux, and Vercel | Single database file (`webbrief_history.db`); 100% ACID transactional consistency | ✅ **Optimal for WebBrief AI** |

### Cosine Similarity & Model Cost:
- **Embedding Model**: OpenAI `text-embedding-3-small` (1536 dimensions).
- **Storage Cost**: ~6 KB per summary packed into compact binary blobs.
- **Execution Speed**: Pure Python dot product executes in **~0.03ms** per vector comparison (searching 500 summaries takes ~15ms).
- **API Cost**: $0.00002 per 1,000 tokens (~50,000 pages per $1.00). Content-hash caching guarantees that documents are never redundantly re-embedded.

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
├── batch_processor.py      # Multi-URL batch processor, concurrency control, progress callback
├── byok.py                 # Key validation, safe .env persistence, and serverless logic
├── chunker.py              # Paragraph-aware chunking and token overlap management
├── comparative_analyzer.py # Dual-source comparative research, claims matrix & fact synthesis
├── history.py              # Local SQLite summary & comparison persistence layer (CRUD)
├── notion_service.py       # Official Notion REST API client, block parser & page exporter
├── pdf_exporter.py         # ReportLab multi-page PDF generation engine with NumberedCanvas
├── scraper.py              # Dual static/dynamic scraper, SSRF firewall, Playwright engine
├── summarizer.py           # Multi-mode prompt engine, Map-Reduce pipeline, JSON parser
├── vector_store.py         # Local SQLite vector database engine, cosine search & hash cache
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
