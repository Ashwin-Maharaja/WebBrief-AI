# WebBrief AI

> **Production Release — v1.7.0**  
> An intelligent, multi-mode website summarizer powered by the Google Gemini Developer API (`gemini-3.8-flash`) and Flask. Converts any webpage—from static articles to JavaScript-rendered Single Page Applications—into structured, high-value briefs in seconds with Hardened Security & Reliability, Hosted BYOK & Deployment-Aware Storage, Local Knowledge Retrieval & Semantic Search, Comparative Research, Notion Integration, Multi-URL Batch Summarization, PDF Document Export, and a Unified Single-Page Application (SPA) Interface.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [What's New in v1.7.0](#2-whats-new-in-v170-unified-spa--production-stabilization)
3. [Features](#3-features)
4. [Google Gemini Architecture & Model Execution](#4-google-gemini-architecture--model-execution)
5. [Deployment Models & Storage Architecture](#5-deployment-models--storage-architecture)
6. [Vector Database Architecture & Semantic Search](#6-vector-database-architecture--semantic-search)
7. [Visual Previews](#7-visual-previews)
8. [Technology Stack](#8-technology-stack)
9. [Repository Structure](#9-repository-structure)
10. [Installation Instructions](#10-installation-instructions)
11. [Virtual Environment Setup](#11-virtual-environment-setup)
12. [Dependency Installation](#12-dependency-installation)
13. [First-Run BYOK Configuration](#13-first-run-byok-configuration)
14. [Provider Credential Configuration](#14-provider-credential-configuration)
15. [BYOK Architecture & Security Model](#15-byok-architecture--security-model)
16. [Running Locally](#16-running-locally)
17. [Vercel & Cloud Deployment](#17-vercel--cloud-deployment)
18. [Environment Variables Reference](#18-environment-variables-reference)
19. [Testing Suite & Verification](#19-testing-suite--verification)
20. [Troubleshooting](#20-troubleshooting)
21. [Known Limitations](#21-known-limitations)
22. [Completed Roadmap & Feature Milestones](#22-completed-roadmap--feature-milestones)
23. [Contributing Guidelines](#23-contributing-guidelines)
24. [License](#24-license)

---

## 1. Project Overview

WebBrief AI simplifies reading long articles, technical papers, corporate releases, and dynamic websites by distilling raw web content into accurate, five-section structured summaries.

Built with enterprise-grade SSRF defense, deployment-aware storage, and a **Bring Your Own Key (BYOK)** security architecture, WebBrief AI guarantees that personal API credentials remain private—stored locally in `.env` for self-hosted instances or secured via a server-side Fernet-encrypted Redis credential vault with persistent `wb_vault_id` browser cookies (with ephemeral in-memory fallback when Redis is unconfigured)—never shared between users, never logged, and never exposing developer API credits.

In **v1.7.0**, WebBrief AI delivers a fully unified, single-page application experience with comprehensive mobile-first responsiveness, rigorous function-level audit remediation, atomic credential vault persistence, hardened Playwright scraping limits, and a 236-test regression suite.

---

## 2. What's New in v1.7.0 (Unified SPA & Production Stabilization)

- **Unified Single-Page Application (SPA)**: Deprecated standalone `/settings`, `/setup`, and `/history` templates in favor of a cohesive, zero-friction dashboard in `templates/index.html`. Legacy routes return permanent HTTP 301 redirects to `/`.
- **Integrated In-Place Configuration**: Manage API keys, select allowed Gemini models, connect Notion integration, and rebuild vector search indexes directly through an accessible modal dialog on the main dashboard.
- **Mobile-First Responsive Design**: Added CSS media queries supporting viewports from 320px mobile screens up to 4K displays. Features touch-friendly tap targets (minimum 44x44px), horizontally scrolling comparative claims tables, and fluid card layouts.
- **Audited History Persistence**: Verified real disk writes for SQLite history operations, eliminating false-positive success states and adding graceful disk-full and lock-timeout recovery.
- **Atomic Credential Vault & Pipeline Fallback**: Hardened Redis credential vault with atomic read-merge-write semantics, automatic fallback for Redis clusters lacking transaction pipelines, and Fernet AES-128-CBC encryption.
- **Playwright Scraping Hardening**: Streaming 10 MiB payload cap with true network body accounting (handling missing or inaccurate `Content-Length` headers), subrequest budget limit (max 50 requests), valid abort codes, and connection-level IP pinning.
- **Production Hardened Security**: Nonce/strict Content Security Policy (CSP), origin/referer validation on all mutating routes, strict anti-CSRF token verification, and zero-trust DOM construction.
- **236-Test Comprehensive Verification**: Complete regression coverage across unit, integration, and security surfaces with 0 errors and 0 failures.

---

### 2.1. Highlights from v1.6.4 & v1.6.3
- **Flask Production Secret Enforcement**: Enforces an explicit, high-entropy 32-character `FLASK_SECRET_KEY` in production; generates ephemeral cryptographically secure keys during local development.
- **SSRF Defense with Connection-Level IP Pinning**: DNS validation is paired with custom socket connection pinning (`PinnedIPAdapter`), eliminating Time-of-Check to Time-of-Use (TOCTOU) DNS rebinding attacks.
- **Non-Globally-Routable & CGNAT IP Rejection**: Rejection of all non-globally-routable unicast destination IPs, including Carrier-Grade NAT (CGNAT RFC 6598: `100.64.0.0/10`), loopback, link-local, and cloud metadata addresses (`169.254.169.254`, `100.100.100.200`, `fd00:ec2::254`).
- **Prompt Injection Trust Boundaries**: Explicit security boundaries with `<webpage_content>`, `<section_content>`, and `<intermediate_summaries>` XML boundary tags instructing LLMs to ignore embedded overrides.

---

### 2.2. Highlights from v1.6.0 (Gemini Engine Migration)
- **Modular Provider Abstraction**: Dedicated `GeminiProvider` adapter under `services/providers/` utilizing the official `google-genai` SDK.
- **Google Gemini Developer API**: Fast, multimodal-capable inference powered by `gemini-3.8-flash` (default) and `gemini-3.5-flash-lite`.
- **Strict Server-Side Model Allowlisting**: Strictly validates requested model IDs against the verified catalogue; rejects unsupported or deprecated models without silent substitution.
- **Explicit SDK Timeouts**: Enforces deterministic request timeouts configured via the official `google-genai` SDK `HttpOptions`.
- **Upgraded Vector Embeddings**: Local SQLite semantic search utilizes Google Gemini embeddings (`gemini-embedding-001`), with a deterministic feature-vector fallback for unauthenticated environments.

---

## 3. Features

- **Google Gemini BYOK & Model Selection**:
  - Select your preferred Google Gemini model (`gemini-3.8-flash` or `gemini-3.5-flash-lite`) directly from the dashboard configuration modal.
  - Strict server-side allowlisting guarantees unsupported model IDs are rejected immediately with descriptive feedback.
  - Client- and call-level HTTP timeouts safeguard execution against hanging requests.
- **Single-Page Application Experience**:
  - All modes—Single Summary, Batch Summarization, Comparative Analysis, Semantic Search, and History Library—operate fluidly on a single, responsive view.
  - Collapsible **Custom Prompt Directives** accordion to personalize summary tone and focus.
  - Slide-out **Summary Library Drawer** for searching, viewing, reopening, and deleting past briefs.
  - Instant dark/light theme switching with persistence in `StorageAdapter`.
- **Enterprise Security & Reliability Hardening**:
  - **Zero Hardcoded Secrets**: In production environments, requires an explicit 32-byte `FLASK_SECRET_KEY`; in local development, generates an ephemeral, cryptographically secure 32-byte key (`secrets.token_hex(32)`).
  - **Defensive HTTP Security Headers**: Injects `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`, and a strict Content Security Policy (`CSP`) into every response.
  - **Origin & Anti-CSRF Protection**: Enforces Origin and Referer validation on all mutating HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`) to block cross-origin request forging, accompanied by session anti-CSRF token verification for form submissions.
  - **Enhanced SSRF & Redirect-Hopping Guard**: Upgrades scraper SSRF defenses with an iterative redirect loop (`allow_redirects=False`, max 5 hops) where every hop destination IP is verified against private, loopback, cloud metadata (`169.254.169.254`, `100.100.100.200`, `fd00:ec2::254`), and IPv4-mapped IPv6 ranges before connecting. Rejects embedded URL credentials and restricts requests to safe web ports (`80, 443, 8080, 8443`).
  - **Warm Serverless Multi-Tenant Isolation**: Bypasses server-side SQLite writes and reads in hosted serverless mode (Vercel), assigning ephemeral in-memory UUIDs and ensuring zero data leakage between concurrent visitors sharing warm containers.
  - **DOM-Based XSS Sanitization**: Rigorously escapes all dynamic values rendered in the browser DOM (titles, URLs, excerpts, claims, and error strings) across Single, Batch, Compare, and Search views.
  - **PDF Export Path Traversal Neutralization**: Sanitizes downloaded PDF filenames to strictly alphanumeric characters, underscores, and hyphens, preventing path traversal attacks and Content-Disposition header manipulation.
  - **Request Body Payload Limit**: Enforces a strict 2MB maximum content length (`MAX_CONTENT_LENGTH`) to protect backend memory from denial-of-service attempts.
- **Hosted BYOK & Deployment-Aware Storage**:
  - **Public Cloud Hosting Ready**: Visitors can freely use the public deployment by supplying their own Google Gemini API key.
  - **Encrypted Persistent Vault**: When Redis (`REDIS_URL`) and `CREDENTIAL_ENCRYPTION_KEY` are configured, credentials are encrypted via 256-bit Fernet and stored in Redis indefinitely without TTL. An opaque, 10-year HttpOnly `wb_vault_id` cookie references the SHA-256 hashed key in Redis.
  - **In-Memory Credential Security (Fallback)**: When Redis is omitted, visitor API credentials reside exclusively in frontend JavaScript RAM during the active tab session. Credentials are **never** stored in `localStorage`, `sessionStorage`, `IndexedDB`, or client-accessible cookies.
  - **Zero Developer Credit Drainage**: Hosted mode strictly disables fallback to server-side developer credentials, ensuring visitors never consume developer API credits.
  - **Zero Cross-Visitor Data Leakage**: In hosted mode, all summary records, metadata, and history are stored locally in the visitor's browser via **IndexedDB** (`WebBriefDB`).
  - **Anonymous Visitor Experience**: Completely account-free. No Google OAuth, mandatory login, or registration required to summarize webpages.
  - **Automatic Credential Redaction**: All API keys and tokens matching standard signatures (`AIza...`, Bearer tokens) are automatically sanitized and redacted from logs, error messages, and API traces.
- **Local Knowledge Retrieval & Semantic Search**:
  - **Natural-Language Vector Search**: Query your personal summary library with conceptual questions without relying on exact keyword matches.
  - **Embedded SQLite Vector Storage**: Vectors are packed as binary float blobs (`struct.pack`) directly in `webbrief_history.db`. Zero separate database servers, zero native C++ compiler requirements, and 100% ACID persistence across application restarts.
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

```text
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

## 5. Deployment Models & Storage Architecture

WebBrief AI dynamically adapts its storage and security behavior based on its execution environment:

| Feature | Local Self-Hosted Mode | Public Hosted Vercel Mode |
| :--- | :--- | :--- |
| **Target Runtime** | Local machine, Docker, VPS | Vercel Serverless Functions |
| **BYOK Storage** | Saved in local `.env` file | Encrypted Redis vault (Fernet + HttpOnly cookie) or in-memory RAM fallback |
| **Credential Persistence** | Durable across machine restarts | Indefinite persistence in Redis (or tab-lifetime in RAM fallback) |
| **Developer API Credits** | User's own credentials in `.env` | Strictly protected (Zero developer key fallback) |
| **Summary & History Storage** | SQLite (`webbrief_history.db`) | Client-side IndexedDB (`WebBriefDB`) |
| **Vector Search Architecture** | Server-side SQLite float blobs | Client-side hybrid similarity & lexical ranking |
| **User Isolation** | Single-user machine boundary | 100% private per visitor (zero cross-user sharing) |
| **Authentication Requirement** | None required | None required (Zero mandatory OAuth or accounts) |

### 5.1. Persistent BYOK Credential Vault & Durability Architecture

When WebBrief AI is deployed on hosted serverless infrastructure alongside managed Redis:

1. **Encrypted Storage without TTL**:
   - Credentials (`gemini_api_key`, `gemini_model`, `notion_token`) are encrypted with server-side AES-128-CBC / HMAC-SHA256 (Fernet) using `CREDENTIAL_ENCRYPTION_KEY`.
   - Records are saved via atomic `SET` commands without TTL or expiration parameters, persisting indefinitely until the visitor clicks **Clear Saved Credentials** or sends an explicit DELETE request.
2. **Opaque Cookie Lookup Reference**:
   - The browser receives an opaque 256-bit cryptographically secure identifier stored in a dedicated `wb_vault_id` cookie (`HttpOnly = True`, `SameSite = 'Lax'`, `Secure = True`, 10-year expiration).
   - Redis keys are addressed via SHA-256 hashes (`webbrief:credentials:<sha256>`). Raw browser cookies never appear in Redis key names, and client-side JavaScript cannot read the cookie.
3. **Redis Durability Requirements (AOF & RDB)**:
   - To ensure credential survival across Redis server or cluster restarts, enable Append-Only File persistence (`appendonly yes`, `appendfsync everysec`) and periodic RDB snapshots.
   - For managed providers (Upstash, AWS ElastiCache, Redis Enterprise), enable automatic persistence and multi-AZ replication.
4. **Capacity & Eviction Policy**:
   - WebBrief AI requires `maxmemory-policy noeviction`. Under memory exhaustion, Redis returns an explicit OOM error rather than evicting saved encryption keys. The application catches this safely, returning HTTP 503 instead of corrupting user state.
5. **Cookie Loss & Disconnect Semantics**:
   - If a visitor clears browser cookies, the lookup identifier is lost. Because Redis keys are keyed by the SHA-256 hash of the random identifier, orphaned encrypted records cannot be correlated to any user and remain unreadable without both the cookie and the server's secret Fernet key.
   - Explicit disconnect cleanly deletes the hashed Redis record and removes the `wb_vault_id` cookie.

---

## 6. Vector Database Architecture & Semantic Search

WebBrief AI uses embedded vector storage directly inside SQLite (`webbrief_history.db`):

- **Zero Additional Daemons**: Embeddings are packed as binary float blobs (`struct.pack`) alongside article metadata.
- **Embeddings Provider**: Google Gemini `gemini-embedding-001` (768 dimensions). A deterministic feature-vector hashing generator is also available for unauthenticated environments.
- **SHA-256 Content Caching**: Embeddings are indexed by content hash. Summaries are never re-embedded unless their text changes.
- **Search Latency**: Pure Python dot product executes in **~0.03ms** per vector comparison.

---

## 7. Visual Previews

### Single-Page Unified Dashboard Layout
```text
+-------------------------------------------------------------------------------+
| [O] WebBrief AI                 [📚 History Library] [⚙️ Config] [🌓 Theme]   |
| Turn any webpage into an accurate, structured AI summary in seconds.          |
+-------------------------------------------------------------------------------+
| Navigation Modes:                                                             |
| [⚡ Single URL]    [📑 Batch (Up to 10)]    [⚖️ Compare Dual URLs]   [🔍 Search] |
+-------------------------------------------------------------------------------+
| Webpage URL: [ https://example.com/deep-dive-article                        ] |
|                                                                               |
| Summarization Mode:                                                           |
| (•) Quick Summary     ( ) Detailed Brief     ( ) Bullet Points   ( ) Technical|
|                                                                               |
| ▸ Custom Prompt Directives (Optional)                                         |
|                                                                               |
| [                     Summarize Webpage (Google Gemini)                     ] |
+-------------------------------------------------------------------------------+
```

### Result Card with Provider Transparency
```text
+-------------------------------------------------------------------------------+
|  ✦ Quantum Computing Breakthrough                                            |
|  [Detailed Summary]  Source: https://example.com...                           |
|  ⚙️ Provider: Google Gemini          |  Model: gemini-3.8-flash                |
|                                                                               |
|  ## Overview                                                     [Copy Section]|
|  Researchers achieve fault-tolerant logical qubits with neutral atoms...      |
|                                                                               |
|  ## Key Points                                                  [Copy Section]|
|  • 48 logical qubits sustained over multi-step quantum circuits.              |
|  • Two-qubit gate fidelities exceeded 99.5% across the array.                 |
+-------------------------------------------------------------------------------+
```

---

## 8. Technology Stack

- **Backend Runtime**: Python 3.10+
- **Web Framework**: Flask 3.1+ (Jinja2 templates, signed `HttpOnly` sessions)
- **AI Engine**: Modular `AIRouter` with official Google Gen AI SDK for **Google Gemini**
- **Web Scraping**:
  - Static: `requests` + `BeautifulSoup4` + `trafilatura`
  - Dynamic: `playwright` (Chromium headless with request routing and IP pinning)
- **Persistence**: SQLite3 (native standard library) with IndexedDB client-side fallback
- **Frontend**: Vanilla JavaScript (ES6+), CSS Custom Properties (zero heavy frontend frameworks)
- **Production Server**: Gunicorn WSGI (Linux/Docker) or Waitress (Windows)

---

## 9. Repository Structure

```text
WebBrief AI/
├── app.py                  # Core Flask application, routing, and controller logic
├── batch_processor.py      # Multi-URL batch processor, concurrency control, progress callback
├── byok.py                 # Gemini credential validation, safe .env persistence
├── chunker.py              # Paragraph-aware chunking and token overlap management
├── comparative_analyzer.py # Dual-source comparative research, claims matrix & fact synthesis
├── credential_vault.py     # Redis Fernet-encrypted BYOK credential vault & session isolation
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
│       ├── catalogue.py    # Verified model catalogue & free-tier mappings
│       ├── exceptions.py   # Normalized provider exception hierarchy
│       ├── gemini.py       # Google Gemini Gen AI SDK adapter & embeddings
│       └── router.py       # AIRouter provider coordination & model validation
├── static/
│   ├── storage_adapter.js  # Unified client-side adapter for in-memory BYOK and IndexedDB
│   └── style.css           # Mobile-first responsive stylesheet with light/dark theme variables
├── templates/
│   └── index.html          # Unified single-page application dashboard, BYOK config, history drawer, and results
├── tests/                  # 236 comprehensive unit, integration, and security regression tests
│   ├── test_credential_vault.py
│   ├── test_preferences_persistence.py
│   ├── test_providers.py
│   ├── test_security.py
│   ├── test_ui_responsiveness.py
│   ├── test_audit_stabilization_fixes.py
│   ├── test_integration_fixes.py
│   ├── test_phase3_hardening.py
│   ├── test_real_redis_durability.py
│   ├── test_scraper_size_limits.py
│   ├── test_single_page_ui.py
│   └── test_vault_persistence.py
├── .env.example            # Environment variable template
├── .gitignore              # Git ignore rules (protects credentials and local DBs)
├── requirements.txt        # Production Python dependencies
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

## 13. First-Run BYOK Configuration

1. Start the web server:
   ```bash
   python app.py
   ```
2. Navigate to `http://127.0.0.1:5000` (or `http://localhost:5000`) in your web browser.
3. If no credentials are configured, the **BYOK Configuration Modal** automatically opens on the dashboard.
4. Enter your Google Gemini API key (`AIza...`) and select your preferred model (`gemini-3.8-flash`).
5. Click **Validate & Save Configuration**. WebBrief AI executes a live, minimal verification request to verify authentication before persisting.

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

### Development Server
```bash
python app.py
```
Visit `http://127.0.0.1:5000` or `http://localhost:5000` in your browser.

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

WebBrief AI is configured for deployment on **Vercel Serverless Functions**.

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
| `REDIS_URL` | Optional | None | TLS-enabled Redis URL for encrypted persistent BYOK vault and rate limiting |
| `CREDENTIAL_ENCRYPTION_KEY` | Optional | None | Base64-encoded 32-byte Fernet key for vault encryption |

---

## 19. Testing Suite & Verification

WebBrief AI includes a comprehensive 236-test regression and security verification suite:

```bash
# Run the complete test suite
python -m unittest discover tests

# Run specific functional suites
python -m unittest tests/test_ui_responsiveness.py
python -m unittest tests/test_preferences_persistence.py
python -m unittest tests/test_credential_vault.py
python -m unittest tests/test_security.py
python -m unittest tests/test_providers.py
```

---

## 20. Troubleshooting

### Issue: "Provider authentication failed"
- **Google Gemini**: Verify your API key at https://aistudio.google.com/app/apikey and ensure it starts with `AIza`.

### Issue: "Primary provider rate limited (HTTP 429)"
- Check API quotas and limits in Google AI Studio if experiencing 429 rate limit responses.

### Issue: "Access to internal, loopback, or private addresses is restricted"
- WebBrief AI blocks requests to `localhost`, `127.0.0.1`, and private IP ranges to prevent SSRF vulnerabilities. Ensure you supply public internet URLs.

### Issue: "Dynamic browser rendering is not available on this server"
- Run `playwright install chromium` in your virtual environment to download headless browser binaries.

---

## 21. Known Limitations

- **Aggressive Cloudflare / CAPTCHA Walls**: Websites that require solving interactive CAPTCHAs cannot be scraped automatically.
- **Paywalled Content**: WebBrief AI extracts only publicly visible content and does not bypass paywalls or logins.
- **Serverless Browser Binaries**: Vercel Serverless Functions have file size limits that prevent bundling full Chromium binaries without external container layers.

---

## 22. Completed Roadmap & Feature Milestones

- [x] **v1.0 — Core Foundation**: Dual-engine scraping, 4 summarization modes, hierarchical Map-Reduce chunking, BYOK security architecture, SSRF defense, and dark/light UI.
- [x] **v1.1 — Personalization & Export**: High-fidelity ReportLab PDF export with custom branding and numbered pagination; user-defined custom prompt directives in Settings.
- [x] **v1.2 — Multi-URL Batch Summarization**: Tabbed batch input with dynamic rows, independent validation, duplicate detection, resilient concurrent worker execution, and combined batch PDF export.
- [x] **v1.3 — Comparative Research & Notion Integration**: Side-by-side dual-article comparison with consensus points, contradictions/disagreements, differing perspectives; official Notion REST API export.
- [x] **v1.4 — Local Knowledge Retrieval & Semantic Search**: Natural-language vector embedding retrieval across saved summaries, embedded binary SQLite float vector storage, deterministic SHA-256 caching.
- [x] **v1.5 — Hosted BYOK & Deployment-Aware Storage**: Public Vercel serverless deployment support, strictly in-memory active tab visitor keys, zero developer credit drainage, complete multi-user isolation via browser IndexedDB (`WebBriefDB`).
- [x] **v1.6 — Gemini API Migration & Security Hardening**: Google Gen AI SDK migration (`gemini-3.8-flash`), verified model catalogue, strict timeouts, SSRF connection-level IP pinning, streaming size limits, and Fernet Redis vault.
- [x] **v1.7.0 — Unified SPA & Production Stabilization**: Single-page dashboard consolidation, mobile-first responsive layout (320px–4K), full settings retirement, verified SQLite persistence, and 236-test stabilization suite.

---

## 23. Contributing Guidelines

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

## 24. License

Distributed under the **MIT License**. See `LICENSE` for more information.

---

© 2026 WebBrief AI — Built with security, performance, and transparency.
