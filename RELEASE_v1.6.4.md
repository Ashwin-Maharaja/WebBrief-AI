# WebBrief AI v1.6.4 — Stability, Security & Reliability Patch

## Overview

WebBrief AI v1.6.4 is a focused security, reliability, and stability maintenance patch building upon the v1.6.3 foundation. It resolves edge cases in serverless secret handling, Playwright resource accounting and SSRF subresource inspection, Redis vault persistence confirmation, Notion integration method contracts, frontend DOM injection defense, and serverless rate-limiting synchronization.

---

## Key Remediations in v1.6.4

### 1. Flask Production Secret-Key Hardening (`app.py`)
- **Strict Production Verification**: In production serverless/container environments (`VERCEL`, `FLASK_ENV=production`, etc.), WebBrief AI now strictly requires an explicitly configured, high-entropy `FLASK_SECRET_KEY` (minimum 32 characters / 16 bytes).
- **Safe Startup Failure**: Startup aborts with a clear `RuntimeError` if the secret key is missing, weak, or matches known placeholders (e.g. `your_secret_key_here`, `change-me`, `development-key`). Ephemeral keys are rejected in production to prevent session invalidation across cold starts and multi-instance Lambda environments.
- **Development Convenience Preserved**: In local development, an unguessable 32-byte cryptographic secret (`secrets.token_hex(32)`) is generated automatically with a console warning.
- **Zero Information Leakage**: Secrets are never derived from deployment IDs or commit hashes, and are never emitted in logs or error traces.

### 2. Playwright SSRF Defense & Response-Size Enforcement (`scraper.py`)
- **True Network Body Accounting**: Network response sizes are measured by fetching response bodies directly in route interception, enforcing the 5 MB limit regardless of whether `Content-Length` is present, omitted, chunked, or inaccurate.
- **Valid Abort Error Codes**: Replaced non-standard abort codes with standard Playwright abort codes (`"aborted"` and `"blockedbyclient"`).
- **Subresource & Redirect SSRF Hardening**: Every intercepted subresource and redirect destination is validated against `resolve_and_validate_safe_url()`. Any requests to loopback, private RFC 1918 subnets, CGNAT (`100.64.0.0/10`), cloud metadata endpoints (`169.254.169.254`, `100.100.100.200`, `fd00:ec2::254`), non-standard ports, or unsupported schemes (`file://`, `data:`, etc.) are aborted immediately.
- **Resource Budget & Process Teardown**: Enforces a strict 50-subrequest budget and 15-second navigation timeout. Browser processes and contexts are guaranteed to terminate cleanly in `finally` blocks.

### 3. Redis Credential Vault Reliability (`app.py`)
- **Atomic Persistence Check in `/api/save-config`**: Checked the return value of `_vault.save()` during hosted configuration saving. If Redis fails or is unreachable, the endpoint returns an HTTP 503 error rather than falsely reporting success.
- **Frontend Input Preservation**: The user's input fields in Settings and Setup forms are preserved if the backend fails to persist credentials, allowing retry without losing entered keys.

### 4. Notion Integration Contract & Hosted Security (`notion_service.py`, `app.py`)
- **Elimination of Session Cookie Storage**: Completely removed Notion token storage in client-side Flask session cookies. In hosted mode, persistent connection strictly requires the encrypted Redis vault; otherwise users supply tokens per-request in browser memory.
- **Signature & Return Contract Aligned**: Updated `save_notion_key_to_env()` to return a consistent `Tuple[bool, str]` contract across all execution paths.
- **Hosted vs Local Separation**: In hosted serverless mode (Vercel), Notion credentials are never written to the read-only/ephemeral filesystem. In local self-hosted mode, `NOTION_API_KEY` persists safely to `.env`.
- **Confidentiality Guaranteed**: Notion integration tokens are never returned in JSON payloads, exposed in client-readable session cookies, or logged.

### 5. Frontend DOM Injection Hardening (`templates/index.html`)
- **Safe Notion Option Construction**: Replaced raw string template interpolation in the Notion page selector with standard DOM APIs (`document.createElement("option")`, `.value`, `.textContent`), preventing HTML breakout and XSS via malicious page titles or IDs.
- **Safe Batch URL Input Rendering**: Removed raw string interpolation `${val}` from batch URL row creation; assigned `.value = val` directly to the created input element.
- **Attribute Breakout Elimination**: Replaced inline JavaScript string interpolation in semantic search and batch result cards with data attributes and event listeners.

### 6. Rate Limiting and Serverless Reliability (`app.py`)
- **Shared Redis Rate Limiting**: When Redis is configured (`_redis` active), rate limits across sensitive endpoints (`/summarize`, `/batch/summarize`, `/compare`, `/api/validate-key`, `/api/credentials`) are synchronized across serverless instances using atomic Redis pipelines (`INCR` + `EXPIRE`).
- **Resilient Fallback**: If Redis is not configured or encounters network errors, the application seamlessly falls back to the thread-safe `InMemoryRateLimiter` without breaking ordinary BYOK workflows.
- **Process-Local Limitations Documented**: Honestly documents that in-memory rate limiting is process-local and provides burst protection per instance, while Redis provides globally coordinated abuse protection.

---

## Release Verification

- Full test suite: **73 tests executed across all test suites, 0 failures, 0 errors**.
- Tests cover:
  - Secret key production vs development resolution and production configuration failure blocking.
  - SSRF protection (CGNAT, loopback, private ranges, cloud metadata, mixed-IP DNS rebinding).
  - Scraper limits (static & Playwright 5 MB bounds, missing/inaccurate Content-Length headers, subrequest limits, process teardown).
  - Redis vault lifecycle (encryption, expiration, session isolation, failure recovery).
  - Rate limiting (in-memory sliding window, Redis atomic pipeline, graceful fallback).
  - Notion integration (tuple contract, hosted Redis vault requirement, session cookie exclusion, confidentiality).
  - Frontend DOM injection resistance.
  - Multi-provider AI summarization & fallback routing.

---

## Known Operating Limitations

1. **Anti-Bot / Captcha Walls**: Sites protected by Cloudflare Turnstile, reCAPTCHA, or Cloudflare Under Attack mode cannot be scraped without human interactive challenge solving. The scraper detects these and returns an actionable HTTP 403/429 error.
2. **Paywalls & Subscriber Access**: WebBrief AI respects site paywalls and access restrictions. Content requiring active user login or subscription cookies cannot be summarized.
3. **Playwright Socket Pinning Boundary**: The primary `requests` engine uses `PinnedIPAdapter` to achieve socket-level IP pinning. The fallback Playwright engine enforces DNS resolution, IP safety filtering, and route interception on every subrequest, but relies on Chromium's internal Network Service for low-level socket connections.
4. **Serverless Shared State**: For distributed rate limiting and persistent encrypted credential vaulting across serverless Lambdas, an external Redis instance (e.g. Upstash Redis) is required. Without Redis, WebBrief AI operates in standard ephemeral in-memory BYOK mode.
