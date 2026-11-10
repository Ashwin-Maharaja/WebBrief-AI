"""
WebBrief AI — Google Gemini Provider Package.
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
from .gemini import GeminiProvider
from .router import AIRouter, RouterResult
from .catalogue import (
    PROVIDER_CATALOGUE,
    get_models_for_provider,
    get_default_model,
    get_allowed_models,
    is_valid_model,
    validate_model_id,
)

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
    "GeminiProvider",
    "AIRouter",
    "RouterResult",
    "PROVIDER_CATALOGUE",
    "get_models_for_provider",
    "get_default_model",
    "get_allowed_models",
    "is_valid_model",
    "validate_model_id",
]
