"""
WebBrief AI — GroqCloud Provider (v1.6.0).
Implements direct HTTP REST integration with GroqCloud API.
"""

import json
import logging
import requests
from typing import Dict, Any, List, Optional, Tuple

from .base import BaseProvider
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
from .catalogue import get_models_for_provider, get_default_model

logger = logging.getLogger(__name__)

GROQ_API_BASE = "https://api.groq.com/openai/v1"


class GroqProvider(BaseProvider):
    """
    Adapter for GroqCloud API.
    Optional fallback or standalone primary provider for WebBrief AI v1.6.0.
    """

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout: int = 45
    ):
        super().__init__(model=model)
        self.api_key = (api_key or "").strip()
        self.default_timeout = timeout

    @property
    def provider_id(self) -> str:
        return "groq"

    @property
    def provider_name(self) -> str:
        return "GroqCloud"

    @property
    def default_model(self) -> str:
        return get_default_model("groq") or "llama-3.3-70b-versatile"

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _map_http_error(self, status_code: int, error_text: str, model: str):
        """Maps HTTP status codes and Groq API error messages to normalized exceptions."""
        lower_err = error_text.lower()
        if status_code in (401, 403):
            raise ProviderAuthError(
                "Invalid Groq API key. Please check your credentials in Settings.",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        elif status_code == 404 or "model_not_found" in lower_err:
            raise ProviderInvalidModelError(
                f"Groq model '{model}' not found or decommissioned.",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        elif status_code == 429 or "rate_limit_exceeded" in lower_err or "quota" in lower_err:
            if "quota" in lower_err:
                raise ProviderQuotaError(
                    "GroqCloud account rate limit or credit quota exhausted (HTTP 429).",
                    provider=self.provider_id,
                    model=model,
                    status_code=status_code,
                )
            raise ProviderRateLimitError(
                "GroqCloud rate limit reached (HTTP 429).",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        elif status_code in (500, 502, 503, 504):
            raise ProviderUnavailableError(
                f"GroqCloud service temporarily unavailable (HTTP {status_code}).",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        elif status_code == 400:
            raise ProviderBadRequestError(
                f"Groq rejected the request: {error_text}",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        else:
            raise ProviderError(
                f"Groq API error (HTTP {status_code}): {error_text}",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )

    def test_connection(self) -> Tuple[bool, str]:
        """
        Validates the Groq API key using the GET /models endpoint with 0 completion tokens.
        """
        if not self.api_key:
            return False, "Groq API key is required."

        endpoint = f"{GROQ_API_BASE}/models"
        try:
            resp = requests.get(endpoint, headers=self._get_headers(), timeout=10)
            if resp.status_code == 200:
                return True, "Groq API key successfully verified."

            if resp.status_code in (401, 403):
                return False, "Invalid Groq API key. Check your key on the GroqCloud dashboard."

            err_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err_msg = err_data.get("error", {}).get("message", resp.text)
            return False, f"Groq verification failed: {err_msg[:200]}"

        except requests.exceptions.Timeout:
            return False, "Connection to GroqCloud timed out. Check your network connection."
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to GroqCloud API. Please check your internet connection."
        except Exception as e:
            return False, f"Groq connection test error: {str(e)}"

    def generate_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """
        Executes chat completion via GroqCloud.
        """
        if not self.api_key:
            raise ProviderAuthError("Groq API key not configured.", provider=self.provider_id)

        target_model = (model or self.active_model).strip()
        endpoint = f"{GROQ_API_BASE}/chat/completions"

        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if response_format:
            payload["response_format"] = response_format

        req_timeout = timeout or self.default_timeout

        try:
            resp = requests.post(
                endpoint,
                headers=self._get_headers(),
                json=payload,
                timeout=req_timeout,
            )
        except requests.exceptions.Timeout:
            raise ProviderTimeoutError(
                f"Groq inference request timed out after {req_timeout}s.",
                provider=self.provider_id,
                model=target_model,
            )
        except requests.exceptions.ConnectionError as ce:
            raise ProviderUnavailableError(
                f"Connection to GroqCloud failed: {str(ce)}",
                provider=self.provider_id,
                model=target_model,
            )

        if resp.status_code != 200:
            self._map_http_error(resp.status_code, resp.text, target_model)

        try:
            data = resp.json()
        except Exception:
            raise ProviderError("Failed to parse Groq JSON response.", provider=self.provider_id, model=target_model)

        choices = data.get("choices", [])
        if not choices:
            raise ProviderError("Groq returned no completion choices.", provider=self.provider_id, model=target_model)

        message = choices[0].get("message", {})
        return (message.get("content") or "").strip()

    def list_models(self) -> List[Dict[str, Any]]:
        """Returns verified models for GroqCloud."""
        return get_models_for_provider("groq")
