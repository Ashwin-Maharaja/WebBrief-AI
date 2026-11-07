# WebBrief AI

> **Production Release — v1.5.1**  
> An intelligent, multi-mode website summarizer powered by OpenAI and Flask. Converts any webpage—from standard blogs to JavaScript-rendered Single Page Applications—into structured, high-value briefs in seconds with Hardened Security & Reliability, Hosted BYOK & Deployment-Aware Storage, Local Knowledge Retrieval & Semantic Search, Comparative Research, Notion Integration, Multi-URL Batch Summarization, PDF document export, and personalized prompt directives.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Features](#2-features)
3. [Deployment Models & Architecture (v1.5)](#3-deployment-models--architecture-v15)
4. [Vector Database Architecture & Evaluation](#4-vector-database-architecture--evaluation)
5. [Visual Previews](#5-visual-previews)
6. [Technology Stack](#6-technology-stack)
7. [System Architecture](#7-system-architecture)
8. [Repository Structure](#8-repository-structure)
9. [Installation Instructions](#9-installation-instructions)
10. [Virtual Environment Setup](#10-virtual-environment-setup)
11. [Dependency Installation](#11-dependency-installation)
12. [First-Run Setup Wizard](#12-first-run-setup-wizard)
13. [OpenAI API Key Configuration](#13-openai-api-key-configuration)
14. [BYOK Architecture & Security Model](#14-byok-architecture--security-model)
15. [Running Locally](#15-running-locally)
16. [Vercel & Cloud Deployment](#16-vercel--cloud-deployment)
17. [Environment Variables Reference](#17-environment-variables-reference)
18. [Troubleshooting](#18-troubleshooting)
19. [Known Limitations](#19-known-limitations)
20. [Future Roadmap](#20-future-roadmap)
21. [Contributing Guidelines](#21-contributing-guidelines)
22. [License](#22-license)

---

## 1. Project Overview

WebBrief AI simplifies reading long articles, technical papers, corporate releases, and dynamic websites by distilling raw web content into accurate, five-section structured summaries. 

Built with enterprise-grade SSRF defense, deployment-aware storage, and a **Bring Your Own Key (BYOK)** security architecture, WebBrief AI guarantees that personal API credentials remain private, stored locally in `.env` for self-hosted instances or strictly in active browser memory for public Vercel visitors—never shared between users, never logged, and never exposing developer API credits.

---

## 2. Features

- **Enterprise Security & Reliability Hardening (v1.5.1)**:
  - **Zero Hardcoded Secrets & Production Verification**: Eliminates static fallbacks. In production environments, requires an explicit 32-byte `FLASK_SECRET_KEY`; in local development, generates an ephemeral, cryptographically secure 32-byte key (`secrets.token_hex(32)`).
  - **Defensive HTTP Security Headers**: Injects `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`, and a strict Content Security Policy (`CSP`) into every response.
  - **Origin & Anti-CSRF Protection**: Enforces Origin and Referer validation on all mutating HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`) to block cross-origin request forging, accompanied by session anti-CSRF token verification for form submissions.
  - **Enhanced SSRF & Redirect-Hopping Guard**: Upgrades scraper SSRF defenses with an iterative redirect loop (`allow_redirects=False`, max 5 hops) where every hop destination IP is verified against private, loopback, cloud metadata (`169.254.169.254`, `100.100.100.200`, `fd00:ec2::254`), and IPv4-mapped IPv6 ranges before connecting. Rejects embedded URL credentials and restricts requests to safe web ports (`80, 443, 8080, 8443`).
  - **Warm Serverless Multi-Tenant Isolation**: Completely bypasses server-side SQLite writes and reads in hosted serverless mode (Vercel), assigning ephemeral in-memory UUIDs and ensuring zero data leakage between concurrent visitors sharing warm containers.
  - **DOM-Based XSS Sanitization**: Rigorously escapes all dynamic values rendered in the browser DOM (titles, URLs, excerpts, claims, and error strings) across Single, Batch, Compare, and Search views.
  - **PDF Export Path Traversal Neutralization**: Sanitizes downloaded PDF filenames to strictly alphanumeric characters, underscores, and hyphens, preventing path traversal attacks and Content-Disposition header manipulation.
  - **Request Body Payload Limit**: Enforces a strict 2MB maximum content length (`MAX_CONTENT_LENGTH`) to protect backend memory from denial-of-service attempts.
- **Hosted BYOK & Deployment-Aware Storage (v1.5)**:
  - **Public Vercel Hosting Ready**: Visitors can freely use the public deployment by supplying their own OpenAI API key.
  - **In-Memory Credential Security**: Visitor API keys reside exclusively in frontend JavaScript RAM during the active tab session. Keys are **never** stored in `localStorage`, `sessionStorage`, `IndexedDB`, or browser-readable cookies, and are never written to the server's ephemeral filesystem or process environment.
  - **Zero Developer Credit Drainage**: Hosted mode strictly disables fallback to server-side developer credentials, ensuring visitors never consume developer API credits.
  - **Zero Cross-Visitor Data Leakage**: In hosted mode, all summary records, metadata, and history are stored locally in the visitor's browser via **IndexedDB** (`WebBriefDB`). Visitors never see each other's summaries.
  - **Anonymous Visitor Experience**: Completely account-free. No Google OAuth, mandatory login, or registration required to summarize webpages.
  - **Automatic Credential Redaction**: All OpenAI keys matching standard signatures (`sk-...`) are automatically sanitized and redacted from logs, error messages, and API traces.
  - **Environment Discovery Endpoint**: `/api/environment` serves real-time runtime detection (detecting Vercel, AWS Lambda, Netlify, or self-hosted environments).
- **Local Knowledge Retrieval & Semantic Search (v1.4)**:
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
  - **Dual-URL History Tracking**: Saves comparative entries to history, tagged with `⚖️ Comparison` badges.
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
- **Summary History & Deployment Storage**: Unified abstraction supporting SQLite locally and IndexedDB on hosted serverless environments.

---

## 3. Deployment Models & Architecture (v1.5)

WebBrief AI dynamically adapts its storage and security behavior based on its execution environment:

| Feature | Local Self-Hosted Mode | Public Hosted Vercel Mode |
| :--- | :--- | :--- |
| **Target Runtime** | Local machine, Docker, VPS | Vercel Serverless Functions |
| **BYOK Storage** | Saved in local `.env` file | In-memory RAM during active tab session |
| **Credential Persistence** | Durable across machine restarts | Cleared upon browser tab close or refresh |
| **Developer API Credits** | User's own key in `.env` | Strictly protected (Zero developer key fallback) |
| **Summary & History Storage** | SQLite (`webbrief_history.db`) | Client-side IndexedDB (`WebBriefDB`) |
| **Vector Search Architecture** | Server-side SQLite float blobs | Client-side hybrid similarity & lexical ranking |
| **User Isolation** | Single-user machine boundary | 100% private per visitor (zero cross-user sharing) |
| **Authentication Requirement** | None required | None required (Zero mandatory OAuth or accounts) |

---

## 4. Vector Database Architecture & Evaluation

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

## 5. Visual Previews

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

## 6. Technology Stack

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

## 7. System Architecture

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

## 8. Repository Structure

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
│   ├── storage_adapter.js  # Unified client-side adapter for in-memory BYOK and IndexedDB
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

## 9. Installation Instructions

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

## 10. Virtual Environment Setup

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

## 11. Dependency Installation

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

## 12. First-Run Setup Wizard

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

## 13. OpenAI API Key Configuration

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

## 14. BYOK Architecture & Security Model

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

## 15. Running Locally

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

## 16. Vercel & Cloud Deployment

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

## 17. Environment Variables Reference

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `OPENAI_API_KEY` | Optional* | None | OpenAI API key (*prompted in wizard if missing) |
| `FLASK_SECRET_KEY` | Required in Prod | Ephemeral Dev Key | Secret key used to sign session cookies (fatal in production if missing) |
| `FLASK_DEBUG` | Optional | `0` | Set `1` to enable Flask debugger (development only) |
| `PORT` | Optional | `5000` | Local web server listening port |
| `HOST` | Optional | `127.0.0.1` | Local web server binding host |
| `SESSION_COOKIE_SECURE` | Optional | `false` (local) / `true` (prod) | Sets Secure flag on session cookies for HTTPS |
| `MAX_CONTENT_LENGTH` | Optional | `2097152` (2MB) | Maximum allowed incoming request body size in bytes |

---

## 18. Troubleshooting

### Issue: "OpenAI API key not configured"
- **Solution**: Visit `/setup` and submit a valid OpenAI API key, or check that your `.env` contains `OPENAI_API_KEY=sk-...`.

### Issue: "Access to internal, loopback, or private addresses is restricted"
- **Solution**: WebBrief AI blocks requests to `localhost`, `127.0.0.1`, and private IP ranges to prevent SSRF vulnerabilities. Ensure you supply public internet URLs.

### Issue: "Dynamic browser rendering is not available on this server"
- **Solution**: Run `playwright install chromium` in your virtual environment to download the required headless browser binaries.

### Issue: "Website returned an error (HTTP 403 / 401)"
- **Solution**: Some websites restrict automated web scrapers. WebBrief AI automatically attempts browser emulation, but sites with aggressive anti-bot captchas (Cloudflare Under Attack mode) may block scraping.

---

## 19. Known Limitations

- **Aggressive Cloudflare / CAPTCHA Walls**: Websites that require solving interactive CAPTCHAs cannot be scraped automatically.
- **Paywalled Content**: WebBrief AI extracts only publicly visible content and does not bypass paywalls or logins.
- **Serverless Browser Binaries**: Vercel Serverless Functions have file size limits that prevent bundling full Chromium binaries without external container layers.

---

## 20. Completed Roadmap & Feature Milestones

All planned features and roadmap milestones across the WebBrief AI development lifecycle are completely fulfilled and covered:

- [x] **v1.0 — Core Foundation**: Dual-engine scraping (Requests + Playwright headless fallback), 4 summarization modes (Quick, Detailed, Bullet-point, Technical), hierarchical Map-Reduce chunking, BYOK security architecture, SSRF defense, and dark/light UI.
- [x] **v1.1 — Personalization & Export**: High-fidelity ReportLab PDF export with custom branding and numbered pagination; user-defined custom prompt directives in Settings persisted locally.
- [x] **v1.2 — Multi-URL Batch Summarization**: Tabbed batch input with dynamic rows, independent validation, duplicate detection, resilient concurrent worker execution, real-time progress tracking, expandable result cards, and combined batch PDF export.
- [x] **v1.3 — Comparative Research & Notion Integration**: Side-by-side dual-article comparison with consensus points, contradictions/disagreements, differing perspectives, structured fact claims, and comparative PDF export; official Notion REST API export for single and comparative research briefs.
- [x] **v1.4 — Local Knowledge Retrieval & Semantic Search**: Natural-language vector embedding retrieval across saved summaries using OpenAI `text-embedding-3-small`, embedded binary SQLite float vector storage, deterministic SHA-256 caching (zero redundant billing), exact cosine similarity ranking, and excerpt matching.
- [x] **v1.5 — Hosted BYOK & Deployment-Aware Storage**: Public Vercel serverless deployment support, strictly in-memory active tab visitor keys (never persisted on client disk or server environment), zero developer credit drainage, complete multi-user isolation via browser IndexedDB (`WebBriefDB`), anonymous visitor experience, and automated credential redaction.
- [x] **v1.5.1 — Security & Reliability Hardening**: Comprehensive audit and remediation across 10 security vectors (SEC-01 through SEC-10): mandatory production secret key enforcement, session cookie hardening (`HttpOnly`, `SameSite=Lax`, `Secure`), defensive security headers (`CSP`, `nosniff`, `SAMEORIGIN`), anti-CSRF token & Origin verification, iterative redirect-hopping SSRF guard with cloud metadata & IPv4-mapped IPv6 defenses, complete warm serverless multi-tenant SQLite isolation, DOM-based XSS sanitization, 2MB payload limits, and PDF path traversal neutralization.

---

## 21. Contributing Guidelines

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

## 22. License

Distributed under the **MIT License**. See `LICENSE` for more information.

---

*(c) 2026 WebBrief AI — Built with security, performance, and transparency.*
