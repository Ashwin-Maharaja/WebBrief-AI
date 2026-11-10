"""
WebBrief AI — Normalized Provider Exceptions.
Provides standard exception classes for AI provider adapters (Google Gemini).
"""

class ProviderError(Exception):
    """Base exception for all AI provider operations."""
    def __init__(self, message: str, provider: str = "", model: str = "", status_code: int = None):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.status_code = status_code

    def __str__(self):
        ctx = []
        if self.provider:
            ctx.append(f"Provider: {self.provider}")
        if self.model:
            ctx.append(f"Model: {self.model}")
        if self.status_code:
            ctx.append(f"Status: {self.status_code}")
        prefix = f"[{', '.join(ctx)}] " if ctx else ""
        return f"{prefix}{self.message}"


# -------------------------------------------------------------
# Client-Side / Configuration Exceptions
# -------------------------------------------------------------
class ProviderAuthError(ProviderError):
    """Authentication or authorization failure (HTTP 401, 403, invalid key)."""
    pass


class ProviderInvalidModelError(ProviderError):
    """Model not found, unverified, deprecated, or unavailable to user account."""
    pass


class ProviderBadRequestError(ProviderError):
    """Malformed request payload, invalid parameters, or content-policy refusal."""
    pass


# -------------------------------------------------------------
# Runtime / Transient Service Exceptions
# -------------------------------------------------------------
class ProviderRateLimitError(ProviderError):
    """Provider rate limit reached (HTTP 429)."""
    pass


class ProviderQuotaError(ProviderRateLimitError):
    """Provider account credit or quota exhausted."""
    pass


class ProviderUnavailableError(ProviderError):
    """Provider service temporary outage or error (HTTP 500, 502, 503, 504)."""
    pass


class ProviderTimeoutError(ProviderError):
    """Network connection or inference response timeout."""
    pass
