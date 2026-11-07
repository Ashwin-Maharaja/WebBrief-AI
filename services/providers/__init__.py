"""
WebBrief AI Multi-Provider AI Architecture Package (v1.6.0).
"""

from .exceptions import (
    ProviderError,
    ProviderAuthError,
    ProviderInvalidModelError,
    ProviderBadRequestError,
    ProviderRateLimitError,
    ProviderQuotaError,
    ProviderUnavailableError,
    ProviderTimeoutError,
)
from .base import BaseProvider
from .cloudflare import CloudflareProvider
from .groq import GroqProvider
from .router import AIRouter
from .catalogue import PROVIDER_CATALOGUE, get_models_for_provider, get_default_model

__all__ = [
    "ProviderError",
    "ProviderAuthError",
    "ProviderInvalidModelError",
    "ProviderBadRequestError",
    "ProviderRateLimitError",
    "ProviderQuotaError",
    "ProviderUnavailableError",
    "ProviderTimeoutError",
    "BaseProvider",
    "CloudflareProvider",
    "GroqProvider",
    "AIRouter",
    "PROVIDER_CATALOGUE",
    "get_models_for_provider",
    "get_default_model",
]
