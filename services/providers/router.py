"""
WebBrief AI — Automatic Provider Router (v1.6.0).
Coordinates primary provider execution, error classification, and optional fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Dict, Any, List, Optional, Tuple

from .base import BaseProvider
from .cloudflare import CloudflareProvider
from .groq import GroqProvider
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

# Explicit tuple of exception types that are eligible for automatic fallback routing
FALLBACK_ELIGIBLE_EXCEPTIONS = (
    ProviderRateLimitError,
    ProviderQuotaError,
    ProviderUnavailableError,
    ProviderTimeoutError,
)


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
    Coordinates multi-provider routing between Cloudflare Workers AI and GroqCloud.
    Executes primary provider and falls back cleanly on eligible infrastructure failures.
    """

    def __init__(
        self,
        primary_provider: str = "cloudflare",
        primary_credentials: Optional[Dict[str, Any]] = None,
        primary_model: Optional[str] = None,
        fallback_enabled: bool = False,
        fallback_provider: Optional[str] = None,
        fallback_credentials: Optional[Dict[str, Any]] = None,
        fallback_model: Optional[str] = None,
    ):
        self.primary_provider_id = (primary_provider or "cloudflare").strip().lower()
        self.primary_credentials = primary_credentials or {}
        self.primary_model = primary_model

        self.fallback_enabled = bool(fallback_enabled)
        self.fallback_provider_id = (fallback_provider or "").strip().lower() if self.fallback_enabled else None
        self.fallback_credentials = fallback_credentials or {}
        self.fallback_model = fallback_model

        # Guard: Primary and Fallback cannot be the exact same provider
        if self.fallback_enabled and self.primary_provider_id == self.fallback_provider_id:
            logger.warning("Primary and fallback providers cannot be identical; disabling fallback.")
            self.fallback_enabled = False
            self.fallback_provider_id = None

        self._primary_instance: Optional[BaseProvider] = None
        self._fallback_instance: Optional[BaseProvider] = None

    def _instantiate_provider(self, provider_id: str, credentials: Dict[str, Any], model: Optional[str]) -> BaseProvider:
        """Instantiates a provider adapter from configuration dictionary."""
        pid = (provider_id or "").strip().lower()
        if pid == "cloudflare":
            account_id = credentials.get("account_id") or credentials.get("CLOUDFLARE_ACCOUNT_ID") or ""
            api_token = credentials.get("api_token") or credentials.get("CLOUDFLARE_API_TOKEN") or ""
            return CloudflareProvider(account_id=account_id, api_token=api_token, model=model)
        elif pid == "groq":
            api_key = credentials.get("api_key") or credentials.get("GROQ_API_KEY") or ""
            return GroqProvider(api_key=api_key, model=model)
        else:
            raise ProviderError(f"Unsupported provider identifier: '{provider_id}'")

    @property
    def primary(self) -> BaseProvider:
        if self._primary_instance is None:
            self._primary_instance = self._instantiate_provider(
                self.primary_provider_id, self.primary_credentials, self.primary_model
            )
        return self._primary_instance

    @property
    def fallback(self) -> Optional[BaseProvider]:
        if not self.fallback_enabled or not self.fallback_provider_id:
            return None
        if self._fallback_instance is None:
            self._fallback_instance = self._instantiate_provider(
                self.fallback_provider_id, self.fallback_credentials, self.fallback_model
            )
        return self._fallback_instance

    def _is_eligible_for_fallback(self, exc: Exception) -> bool:
        """
        Determines whether the encountered exception qualifies for automatic fallback.
        Eligible: HTTP 429, Quotas, 500/502/503/504, Timeouts.
        Ineligible: 401, 403, Invalid Model, Malformed request, Policy Refusals.
        """
        if isinstance(exc, (ProviderAuthError, ProviderInvalidModelError, ProviderBadRequestError)):
            return False
        return isinstance(exc, FALLBACK_ELIGIBLE_EXCEPTIONS)

    def execute_with_routing(self, call_fn) -> RouterResult:
        """
        Executes an inference function against primary provider with optional fallback.
        call_fn receives (provider_instance) and returns generated content.
        """
        primary_inst = self.primary

        try:
            # 1. Attempt primary provider
            result = call_fn(primary_inst)
            return RouterResult(
                content=result,
                provider_id=primary_inst.provider_id,
                provider_name=primary_inst.provider_name,
                model=primary_inst.active_model,
                switched_to_fallback=False,
                fallback_reason=None,
            )

        except Exception as primary_error:
            # Check eligibility for fallback
            fallback_inst = self.fallback
            is_eligible = self._is_eligible_for_fallback(primary_error)

            if not is_eligible or fallback_inst is None:
                # Do not catch or disguise non-fallback failures; re-raise immediately
                logger.error(
                    "Primary provider '%s' failed (eligible_fallback=%s, fallback_configured=%s): %s",
                    primary_inst.provider_id,
                    is_eligible,
                    fallback_inst is not None,
                    str(primary_error),
                )
                raise primary_error

            # 2. Eligible failure encountered and fallback is available: attempt fallback
            primary_reason = str(primary_error)
            logger.warning(
                "Primary provider '%s' encountered recoverable failure (%s). Automatically switching to fallback '%s'.",
                primary_inst.provider_id,
                primary_reason,
                fallback_inst.provider_id,
            )

            try:
                fallback_result = call_fn(fallback_inst)
                return RouterResult(
                    content=fallback_result,
                    provider_id=fallback_inst.provider_id,
                    provider_name=fallback_inst.provider_name,
                    model=fallback_inst.active_model,
                    switched_to_fallback=True,
                    fallback_reason=primary_reason,
                )
            except Exception as fallback_error:
                # Both primary and fallback failed: return unified, informative error
                logger.error(
                    "Fallback provider '%s' also failed after primary failure: %s",
                    fallback_inst.provider_id,
                    str(fallback_error),
                )
                raise ProviderError(
                    f"All configured providers failed. "
                    f"Primary ({primary_inst.provider_name}): {primary_reason}. "
                    f"Fallback ({fallback_inst.provider_name}): {str(fallback_error)}"
                )

    def generate_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        timeout: int = 45,
    ) -> RouterResult:
        """Routes text completion request through configured providers."""
        return self.execute_with_routing(
            lambda prov: prov.generate_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        )

    def generate_structured_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        timeout: int = 45,
    ) -> RouterResult:
        """Routes structured JSON generation through configured providers."""
        return self.execute_with_routing(
            lambda prov: prov.generate_structured_json(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        )
