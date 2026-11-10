> **v1.6.4 update:** Stability, security, and reliability patch featuring strict production secret enforcement, Playwright SSRF & response-size hardening, Redis vault atomic confirmation, Notion contract alignment, and frontend DOM injection defense.

# WebBrief AI

> **Production Release — v1.6.4**  
> An intelligent, multi-mode website summarizer powered by the Google Gemini Developer API (gemini-3.8-flash) and Flask. Converts any webpage—from standard blogs to JavaScript-rendered Single Page Applications—into structured, high-value briefs in seconds with Hardened Security & Reliability, Hosted BYOK & Deployment-Aware Storage, Local Knowledge Retrieval & Semantic Search, Comparative Research, Notion Integration, Multi-URL Batch Summarization, PDF document export, personalized prompt directives, and transparent model execution.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [What's New in v1.6.4](#2-whats-new-in-v164-stability-security--reliability-patch)
3. [Features](#3-features)
4. [Google Gemini Architecture & Model Execution](#4-google-gemini-architecture--model-execution)
5. [Deployment Models & Architecture](#5-deployment-models--architecture)
6. [Vector Database Architecture & Semantic Search](#6-vector-database-architecture--semantic-search)
7. [Visual Previews](#7-visual-previews)
8. [Technology Stack](#8-technology-stack)
9. [Repository Structure](#9-repository-structure)
10. [Installation Instructions](#10-installation-instructions)
11. [Virtual Environment Setup](#11-virtual-environment-setup)
12. [Dependency Installation](#12-dependency-installation)
13. [First-Run Setup Wizard](#13-first-run-setup-wizard)
14. [Provider Credential Configuration](#14-provider-credential-configuration)
15. [BYOK Architecture & Security Model](#15-byok-architecture--security-model)
16. [Running Locally](#16-running-locally)
17. [Vercel & Cloud Deployment](#17-vercel--cloud-deployment)
18. [Environment Variables Reference](#18-environment-variables-reference)
19. [Troubleshooting](#19-troubleshooting)
20. [Known Limitations](#20-known-limitations)
21. [Completed Roadmap & Feature Milestones](#21-completed-roadmap--feature-milestones)
22. [Contributing Guidelines](#22-contributing-guidelines)
23. [License](#23-license)

---

## 1. Project Overview

WebBrief AI simplifies reading long articles, technical papers, corporate releases, and dynamic websites by distilling raw web content into accurate, five-section structured summaries. 

Built with enterprise-grade SSRF defense, deployment-aware storage, and a **Bring Your Own Key (BYOK)** security architecture, WebBrief AI guarantees that personal API credentials remain private, stored locally in `.env` for self-hosted instances or strictly in active browser memory for public Vercel visitors—never shared between users, never logged, and never exposing developer API credits.

In **v1.6.4**, WebBrief AI delivers a security and stability hardened release while maintaining its modular provider engine supporting the **Google Gemini Developer API** (default: `gemini-3.8-flash`), complete with strict model allowlisting, verified model catalogue, explicit SDK timeouts, and secure BYOK handling.

---

## 2. What's New in v1.6.4 (Stability, Security & Reliability Patch)

- **Flask Production Secret Enforcement**: In production environments, strictly enforces an explicit, high-entropy `FLASK_SECRET_KEY` (minimum 32 characters / 16 bytes). Startup fails safely (`RuntimeError`) if missing or set to insecure placeholders.
- **Playwright SSRF & Response-Size Hardening**: Enforces a cumulative 5 MB streaming limit with true network body accounting (handling missing or inaccurate `Content-Length` headers), valid Playwright abort codes, a 50-request subrequest budget, and independent SSRF verification on all subresources and redirects.
- **Redis Credential Vault Atomic Confirmation**: Ensures `/api/save-config` checks vault write results atomically, returning HTTP 503 if Redis persistence fails and preserving entered user inputs.
- **Notion Integration Security & Hosted Isolation**: Completely removed Notion token storage from client-side Flask session cookies. In hosted mode, persistent connection strictly requires the encrypted Redis vault (or per-request browser-memory submission), with function signatures standardized to a `Tuple[bool, str]` contract.
- **Frontend DOM Injection Prevention**: Replaced raw template string interpolation in Notion page selection and batch URL rows with safe DOM construction (`document.createElement`, `.value`, `.textContent`), eliminating XSS vectors.
- **Shared Serverless Rate Limiting**: Added support for atomic Redis-backed rate limiting (`INCR` + `EXPIRE`) across serverless Lambda instances when Redis is configured, with graceful fallback to process-local in-memory limiting.

---

## 2.1. Highlights from v1.6.3 (Security Hardening, Vault & SSRF Remediation)

- **SSRF Defense with Connection-Level IP Pinning**: DNS validation is paired with custom socket connection pinning (`PinnedIPAdapter`), eliminating Time-of-Check to Time-of-Use (TOCTOU) DNS rebinding attacks.
- **Non-Globally-Routable & CGNAT IP Rejection**: Rejection of all non-globally-routable unicast destination IPs, including Carrier-Grade NAT (CGNAT RFC 6598: `100.64.0.0/10`), loopback, link-local, and cloud metadata addresses (`169.254.169.254`, `100.100.100.200`, `fd00:ec2::254`).
- **Scraper Resource & Memory Protection**: Strict 5 MB streaming download cap on both static (`requests`) and dynamic (`playwright`) extraction pipelines. Subrequest navigation budget (max 50 requests) and reliable browser process termination.
- **Prompt Injection Trust Boundaries**: Explicit security instructions and `<webpage_content>`, `<section_content>`, and `<intermediate_summaries>` XML boundary tags classifying scraped DOM text as untrusted and instructing LLMs to ignore embedded overrides.
- **Ephemeral BYOK Vault**: Optional Redis-backed Fernet-encrypted vault storing and restoring credentials and model selections across sessions.
- **Cross-Page Vault Consistency**: Synchronized vault activation, credential masking, and validation across Home, Settings, and Setup pages.
- **Lightweight Sliding-Window Rate Limiting**: Built-in per-client request throttling on `/summarize`, `/batch/summarize`, `/compare`, `/api/credentials`, and `/api/validate-key` accounting for shared NATs and anonymous sessions.
- **Zero OpenAI Dependencies**: Total removal of legacy OpenAI placeholder values and SDK imports.

---

## 2.2. Highlights from v1.6.0

- **Complete Decoupling from OpenAI**: The OpenAI Python SDK and all direct OpenAI API calls have been completely removed.
- **Modular Provider Abstraction**: Introduces `BaseProvider` and dedicated `GeminiProvider` adapter under `services/providers/`.
- **Google Gemini Developer API**: Fast, multimodal-capable inference powered by Google's Gen AI SDK (recommended models: `gemini-3.8-flash`, `gemini-3.5-flash-lite`).
- **Google Gemini Provider Coordination (`AIRouter`)**: Coordinates execution against the Google Gemini Developer API with strict model validation and unified error classification.
- **Strict Server-Side Model Allowlisting**: Strictly validates requested model IDs against the verified catalogue (`gemini-3.8-flash`, `gemini-3.5-flash-lite`); rejects unsupported or deprecated models without silent substitution.
- **Explicit SDK Timeouts**: Enforces deterministic request timeouts configured via the official `google-genai` SDK `HttpOptions`.
- **Curated Model Catalogue & Selection UI**: Dynamically populated model catalogues with human-friendly descriptions and free-tier tags across `/setup` and `/settings`.
- **Provider Transparency Badge**: Every summary result card displays the exact provider and model that generated it.
- **Upgraded Vector Embeddings**: Local SQLite semantic search utilizes Google Gemini embeddings (`gemini-embedding-001`), with a deterministic feature-vector fallback for unauthenticated environments.

---

## 3. Features

- **Google Gemini BYOK & Strict Model Selection**:
  - Select your preferred Google Gemini model (`gemini-3.8-flash` or `gemini-3.5-flash-lite`) directly from the UI or Settings.
  - Strict server-side allowlisting guarantees unsupported model IDs are rejected immediately with descriptive feedback.
  - Client- and call-level HTTP timeouts safeguard execution against hanging requests.
- **Enterprise Security & Reliability Hardening**:
  - **Zero Hardcoded Secrets & Production Verification**: Eliminates static fallbacks. In production environments, requires an explicit 32-byte `FLASK_SECRET_KEY`; in local development, generates an ephemeral, cryptographically secure 32-byte key (`secrets.token_hex(32)`).
  - **Defensive HTTP Security Headers**: Injects `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`, and a strict Content Security Policy (`CSP`) into every response.
  - **Origin & Anti-CSRF Protection**: Enforces Origin and Referer validation on all mutating HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`) to block cross-origin request forging, accompanied by session anti-CSRF token verification for form submissions.
  - **Enhanced SSRF & Redirect-Hopping Guard**: Upgrades scraper SSRF defenses with an iterative redirect loop (`allow_redirects=False`, max 5 hops) where every hop destination IP is verified against private, loopback, cloud metadata (`169.254.169.254`, `100.100.100.200`, `fd00:ec2::254`), and IPv4-mapped IPv6 ranges before connecting. Rejects embedded URL credentials and restricts requests to safe web ports (`80, 443, 8080, 8443`).
  - **Warm Serverless Multi-Tenant Isolation**: Completely bypasses server-side SQLite writes and reads in hosted serverless mode (Vercel), assigning ephemeral in-memory UUIDs and ensuring zero data leakage between concurrent visitors sharing warm containers.
  - **DOM-Based XSS Sanitization**: Rigorously escapes all dynamic values rendered in the browser DOM (titles, URLs, excerpts, claims, and error strings) across Single, Batch, Compare, and Search views.
  - **PDF Export Path Traversal Neutralization**: Sanitizes downloaded PDF filenames to strictly alphanumeric characters, underscores, and hyphens, preventing path traversal attacks and Content-Disposition header manipulation.
  - **Request Body Payload Limit**: Enforces a strict 2MB maximum content length (`MAX_CONTENT_LENGTH`) to protect backend memory from denial-of-service attempts.
- **Hosted BYOK & Deployment-Aware Storage**:
  - **Public Vercel Hosting Ready**: Visitors can freely use the public deployment by supplying their own Google Gemini API key.
  - **In-Memory Credential Security**: Visitor API credentials reside exclusively in frontend JavaScript RAM during the active tab session. Credentials are **never** stored in `localStorage`, `sessionStorage`, `IndexedDB`, or browser-readable cookies, and are never written to the server's ephemeral filesystem or process environment.
  - **Zero Developer Credit Drainage**: Hosted mode strictly disables fallback to server-side developer credentials, ensuring visitors never consume developer API credits.
  - **Zero Cross-Visitor Data Leakage**: In hosted mode, all summary records, metadata, and history are stored locally in the visitor's browser via **IndexedDB** (`WebBriefDB`). Visitors never see each other's summaries.
  - **Anonymous Visitor Experience**: Completely account-free. No Google OAuth, mandatory login, or registration required to summarize webpages.
  - **Automatic Credential Redaction**: All API keys and tokens matching standard signatures (`AIza...`, Bearer tokens) are automatically sanitized and redacted from logs, error messages, and API traces.
- **Local Knowledge Retrieval & Semantic Search**:
  - **Natural-Language Vector Search**: Query your personal summary library with questions without relying on exact keyword matches.
  - **Embedded SQLite Vector Storage**: Vectors are packed as binary float blobs (`struct.pack`) directly in `webbrief_history.db`. Zero separate database servers, zero native C++ compiler requirements, and 100% ACID persistence across all application restarts.
  - **SHA-256 Content-Hash Caching**: Computes deterministic hashes of summary texts to guarantee that embeddings are generated exactly once per version.
  - **Exact Cosine Similarity Ranking**: Pure Python mathematical dot-product retrieval (< 0.05ms per comparison) with user-controlled relevance thresholds and result limits.
- **Comparative Research & Dual-Source Synthesis**:
  - **Side-by-Side Dual URL Submission**: Input two independent website URLs to run deep comparative fact-checking and analysis.
  - **Shared Ground & Consensus Points**: Automatically isolates common arguments, shared premises, and mutual points of consensus.
  - **Contradictions & Disagreements**: Identifies and highlights direct factual conflicts, opposing claims, or incompatible conclusions with alert callouts.
  - **Comparative Claims Table**: Structured topic-by-topic comparison mapping Source A's claims directly against Source B's claims.
  - **Comparative PDF Export**: Download dedicated, multi-page ReportLab comparative briefs with custom headers, tables, and conflict callouts.
- **Notion Integration & Export**:
  - **Official Notion REST API Integration**: Connects via Notion Internal Integration Secrets (`secret_...` or `ntn_...`).
  - **Structured Block Formatting**: Exports both individual and comparative summaries into native Notion blocks.
- **Multi-URL Batch Summarization**:
  - Manage up to 10 URLs per batch with real-time counters and isolated concurrency (`ThreadPoolExecutor`).
  - Combined multi-article PDF export compiling all batch briefs into a single numbered report.
- **Dual-Engine Scraping with Dynamic Fallback**:
  - **Static Engine**: Fast HTTP requests via `requests` + `BeautifulSoup4` with boilerplate element decomposition.
  - **Dynamic Fallback**: Automatic Playwright headless Chromium invocation for JavaScript-rendered SPAs (React, Vue, Angular).
- **Four Distinct Summarization Modes**: Quick, Detailed, Bullet-Point, and Technical.
- **Hierarchical Map-Reduce for Long Articles**: Natural chunking with token-safe overlap and multi-pass synthesis.

---

## 4. Google Gemini Architecture & Model Execution

```
                     [ User Request + BYOK Credentials ]
                                     |
                                     v
                        +--------------------------+
                        |         AIRouter         |
                        | (Validates Allowed Model)|
                        +--------------------------+
                                     |
                                     v
                       +--------------------------+
                       |      GeminiProvider      |
                       | (google-genai SDK Client)|
                       +--------------------------+
                              /              \
                         (Success)        (Error)
                            /                  \
                           v                    v
                 +-------------------+    +--------------------+
                 | Normalized Result |    | Actionable Feedback|
                 | (Title, Sections) |    |  (No Silent Model  |
                 +-------------------+    |   Substitution)    |
                                          +--------------------+
```

### Provider Details

| Feature | Google Gemini Developer API |
| :--- | :--- |
| **Role** | Primary AI Provider |
| **Credentials Required** | Google Gemini API Key (`AIza...`) |
| **Inference Engine** | Google Gemini Developer Platform |
| **Default Model** | `gemini-3.8-flash` |
| **Verified Models** | `gemini-3.8-flash`<br>`gemini-3.5-flash-lite` |
| **Free-Tier Compatibility** | Free tier available via Google AI Studio |
| **Timeouts** | Explicit HTTP timeouts via `google.genai.types.HttpOptions` |
| **Text Embeddings** | `gemini-embedding-001` (768-dim) / deterministic fallback |

---

## 5. Deployment Models & Architecture

WebBrief AI dynamically adapts its storage and security behavior based on its execution environment:

| Feature | Local Self-Hosted Mode | Public Hosted Vercel Mode |
| :--- | :--- | :--- |
| **Target Runtime** | Local machine, Docker, VPS | Vercel Serverless Functions |
| **BYOK Storage** | Saved in local `.env` file | In-memory RAM during active tab session |
| **Credential Persistence** | Durable across machine restarts | Cleared upon browser tab close or refresh |
| **Developer API Credits** | User's own credentials in `.env` | Strictly protected (Zero developer key fallback) |
| **Summary & History Storage** | SQLite (`webbrief_history.db`) | Client-side IndexedDB (`WebBriefDB`) |
| **Vector Search Architecture** | Server-side SQLite float blobs | Client-side hybrid similarity & lexical ranking |
| **User Isolation** | Single-user machine boundary | 100% private per visitor (zero cross-user sharing) |
| **Authentication Requirement** | None required | None required (Zero mandatory OAuth or accounts) |

---

## 6. Vector Database Architecture & Semantic Search

WebBrief AI uses embedded vector storage directly inside SQLite (`webbrief_history.db`):

- **Zero Additional Daemons**: Embeddings are packed as binary float blobs (`struct.pack`) alongside article metadata.
- **Embeddings Provider**: Google Gemini `gemini-embedding-001` (768 dimensions). A deterministic feature-vector hashing generator is also available for unauthenticated environments.
- **SHA-256 Content Caching**: Embeddings are indexed by content hash. Summaries are never re-embedded unless their text changes.
- **Search Latency**: Pure Python dot product executes in **~0.03ms** per vector comparison.

---

## 7. Visual Previews

### Primary Interface & Model Badging
```
+-------------------------------------------------------------------------+
| [O] WebBrief AI  v1.6.4             [History]  [Settings]  [* Mode: Dark]|
| Turn any webpage into an accurate, structured AI summary in seconds.    |
+-------------------------------------------------------------------------+
| Webpage URL: [ https://example.com/deep-dive-article                 ]  |
|                                                                         |
| Summarization Mode:                                                     |
| [⚡ Quick]       [📖 Detailed]     [📋 Bullet-Point]   [⚙️ Technical]    |
|                                                                         |
| [               Summarize Webpage (Google Gemini)                     ] |
+-------------------------------------------------------------------------+
```

### Result Card with Provider Transparency
```
+-------------------------------------------------------------------------+
|  ✦ Quantum Computing Breakthrough                                      |
|  [Detailed Summary]  Source: https://example.com...                      |
|  ⚙️ Provider: Google Gemini          |  Model: gemini-3.8-flash
|                                                                         |
|  ## Overview                                               [Copy Section]|
|  Researchers achieve fault-tolerant logical qubits with neutral atoms... |
|                                                                         |
|  ## Key Points                                            [Copy Section]|
|  • 48 logical qubits sustained over multi-step quantum circuits.        |
|  • Two-qubit gate fidelities exceeded 99.5% across the array.           |
+-------------------------------------------------------------------------+
```

---

## 8. Technology Stack

- **Backend Runtime**: Python 3.10+
- **Web Framework**: Flask 3.1+ (Jinja2 templates, signed `HttpOnly` sessions)
- **AI Engine**: Modular `AIRouter` with official Google Gen AI SDK for **Google Gemini**
- **Web Scraping**:
  - Static: `requests` + `BeautifulSoup4`
  - Dynamic: `playwright` (Chromium headless with request routing)
- **Persistence**: SQLite3 (native standard library) with IndexedDB client-side fallback
- **Frontend**: Vanilla JavaScript (ES6+), CSS Custom Properties (zero heavy frontend frameworks)
- **Production Server**: Gunicorn WSGI (Linux/Docker) or Vercel Python runtime

---

## 9. Repository Structure

```
WebBrief AI/
├── api/
│   └── index.py            # Vercel serverless function entrypoint bridge
├── app.py                  # Core Flask application, routing, and controller logic
├── batch_processor.py      # Multi-URL batch processor, concurrency control, progress callback
├── byok.py                 # Gemini credential validation, safe .env persistence
├── chunker.py              # Paragraph-aware chunking and token overlap management
├── comparative_analyzer.py # Dual-source comparative research, claims matrix & fact synthesis
├── history.py              # Local SQLite summary & comparison persistence layer (CRUD)
├── notion_service.py       # Official Notion REST API client, block parser & page exporter
├── pdf_exporter.py         # ReportLab multi-page PDF generation engine with NumberedCanvas
├── scraper.py              # Dual static/dynamic scraper, SSRF firewall, Playwright engine
├── summarizer.py           # Multi-mode prompt engine, Map-Reduce pipeline, AIRouter integration
├── vector_store.py         # Local SQLite vector database engine, cosine search & hash cache
├── services/
│   └── providers/
│       ├── __init__.py     # Module initialization and exports
│       ├── base.py         # BaseProvider abstract base class definition
│       └── gemini.py       # Google Gemini Gen AI SDK adapter & embeddings
│       ├── router.py       # AIRouter provider coordination & model validation
│       ├── catalogue.py    # Verified model catalogue & free-tier mappings
│       └── exceptions.py   # Normalized provider exception hierarchy
├── static/
│   ├── storage_adapter.js  # Unified client-side adapter for in-memory BYOK and IndexedDB
│   └── style.css           # Responsive stylesheet with light/dark theme variables
├── templates/
│   ├── index.html          # Primary application dashboard, mode selector, and result cards
│   ├── history.html        # Dedicated summary library view (search, reopen, delete)
│   ├── result.html         # Standalone result view with provider transparency badges
│   ├── setup.html          # First-run BYOK onboarding wizard (redirects to settings)
│   └── settings.html       # Gemini configuration, model selection, danger zone
├── .env.example            # Gemini environment variable template
├── .gitignore              # Git ignore rules (protects credentials and local DBs)
├── requirements.txt        # Production Python dependencies (includes Gunicorn & Playwright)
├── vercel.json             # Vercel Serverless deployment configuration
└── README.md               # Comprehensive documentation
```

---

## 10. Installation Instructions

### Prerequisites
- Python 3.10, 3.11, or 3.12 installed on your machine.
- Git installed.
- A Google Gemini Developer API key:
  - **Google Gemini API Key** (https://aistudio.google.com/app/apikey).

### Clone the Repository
```bash
git clone https://github.com/Ashwin-Maharaja/WebBrief-AI.git
cd WebBrief-AI
```

---

## 11. Virtual Environment Setup

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

## 12. Dependency Installation

Install all required Python libraries:
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

*(Optional but recommended)*: Install Playwright's Chromium browser binary for JavaScript-heavy website fallback:
```bash
playwright install chromium
```

---

## 13. First-Run Setup Wizard

1. Start the Flask development server:
   ```bash
   python app.py
   ```
2. Navigate to `http://127.0.0.1:5000` in your web browser.
3. If no AI provider credentials are configured, WebBrief AI automatically redirects you to `/setup`.
4. Enter your Google Gemini API key and select your preferred model (`gemini-3.8-flash`).
5. Provide your credentials and click **Validate & Save Configuration**.
6. WebBrief AI executes a live, minimal verification request to verify authentication before persisting.

---

## 14. Provider Credential Configuration

You can configure your credentials via the interactive web UI or directly in `.env`:

### Google Gemini Developer API Configuration
1. Sign up and create an API key at [Google AI Studio](https://aistudio.google.com/app/apikey).
2. In `.env`:
   ```ini
   PRIMARY_PROVIDER=gemini
   GEMINI_API_KEY=your_gemini_api_key_here
   GEMINI_DEFAULT_MODEL=gemini-3.8-flash
   ```

---

## 15. BYOK Architecture & Security Model

WebBrief AI strictly enforces a **Zero-Credential Exposure** architecture:

- **Isolated Storage**: Saved credentials reside exclusively in your local `.env` or in active browser RAM on hosted Vercel deployments.
- **No Client Leakage**: Sensitive tokens and keys are never rendered in frontend templates, never injected into HTML attributes, and never stored in cookies or client disk on hosted deployments.
- **Automatic Sanitization**: Error messages and logs automatically mask Google Gemini API keys (`AIza...`).
- **Overwrite Protection**: Modifying an existing `.env` configuration requires explicit confirmation.
- **SSRF Defense**:
  - DNS pre-resolution restricts scraper requests to public IP addresses.
  - Re-evaluates destination URLs across 301/302 redirects (up to 5 hops).
  - Blocks loopback (`127.0.0.1`), cloud metadata endpoints (`169.254.169.254`, `100.100.100.200`, `fd00:ec2::254`), and private RFC 1918 subnets.

---

## 16. Running Locally

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

## 17. Vercel & Cloud Deployment

WebBrief AI is configured for one-click deployment on **Vercel Serverless Functions**.

### 1. Deploy via Vercel CLI
```bash
npm install -g vercel
vercel login
vercel
```

### 2. Deploy via GitHub Integration
1. Push your repository to GitHub (`https://github.com/Ashwin-Maharaja/WebBrief-AI`).
2. In the Vercel Dashboard, click **Add New Project** and select your repository.
3. Vercel automatically detects `vercel.json` and routes requests directly to `app.py`.
4. Under **Environment Variables**, configure:
   - `FLASK_SECRET_KEY`: High-entropy 32-character secret string (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`). Required in production.
   - `SESSION_COOKIE_SECURE`: `true`
   - `REDIS_URL` (optional): Managed TLS Redis connection string (e.g. Upstash) for encrypted session vault and cross-instance rate limiting.
   - `CREDENTIAL_ENCRYPTION_KEY` (optional): Fernet key if `REDIS_URL` is set (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
5. Click **Deploy**.

---

## 18. Environment Variables Reference

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `PRIMARY_PROVIDER` | Required | `gemini` | Primary AI provider (`gemini`) |
| `GEMINI_API_KEY` | Required | None | Google Gemini Developer API key |
| `GEMINI_DEFAULT_MODEL` | Optional | `gemini-3.8-flash` | Default Gemini model identifier |
| `NOTION_API_KEY` | Optional | None | Notion Internal Integration Secret for exporting briefs |
| `FLASK_SECRET_KEY` | Required in Prod | Ephemeral Dev Key | 32-byte secret key used to sign session cookies |
| `FLASK_DEBUG` | Optional | `0` | Set `1` to enable Flask debugger (development only) |
| `PORT` | Optional | `5000` | Local web server listening port |
| `HOST` | Optional | `127.0.0.1` | Local web server binding host |
| `SESSION_COOKIE_SECURE` | Optional | `false` (local) / `true` (prod) | Sets Secure flag on session cookies for HTTPS |
| `MAX_CONTENT_LENGTH` | Optional | `2097152` (2MB) | Maximum allowed incoming request body size in bytes |
| `REDIS_URL` | Optional | None | TLS-enabled Redis URL for encrypted temporary BYOK vault |
| `CREDENTIAL_ENCRYPTION_KEY` | Optional | None | Base64-encoded 32-byte Fernet key for vault encryption |

---

## 19. Troubleshooting

### Issue: "Provider authentication failed"
- **Google Gemini**: Verify your API key at https://aistudio.google.com/app/apikey and ensure it starts with `AIza`.

### Issue: "Primary provider rate limited (HTTP 429)"
- Check API quotas and limits in Google AI Studio if experiencing 429 rate limit responses.

### Issue: "Access to internal, loopback, or private addresses is restricted"
- WebBrief AI blocks requests to `localhost`, `127.0.0.1`, and private IP ranges to prevent SSRF vulnerabilities. Ensure you supply public internet URLs.

### Issue: "Dynamic browser rendering is not available on this server"
- Run `playwright install chromium` in your virtual environment to download headless browser binaries.

---

## 20. Known Limitations

- **Aggressive Cloudflare / CAPTCHA Walls**: Websites that require solving interactive CAPTCHAs cannot be scraped automatically.
- **Paywalled Content**: WebBrief AI extracts only publicly visible content and does not bypass paywalls or logins.
- **Serverless Browser Binaries**: Vercel Serverless Functions have file size limits that prevent bundling full Chromium binaries without external container layers.

---

## 21. Completed Roadmap & Feature Milestones

- [x] **v1.0 — Core Foundation**: Dual-engine scraping, 4 summarization modes, hierarchical Map-Reduce chunking, BYOK security architecture, SSRF defense, and dark/light UI.
- [x] **v1.1 — Personalization & Export**: High-fidelity ReportLab PDF export with custom branding and numbered pagination; user-defined custom prompt directives in Settings.
- [x] **v1.2 — Multi-URL Batch Summarization**: Tabbed batch input with dynamic rows, independent validation, duplicate detection, resilient concurrent worker execution, and combined batch PDF export.
- [x] **v1.3 — Comparative Research & Notion Integration**: Side-by-side dual-article comparison with consensus points, contradictions/disagreements, differing perspectives; official Notion REST API export.
- [x] **v1.4 — Local Knowledge Retrieval & Semantic Search**: Natural-language vector embedding retrieval across saved summaries, embedded binary SQLite float vector storage, deterministic SHA-256 caching.
- [x] **v1.5 — Hosted BYOK & Deployment-Aware Storage**: Public Vercel serverless deployment support, strictly in-memory active tab visitor keys, zero developer credit drainage, complete multi-user isolation via browser IndexedDB (`WebBriefDB`).
- [x] **v1.5.1 — Security & Reliability Hardening**: Comprehensive audit and remediation across 10 security vectors (SEC-01 through SEC-10).
- [x] **v1.6.0 — Gemini API Migration**: Complete removal of Cloudflare and GroqCloud dependencies; modular provider architecture (services/providers/gemini.py); Google Gemini Developer API (gemini-3.8-flash) via official google-genai SDK; updated model catalogue; updated vector embeddings (gemini-embedding-001).

---

## 22. Contributing Guidelines

Contributions are welcome! Please follow these steps:

1. **Fork the Repository**.
2. **Create a Feature Branch**: `git checkout -b feature/amazing-feature`.
3. **Write Tests**: Ensure any changes include accompanying tests in `tests/`.
4. **Run Test Suites**:
   ```bash
   python -m unittest discover tests
   ```
5. **Commit Your Changes**: Follow conventional commits (e.g., `feat: ...`, `fix: ...`).
6. **Open a Pull Request**.

---

## 23. License

Distributed under the **MIT License**. See `LICENSE` for more information.

---

*(c) 2026 WebBrief AI — Built with security, performance, and transparency.*
