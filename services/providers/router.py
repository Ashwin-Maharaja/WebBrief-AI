"""
WebBrief AI — Provider Router.
Coordinates Google Gemini execution, model resolution, and error classification.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Dict, Any, List, Optional, Tuple

from .base import BaseProvider
from .gemini import GeminiProvider
from .catalogue import validate_model_id
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

logger = logging.getLogger(__name__)


@dataclass
class RouterResult:
    """Encapsulates the generated AI response along with routing metadata."""
    content: Any  # Raw text or parsed dictionary
    provider_id: str
    provider_name: str
    model: str
    switched_to_fallback: bool = False
    fallback_reason: Optional[str] = None


class AIRouter:
    """
    Coordinates execution for Google Gemini Developer API.
    Provides standard execution methods and structured results matching WebBrief AI's architecture.
    """

    def __init__(
        self,
        primary_provider: str = "gemini",
        primary_credentials: Optional[Dict[str, Any]] = None,
        primary_model: Optional[str] = None,
    ):
        if isinstance(primary_provider, BaseProvider):
            self._primary_instance = primary_provider
            self.primary_provider_id = primary_provider.provider_id
            self.primary_credentials = {}
            self.primary_model = primary_provider.active_model
        else:
            self.primary_provider_id = "gemini"
            self.primary_credentials = primary_credentials or {}
            self.primary_model = primary_model
            self._primary_instance = None

    def _instantiate_provider(self, provider_id: str, credentials: Dict[str, Any], model: Optional[str]) -> BaseProvider:
        """Instantiates the Gemini provider adapter from credentials."""
        api_key = (
            credentials.get("api_key")
            or credentials.get("gemini_api_key")
            or credentials.get("GEMINI_API_KEY")
            or ""
        )
        return GeminiProvider(api_key=api_key, model=model)

    @property
    def primary(self) -> BaseProvider:
        if self._primary_instance is None:
            self._primary_instance = self._instantiate_provider(
                self.primary_provider_id, self.primary_credentials, self.primary_model
            )
        return self._primary_instance

    def test_connection(self) -> Tuple[bool, str]:
        """Tests the active Gemini provider connection."""
        return self.primary.test_connection()

    def generate_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        timeout: int = 45,
    ) -> RouterResult:
        """Generates raw text completion via Gemini."""
        active_model = validate_model_id(
            model or self.primary_model or self.primary.active_model,
            self.primary.provider_id,
        )
        content = self.primary.generate_completion(
            messages=messages,
            model=active_model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            timeout=timeout,
        )
        return RouterResult(
            content=content,
            provider_id=self.primary.provider_id,
            provider_name=self.primary.provider_name,
            model=active_model,
            switched_to_fallback=False,
            fallback_reason=None,
        )

    def generate_structured_json(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        timeout: int = 45,
    ) -> RouterResult:
        """Generates structured JSON summary via Gemini."""
        active_model = validate_model_id(
            model or self.primary_model or self.primary.active_model,
            self.primary.provider_id,
        )
        content = self.primary.generate_structured_json(
            messages=messages,
            model=active_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return RouterResult(
            content=content,
            provider_id=self.primary.provider_id,
            provider_name=self.primary.provider_name,
            model=active_model,
            switched_to_fallback=False,
            fallback_reason=None,
        )
