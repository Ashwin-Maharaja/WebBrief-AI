# WebBrief AI

> **Production Release — v1.7.0**  
> An intelligent, multi-mode website summarizer powered by a modular multi-provider AI architecture (Cloudflare Workers AI & GroqCloud) and Flask. Converts any webpage—from standard blogs to JavaScript-rendered Single Page Applications—into structured, high-value briefs in seconds with Hardened Security & Reliability, Hosted BYOK & Deployment-Aware Storage, Local Knowledge Retrieval & Semantic Search, Comparative Research, Notion Integration, Multi-URL Batch Summarization, PDF document export, personalized prompt directives, automatic provider failover routing, and **Resumable Summarization with Atomic Checkpoint Recovery**.

---

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [What's New in v1.7.0](#2-whats-new-in-v170)
3. [Features](#3-features)
4. [Resumable Summarization & Checkpoint Architecture](#4-resumable-summarization--checkpoint-architecture)
5. [Multi-Provider AI Architecture & Routing](#5-multi-provider-ai-architecture--routing)
6. [Deployment Models & Storage Adaptations](#6-deployment-models--storage-adaptations)
7. [Vector Database Architecture & Semantic Search](#7-vector-database-architecture--semantic-search)
8. [Visual Previews](#8-visual-previews)
9. [Technology Stack](#9-technology-stack)
10. [Repository Structure](#10-repository-structure)
11. [Installation Instructions](#11-installation-instructions)
12. [Virtual Environment Setup](#12-virtual-environment-setup)
13. [Dependency Installation](#13-dependency-installation)
14. [First-Run Setup Wizard](#14-first-run-setup-wizard)
15. [Provider Credential Configuration](#15-provider-credential-configuration)
16. [BYOK Architecture & Security Model](#16-byok-architecture--security-model)
17. [Running Locally](#17-running-locally)
18. [Vercel & Cloud Deployment](#18-vercel--cloud-deployment)
19. [Environment Variables Reference](#19-environment-variables-reference)
20. [Troubleshooting](#20-troubleshooting)
21. [Known Limitations](#21-known-limitations)
22. [Completed Roadmap & Feature Milestones](#22-completed-roadmap--feature-milestones)
23. [Contributing Guidelines](#23-contributing-guidelines)
24. [License](#24-license)

---

## 1. Project Overview

WebBrief AI simplifies reading long articles, technical papers, corporate releases, and dynamic websites by distilling raw web content into accurate, five-section structured summaries. 

Built with enterprise-grade SSRF defense, deployment-aware storage, and a **Bring Your Own Key (BYOK)** security architecture, WebBrief AI guarantees that personal API credentials remain private, stored locally in `.env` for self-hosted instances or strictly in active browser memory for public Vercel visitors—never shared between users, never logged, and never exposing developer API credits.

In **v1.7.0**, WebBrief AI introduces **Resumable Summarization & Checkpoint Recovery**, preserving completed chunk summaries when interruptions occur (network drops, rate limits, timeouts, server restarts, or browser refreshes) and resuming unfinished work without repeating successful chunks.

---

## 2. What's New in v1.7.0

- **Resumable Map-Reduce Engine**: Every individual chunk in the Map phase is persistently checkpointed immediately upon generation. If an error or interruption happens, completed chunks are preserved and reused.
- **Local SQLite Job & Checkpoint Persistence**: Introduces `summarization_jobs`, `job_chunks`, and `job_events` tables in `webbrief_history.db` with transactional ACID writes, foreign key cascades, and WAL concurrency.
- **Stale Server Restart Auto-Recovery**: On server startup, running jobs stuck in active execution states are automatically detected and transitioned to `Interrupted`, preserving all successful chunk checkpoints and allowing one-click resumption.
- **Hosted Vercel Bounded Orchestration**: The browser client drives chunk-by-chunk processing via `/api/jobs/<id>/chunk/<index>`, persisting checkpoints into browser IndexedDB (`WebBriefDB`) and synthesizing results without hitting serverless execution limits.
- **Dedicated Summarization Jobs Interface (`/jobs`)**: Real-time progress monitoring, chunk completion counters (`X / Y Chunks Completed`), status badges (`Processing`, `Interrupted`, `Paused`, `Completed`, `Failed`), recovery alert messages, and controls (`Resume`, `Pause`, `Cancel`, `View Summary`, `Delete`).
- **Configuration Snapshot & Consistency Enforcement**: Jobs snapshot their summarization mode, prompt directives, and provider settings at inception. If settings change before resumption, WebBrief AI prompts the user to prevent inconsistent output contamination.
- **Transient Hosted Credential Isolation**: In hosted mode, BYOK credentials remain strictly in JavaScript RAM. If a tab refresh occurs, the user is safely prompted to re-enter credentials before resuming unfinished chunks.

---

## 3. Features

- **Resumable Summarization & Checkpoint Recovery (v1.7.0)**:
  - Atomic persistence after every successful chunk.
  - One-click resume for interrupted or paused jobs.
  - Safe state machine: `Queued`, `Extracting`, `Chunking`, `Processing`, `Recovering`, `Paused`, `Interrupted`, `Synthesizing`, `Completed`, `Failed`, `Cancelled`.
  - Zero duplicate chunk queries upon resumption.
- **Multi-Provider BYOK & Fallback Routing (v1.6.0)**:
  - Choose between Cloudflare Workers AI and GroqCloud as primary provider.
  - Enable optional fallback to protect against rate limits or temporary outages.
  - Multi-provider transparency badging on all result cards.
- **Enterprise Security & Reliability Hardening**:
  - **Zero Hardcoded Secrets & Production Verification**: Eliminates static fallbacks. In production environments, requires an explicit 32-byte `FLASK_SECRET_KEY`; in local development, generates an ephemeral, cryptographically secure 32-byte key (`secrets.token_hex(32)`).
  - **Defensive HTTP Security Headers**: Injects `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`, and a strict Content Security Policy (`CSP`) into every response.
  - **Origin & Anti-CSRF Protection**: Enforces Origin and Referer validation on all mutating HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`) to block cross-origin request forging.
  - **Enhanced SSRF & Redirect-Hopping Guard**: Upgrades scraper SSRF defenses with an iterative redirect loop (`allow_redirects=False`, max 5 hops) verifying destination IPs against private, loopback, and cloud metadata ranges before connecting.
  - **Warm Serverless Multi-Tenant Isolation**: Bypasses server-side SQLite writes and reads in hosted serverless mode (Vercel), keeping summaries in client IndexedDB.
  - **DOM-Based XSS Sanitization**: Rigorously escapes all dynamic values rendered in the browser DOM across Single, Batch, Compare, Jobs, and Search views.
- **Local Knowledge Retrieval & Semantic Search**:
  - **Natural-Language Vector Search**: Query your personal summary library with questions without relying on exact keyword matches.
  - **Embedded SQLite Vector Storage**: Vectors are packed as binary float blobs (`struct.pack`) directly in `webbrief_history.db`.
  - **SHA-256 Content-Hash Caching**: Computes deterministic hashes of summary texts to eliminate redundant embedding generation.
- **Comparative Research & Dual-Source Synthesis**: Side-by-side dual-article comparison with consensus points, contradictions/disagreements, differing perspectives, and structured claims table.
- **Notion Integration & Export**: Connects via Notion Internal Integration Secrets to export structured briefs directly into native Notion pages.
- **Multi-URL Batch Summarization**: Summarize up to 10 URLs concurrently with isolated worker execution, live progress counters, and combined batch PDF export.
- **Dual-Engine Scraping with Dynamic Fallback**: Static HTTP scraping via `requests` + `BeautifulSoup4` with automatic Playwright headless Chromium fallback for JavaScript SPAs.

---

## 4. Resumable Summarization & Checkpoint Architecture

```
                          [ Incoming Summarization Job ]
                                         |
                                         v
                            +--------------------------+
                            | Create Job Record in DB  |
                            | (Snapshot Configuration) |
                            +--------------------------+
                                         |
                                         v
                            +--------------------------+
                            | Divide Content to Chunks |
                            | (Register in job_chunks) |
                            +--------------------------+
                                         |
                     +-------------------+-------------------+
                     |                                       |
           [ Local Self-Hosted ]                  [ Hosted Serverless ]
                     |                                       |
                     v                                       v
        +--------------------------+            +--------------------------+
        | Sequential Processing    |            | Browser-Driven Requests  |
        | - Call AIRouter per chunk|            | - Fetch /chunk/<idx>     |
        | - Write to SQLite        |            | - Write to IndexedDB     |
        +--------------------------+            +--------------------------+
                     |                                       |
                     +-------------------+-------------------+
                                         |
                         [ Any Interruption Occurs? ]
                           /                       \
                       (Yes)                       (No)
                         /                           \
                        v                             v
            +-----------------------+     +--------------------------+
            | Update Status to      |     | Transition to            |
            | 'Interrupted'         |     | 'Synthesizing'           |
            | Completed Chunks      |     +--------------------------+
            | Remain 100% Preserved |                 |
            +-----------------------+                 v
                        |                 +--------------------------+
                        v                 | Final Reduce Synthesis   |
            [ User Clicks 'Resume' ]      | Link to History Record   |
            - Load existing checkpoints   +--------------------------+
            - Skip completed chunks                   |
            - Resume unfinished chunks                v
                        |                 +--------------------------+
                        +---------------->| Job Status: 'Completed'  |
                                          +--------------------------+
```

---

## 5. Multi-Provider AI Architecture & Routing

| Feature | Cloudflare Workers AI | GroqCloud |
| :--- | :--- | :--- |
| **Role** | Primary Recommended Provider | Optional Fallback / Alternative Primary |
| **Credentials Required** | Cloudflare Account ID + API Token | Groq API Key (`gsk_...`) |
| **Inference Engine** | Cloudflare Global Edge Workers AI | Groq LPU™ Inference Engine |
| **Verified Models** | `@cf/meta/llama-3.1-8b-instruct`<br>`@cf/mistralai/mistral-7b-instruct-v0.2`<br>`@cf/qwen/qwen1.5-7b-chat`<br>`@cf/meta/llama-3-8b-instruct` | `llama-3.3-70b-versatile`<br>`llama-3.1-8b-instant`<br>`mixtral-8x7b-32768`<br>`gemma2-9b-it` |
| **Failover Support** | Automatic fallback on 429, quota, 5xx, or timeouts | Automatic fallback on 429, quota, 5xx, or timeouts |

---

## 6. Deployment Models & Storage Adaptations

| Feature | Local Self-Hosted Mode | Public Hosted Vercel Mode |
| :--- | :--- | :--- |
| **Target Runtime** | Local machine, Docker, VPS | Vercel Serverless Functions |
| **Job & Checkpoint Storage** | SQLite tables (`summarization_jobs`, `job_chunks`) | Browser IndexedDB (`jobs`, `job_chunks` stores) |
| **Job Execution Engine** | Local sequential worker with checkpointing | Client-side bounded chunk orchestration |
| **Credential Persistence** | Saved in local `.env` file | Transient JavaScript RAM (cleared on refresh) |
| **Interruption Recovery** | Survives server restarts and crashes | Survives browser refresh (prompts credentials) |
| **Summary History** | SQLite (`webbrief_history.db`) | Client-side IndexedDB (`WebBriefDB`) |
| **Multi-Tenant Isolation** | Single-user machine boundary | 100% private per visitor (zero cross-user sharing) |

---

## 7. Vector Database Architecture & Semantic Search

WebBrief AI uses embedded vector storage directly inside SQLite (`webbrief_history.db`):

- **Zero Additional Daemons**: Embeddings are packed as binary float blobs (`struct.pack`) alongside article metadata.
- **Embeddings Provider**: Cloudflare Workers AI `@cf/baai/bge-base-en-v1.5` (768 dimensions), with deterministic feature-vector fallback for Groq-only mode.
- **SHA-256 Content Caching**: Embeddings are indexed by content hash. Summaries are never re-embedded unless their text changes.
- **Search Latency**: Pure Python dot product executes in **~0.03ms** per vector comparison.

---

## 8. Visual Previews

### Resumable Jobs Dashboard (`/jobs`)
```
+-------------------------------------------------------------------------+
| [O] WebBrief AI  v1.7.0     [🏠 Home]  [⚡ Jobs]  [📚 History]  [⚙️ Settings]|
+-------------------------------------------------------------------------+
| ⚡ Summarization Jobs                                  [🔄 Refresh Jobs] |
| Resumable summarization engine with atomic chunk checkpoint recovery.   |
|                                                                         |
| +---------------------------------------------------------------------+ |
| | Quantum Computing Architecture Breakthrough       [Detailed] [INTERRUPTED]|
| | https://example.com/quantum-deep-dive                                |
| |                                                                     | |
| | [==========================================......] 86%              | |
| | 6 / 7 Chunks Completed                                              | |
| |                                                                     | |
| | 🛡️ Cloudflare encountered a temporary rate limit (HTTP 429). WebBrief | |
| | AI is recovering unfinished work. 6 completed chunks preserved.     | |
| |                                                                     | |
| |                                [▶️ Resume]  [⏹️ Cancel]  [🗑️ Delete]   | |
| +---------------------------------------------------------------------+ |
+-------------------------------------------------------------------------+
```

---

## 9. Technology Stack

- **Backend Runtime**: Python 3.10+
- **Web Framework**: Flask 3.1+ (Jinja2 templates, signed `HttpOnly` sessions)
- **AI Engine**: Modular `AIRouter` with REST clients for **Cloudflare Workers AI** & **GroqCloud**
- **Checkpoint Persistence**: SQLite3 (`summarization_jobs`, `job_chunks`, `job_events`) & IndexedDB
- **Web Scraping**:
  - Static: `requests` + `BeautifulSoup4`
  - Dynamic: `playwright` (Chromium headless with request routing)
- **Frontend**: Vanilla JavaScript (ES6+), CSS Custom Properties (zero heavy frontend frameworks)
- **Production Server**: Gunicorn WSGI (Linux/Docker) or Vercel Python runtime

---

## 10. Repository Structure

```
WebBrief AI/
├── api/
│   └── index.py            # Vercel serverless function entrypoint bridge
├── app.py                  # Core Flask application, routing, and controller logic
├── batch_processor.py      # Multi-URL batch processor, concurrency control, progress callback
├── byok.py                 # Multi-provider credential validation, safe .env persistence
├── chunker.py              # Paragraph-aware chunking and token overlap management
├── comparative_analyzer.py # Dual-source comparative research, claims matrix & fact synthesis
├── history.py              # Local SQLite summary & comparison persistence layer (CRUD)
├── job_manager.py          # Resumable summarization job & checkpoint recovery manager (v1.7.0)
├── notion_service.py       # Official Notion REST API client, block parser & page exporter
├── pdf_exporter.py         # ReportLab multi-page PDF generation engine with NumberedCanvas
├── scraper.py              # Dual static/dynamic scraper, SSRF firewall, Playwright engine
├── summarizer.py           # Multi-mode prompt engine, Map-Reduce pipeline, checkpoint recovery
├── vector_store.py         # Local SQLite vector database engine, cosine search & hash cache
├── services/
│   └── providers/
│       ├── __init__.py     # Module initialization and exports
│       ├── base.py         # BaseProvider abstract base class definition
│       ├── cloudflare.py   # Cloudflare Workers AI REST adapter & embeddings
│       ├── groq.py         # GroqCloud REST adapter
│       ├── router.py       # AIRouter automatic routing & fallback engine
│       ├── catalogue.py    # Verified model catalogue & free-tier mappings
│       └── exceptions.py   # Normalized provider exception hierarchy
├── static/
│   ├── storage_adapter.js  # Client-side adapter for in-memory BYOK, IndexedDB jobs & history
│   └── style.css           # Responsive stylesheet with light/dark theme variables
├── templates/
│   ├── index.html          # Primary application dashboard, mode selector, and result cards
│   ├── jobs.html           # Dedicated resumable jobs & checkpoint recovery view (v1.7.0)
│   ├── history.html        # Summary library view (search, reopen, delete)
│   ├── result.html         # Standalone result view with provider transparency badges
│   ├── setup.html          # First-run BYOK onboarding wizard (Cloudflare & Groq)
│   └── settings.html       # Multi-provider configuration, model selection, danger zone
├── .env.example            # Multi-provider environment variable template
├── .gitignore              # Git ignore rules (protects credentials and local DBs)
├── requirements.txt        # Production Python dependencies (includes Gunicorn & Playwright)
├── vercel.json             # Vercel Serverless deployment configuration
└── README.md               # Comprehensive documentation
```

---

## 11. Installation Instructions

### Prerequisites
- Python 3.10, 3.11, or 3.12 installed on your machine.
- Git installed.
- At least one AI provider account:
  - **Cloudflare Account ID & API Token** (Cloudflare Dashboard > Workers AI), OR
  - **Groq API Key** (https://console.groq.com/keys).

### Clone the Repository
```bash
git clone https://github.com/your-username/webbrief-ai.git
cd webbrief-ai
```

---

## 12. Virtual Environment Setup

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

---

## 13. Dependency Installation

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

## 14. First-Run Setup Wizard

1. Start the Flask development server:
   ```bash
   python app.py
   ```
2. Navigate to `http://127.0.0.1:5000` in your web browser.
3. If no AI provider credentials are configured, WebBrief AI automatically redirects you to `/setup`.
4. Choose your primary provider (**Cloudflare Workers AI** or **GroqCloud**) and select your model.
5. Provide your credentials and click **Validate & Save Configuration**.

---

## 15. Provider Credential Configuration

You can configure your credentials via the interactive web UI or directly in `.env`:

### Option A: Cloudflare Workers AI (Recommended)
```ini
PRIMARY_PROVIDER=cloudflare
CLOUDFLARE_ACCOUNT_ID=your_account_id_here
CLOUDFLARE_API_TOKEN=your_token_here
CLOUDFLARE_DEFAULT_MODEL=@cf/meta/llama-3.1-8b-instruct
```

### Option B: GroqCloud
```ini
PRIMARY_PROVIDER=groq
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_DEFAULT_MODEL=llama-3.3-70b-versatile
```

### Optional Fallback Configuration
```ini
FALLBACK_ENABLED=true
FALLBACK_PROVIDER=groq
FALLBACK_MODEL=llama-3.3-70b-versatile
```

---

## 16. BYOK Architecture & Security Model

WebBrief AI strictly enforces a **Zero-Credential Exposure** architecture:

- **Isolated Storage**: Saved credentials reside exclusively in your local `.env` or in active browser RAM on hosted Vercel deployments.
- **Checkpoint Cleanliness**: Checkpoint data in `summarization_jobs` and IndexedDB never stores API keys or tokens.
- **No Client Leakage**: Sensitive tokens and keys are never rendered in frontend templates or stored in client disk on hosted deployments.
- **Automatic Sanitization**: Error messages and logs automatically mask Groq keys (`gsk_...`), Cloudflare tokens, and account IDs.
- **SSRF Defense**: DNS pre-resolution restricts scraper requests to public IP addresses and blocks loopback/metadata endpoints across redirects.

---

## 17. Running Locally

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

## 18. Vercel & Cloud Deployment

WebBrief AI is configured for one-click deployment on **Vercel Serverless Functions**.

1. Push your repository to GitHub.
2. In the Vercel Dashboard, import the repository.
3. Under **Environment Variables**, configure:
   - `FLASK_SECRET_KEY`: A random 32-character secret string.
   - `SESSION_COOKIE_SECURE`: `true`
4. Click **Deploy**.

---

## 19. Environment Variables Reference

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `PRIMARY_PROVIDER` | Required | `cloudflare` | Primary AI provider (`cloudflare` or `groq`) |
| `CLOUDFLARE_ACCOUNT_ID` | Conditional | None | Cloudflare Account ID |
| `CLOUDFLARE_API_TOKEN` | Conditional | None | Cloudflare API Token with Workers AI permissions |
| `CLOUDFLARE_DEFAULT_MODEL`| Optional | `@cf/meta/llama-3.1-8b-instruct` | Default Cloudflare model identifier |
| `GROQ_API_KEY` | Conditional | None | Groq API Key |
| `GROQ_DEFAULT_MODEL` | Optional | `llama-3.3-70b-versatile` | Default Groq model identifier |
| `FALLBACK_ENABLED` | Optional | `false` | Enable automatic failover routing (`true` or `false`) |
| `FALLBACK_PROVIDER` | Conditional | None | Fallback provider (`cloudflare` or `groq`) |
| `FALLBACK_MODEL` | Conditional | None | Fallback model identifier |
| `NOTION_API_KEY` | Optional | None | Notion Internal Integration Secret for exporting briefs |
| `FLASK_SECRET_KEY` | Required in Prod | Ephemeral Dev Key | 32-byte secret key used to sign session cookies |
| `FLASK_DEBUG` | Optional | `0` | Set `1` to enable Flask debugger (development only) |
| `PORT` | Optional | `5000` | Local web server listening port |
| `HOST` | Optional | `127.0.0.1` | Local web server binding host |
| `SESSION_COOKIE_SECURE` | Optional | `false` (local) / `true` (prod) | Sets Secure flag on session cookies for HTTPS |
| `MAX_CONTENT_LENGTH` | Optional | `2097152` (2MB) | Maximum allowed incoming request body size in bytes |

---

## 20. Troubleshooting

### Issue: "Job interrupted: rate limit or network error"
- Visit the `/jobs` tab in WebBrief AI. You will see your job with preserved chunks and a recovery alert. Click **Resume** to continue processing unfinished chunks.

### Issue: "Configuration differs from initial snapshot"
- If you modified provider or prompt directives after a job was interrupted, WebBrief AI prevents silent output contamination. You can choose to resume with the original configuration or restart with the new settings.

### Issue: "Provider authentication failed"
- Verify Cloudflare Account ID & API Token permissions (`Workers AI: Read/Edit`), or check that your Groq key is active at https://console.groq.com/keys.

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
- [x] **v1.5.1 — Security & Reliability Hardening**: Comprehensive audit and remediation across 10 security vectors (SEC-01 through SEC-10).
- [x] **v1.6.0 — Multi-Provider AI Integration**: Modular provider architecture (`services/providers/`); primary provider Cloudflare Workers AI; optional fallback provider GroqCloud; automatic provider routing (`AIRouter`) with bounded failover.
- [x] **v1.7.0 — Resumable Summarization & Checkpoint Recovery**: Atomic chunk checkpoint persistence; local SQLite tables (`summarization_jobs`, `job_chunks`, `job_events`); stale process startup auto-recovery; browser IndexedDB bounded orchestration; dedicated Summarization Jobs interface (`/jobs`); configuration consistency checks.

---

## 23. Contributing Guidelines

Contributions are welcome! Please follow these steps:

1. **Fork the Repository**.
2. **Create a Feature Branch**: `git checkout -b feature/amazing-feature`.
3. **Run Test Suites**:
   ```bash
   .\venv\Scripts\python.exe scratch/test_v170_resumable.py
   .\venv\Scripts\python.exe scratch/test_v160_providers.py
   ```
4. **Commit Your Changes**: Follow conventional commits (e.g., `feat: ...`, `fix: ...`).
5. **Open a Pull Request**.

---

## 24. License

Distributed under the **MIT License**. See `LICENSE` for more information.

---

*(c) 2026 WebBrief AI — Built with security, performance, and transparency.*
