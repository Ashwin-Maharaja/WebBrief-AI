"""
WebBrief AI — Cloudflare Workers AI Provider (v1.6.0).
Implements direct HTTP REST integration with Cloudflare Workers AI.
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

CF_API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareProvider(BaseProvider):
    """
    Adapter for Cloudflare Workers AI REST API.
    Primary recommended provider for WebBrief AI v1.6.0.
    """

    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: Optional[str] = None,
        timeout: int = 45
    ):
        super().__init__(model=model)
        self.account_id = (account_id or "").strip()
        self.api_token = (api_token or "").strip()
        self.default_timeout = timeout

    @property
    def provider_id(self) -> str:
        return "cloudflare"

    @property
    def provider_name(self) -> str:
        return "Cloudflare Workers AI"

    @property
    def default_model(self) -> str:
        return get_default_model("cloudflare") or "@cf/meta/llama-3.1-8b-instruct"

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def _map_http_error(self, status_code: int, error_text: str, model: str):
        """Maps HTTP status codes and Cloudflare response messages to normalized exceptions."""
        lower_err = error_text.lower()
        if status_code in (401, 403):
            if "unauthorized" in lower_err or status_code == 401:
                raise ProviderAuthError(
                    "Invalid Cloudflare API token. Please check your credentials.",
                    provider=self.provider_id,
                    model=model,
                    status_code=status_code,
                )
            raise ProviderAuthError(
                "Access denied by Cloudflare. Ensure your API token has 'Workers AI' Read/Edit permissions and Account ID is correct.",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        elif status_code == 404:
            raise ProviderInvalidModelError(
                f"Cloudflare model '{model}' not found or invalid model identifier.",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        elif status_code == 429 or "quota" in lower_err or "rate limit" in lower_err or "neuron" in lower_err:
            if "quota" in lower_err or "neuron" in lower_err:
                raise ProviderQuotaError(
                    "Cloudflare Workers AI daily neuron quota exhausted. Please check your account limits.",
                    provider=self.provider_id,
                    model=model,
                    status_code=status_code,
                )
            raise ProviderRateLimitError(
                "Cloudflare Workers AI rate limit reached (HTTP 429).",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        elif status_code in (500, 502, 503, 504):
            raise ProviderUnavailableError(
                f"Cloudflare Workers AI service temporarily unavailable (HTTP {status_code}).",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        elif status_code == 400:
            raise ProviderBadRequestError(
                f"Cloudflare rejected the request: {error_text}",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )
        else:
            raise ProviderError(
                f"Cloudflare API error (HTTP {status_code}): {error_text}",
                provider=self.provider_id,
                model=model,
                status_code=status_code,
            )

    def test_connection(self) -> Tuple[bool, str]:
        """
        Validates Cloudflare Account ID and API token without consuming generation neurons.
        Uses the Workers AI models catalogue endpoint.
        """
        if not self.account_id or not self.api_token:
            return False, "Cloudflare Account ID and API token are required."

        endpoint = f"{CF_API_BASE}/accounts/{self.account_id}/ai/models/search?per_page=1"
        try:
            resp = requests.get(endpoint, headers=self._get_headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success", False):
                    return True, "Cloudflare Workers AI credentials successfully verified."

            # Handle error status
            err_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            errors = err_data.get("errors", [])
            err_msg = "; ".join(e.get("message", "") for e in errors) or resp.text
            if resp.status_code in (401, 403):
                return False, "Authentication failed. Check your Cloudflare Account ID and API token permissions."
            return False, f"Cloudflare verification failed: {err_msg[:200]}"

        except requests.exceptions.Timeout:
            return False, "Connection to Cloudflare Workers AI timed out. Check your network connection."
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to Cloudflare Workers AI. Please check your internet connection."
        except Exception as e:
            return False, f"Cloudflare connection test error: {str(e)}"

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
        Runs chat completion through Cloudflare Workers AI.
        """
        if not self.account_id or not self.api_token:
            raise ProviderAuthError("Cloudflare Account ID and API token not configured.", provider=self.provider_id)

        target_model = (model or self.active_model).strip()
        endpoint = f"{CF_API_BASE}/accounts/{self.account_id}/ai/run/{target_model}"

        payload: Dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

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
                f"Cloudflare inference request timed out after {req_timeout}s.",
                provider=self.provider_id,
                model=target_model,
            )
        except requests.exceptions.ConnectionError as ce:
            raise ProviderUnavailableError(
                f"Connection to Cloudflare failed: {str(ce)}",
                provider=self.provider_id,
                model=target_model,
            )

        if resp.status_code != 200:
            self._map_http_error(resp.status_code, resp.text, target_model)

        try:
            data = resp.json()
        except Exception:
            raise ProviderError("Failed to parse Cloudflare JSON response.", provider=self.provider_id, model=target_model)

        if not data.get("success", False) and "result" not in data:
            errors = data.get("errors", [])
            err_msg = "; ".join(e.get("message", "") for e in errors) or "Cloudflare returned success=false."
            self._map_http_error(400, err_msg, target_model)

        result = data.get("result", {})
        if isinstance(result, dict):
            # Cloudflare chat/instruct models return 'response' or 'text'
            output = result.get("response") or result.get("text") or ""
            return str(output).strip()
        elif isinstance(result, str):
            return result.strip()

        return ""

    def generate_embedding(self, text: str, model: str = "@cf/baai/bge-base-en-v1.5", timeout: int = 20) -> List[float]:
        """
        Generates vector embeddings using Cloudflare Workers AI text embedding models.
        """
        if not self.account_id or not self.api_token:
            raise ProviderAuthError("Cloudflare credentials required to generate embeddings.", provider=self.provider_id)

        endpoint = f"{CF_API_BASE}/accounts/{self.account_id}/ai/run/{model}"
        payload = {"text": [text[:4000]]}

        try:
            resp = requests.post(endpoint, headers=self._get_headers(), json=payload, timeout=timeout)
        except requests.exceptions.Timeout:
            raise ProviderTimeoutError("Cloudflare embedding generation timed out.", provider=self.provider_id, model=model)
        except Exception as e:
            raise ProviderUnavailableError(f"Cloudflare embedding request failed: {str(e)}", provider=self.provider_id, model=model)

        if resp.status_code != 200:
            self._map_http_error(resp.status_code, resp.text, model)

        data = resp.json()
        result = data.get("result", {})
        embeddings = result.get("data", [])
        if embeddings and isinstance(embeddings[0], list):
            return [float(x) for x in embeddings[0]]
        return []

    def list_models(self) -> List[Dict[str, Any]]:
        """Returns verified models for Cloudflare Workers AI."""
        return get_models_for_provider("cloudflare")
