# WebBrief AI v1.6.2 — Security & Stability

This patch preserves the original anonymous hosted browser-memory BYOK flow by default.

## Optional Redis encrypted credential vault

Set both `REDIS_URL` (TLS-enabled managed Redis) and `CREDENTIAL_ENCRYPTION_KEY` (Fernet key). Generate a key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Set a stable `FLASK_SECRET_KEY` too.

When enabled, Flask-Session uses Redis instead of signed cookie session storage. The new `POST /api/credentials` endpoint validates and encrypts provider credentials into Redis for 30 minutes of inactivity, up to an 8-hour absolute lifetime. `GET /api/credentials/status` returns metadata only; `DELETE /api/credentials` deletes the stored keys. All mutations require the existing CSRF token.

The hosted homepage automatically switches to the encrypted vault when both Redis and encryption settings are configured. It saves verified credentials server-side, removes keys from subsequent provider request payloads, and restores anonymous session status after reload. Without vault configuration, the original browser-memory-only flow remains unchanged. Other pages, including the standalone Settings/Setup flows, have not been fully migrated to this vault flow; use the homepage BYOK panel for hosted vault sessions.

## Important deployment notes

- Do not commit actual `.env` or encryption keys.
- Configure Redis and Fernet secrets together; partial configuration fails at startup in production.
- Existing browser-memory mode requires no Redis service and continues to function.
- Do not treat a successful build as a successful live test; validate all hosted routes before rollout.
- Redis TTL is sliding, but the absolute 8-hour deadline is enforced on reads.

## Release verification

- `python -m unittest discover -s tests -p test_credential_vault.py` (passed)
- `node --check static/storage_adapter.js` (passed)
- `python -m compileall -q .` (passed)
- Full Flask integration tests were not executed in the build environment because Flask and Redis client dependencies were unavailable.
- No live Vercel or Redis deployment was performed. Test with a staging deployment before promoting to production.

## Known limitations

- A complete Redis-backed rate limiter is not included in this release.
- The existing scraper has URL/DNS checks but should receive additional DNS rebinding and response-size hardening in a subsequent release.
- The original hosted browser-memory flow remains the default unless the vault is configured.
