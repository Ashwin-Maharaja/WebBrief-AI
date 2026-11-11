"""
WebBrief AI — Google Gemini Developer API Provider.
Implements integration with Google Gemini Developer API using the official google-genai SDK.
"""

import logging
import random
import time
from typing import Dict, Any, List, Optional, Tuple

from google import genai
from google.genai import types, errors

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
from .catalogue import get_models_for_provider, get_default_model, validate_model_id

logger = logging.getLogger(__name__)


class GeminiProvider(BaseProvider):
    """
    Adapter for Google Gemini Developer API using the official google-genai SDK.
    Primary AI provider for WebBrief AI.
    """

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout: int = 45
    ):
        clean_model = (model or "").strip()
        if clean_model:
            validate_model_id(clean_model, "gemini")
        super().__init__(model=clean_model)
        self.api_key = (api_key or "").strip()
        self.default_timeout = timeout
        self._client: Optional[genai.Client] = None

    @property
    def provider_id(self) -> str:
        return "gemini"

    @property
    def provider_name(self) -> str:
        return "Google Gemini"

    @property
    def default_model(self) -> str:
        return get_default_model("gemini") or "gemini-3.8-flash"

    def _get_client(self) -> genai.Client:
        if not self.api_key:
            raise ProviderAuthError("Google Gemini API key not provided.", provider=self.provider_id)
        if self._client is None:
            http_options = types.HttpOptions(timeout=int(self.default_timeout * 1000))
            self._client = genai.Client(api_key=self.api_key, http_options=http_options)
        return self._client

    def test_connection(self) -> Tuple[bool, str]:
        """
        Executes a zero-token model metadata request to verify API key validity.
        """
        if not self.api_key:
            return False, "Please enter your Google Gemini API key."

        if not self.api_key.startswith("AIza") and len(self.api_key) < 20:
            return False, "Invalid Google Gemini API key format. Gemini keys typically start with 'AIza'."

        try:
            validate_model_id(self.active_model, self.provider_id)
            client = self._get_client()
            client.models.get(model=self.active_model)
            return True, f"Connection to Google Gemini ({self.active_model}) verified successfully."
        except ProviderInvalidModelError as e:
            return False, str(e)
        except errors.APIError as e:
            if getattr(e, "code", None) in (401, 403) or "API_KEY_INVALID" in str(e) or "PERMISSION_DENIED" in str(e):
                return False, "Invalid Google Gemini API key. Please check your key at Google AI Studio."
            if getattr(e, "code", None) == 404 or "NOT_FOUND" in str(e):
                return False, f"Configured Gemini model '{self.active_model}' is not available for this API key."
            return False, f"Gemini connection verification error ({getattr(e, 'code', 'error')}): {getattr(e, 'message', str(e))}"
        except Exception as e:
            err_msg = str(e)
            if "API_KEY_INVALID" in err_msg or "403" in err_msg or "401" in err_msg:
                return False, "Invalid Google Gemini API key. Please check your key at Google AI Studio."
            return False, f"Could not reach Google Gemini API: {err_msg}"

    def generate_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        timeout: int = 45,
    ) -> str:
        req_model = (model or "").strip()
        if req_model:
            active_model = validate_model_id(req_model, self.provider_id)
        else:
            active_model = validate_model_id(self.active_model, self.provider_id)

        client = self._get_client()

        # Separate system instruction from user/model contents
        system_instruction = None
        contents = []

        for msg in messages:
            role = msg.get("role", "user").strip().lower()
            content_text = msg.get("content", "")
            if role == "system":
                if system_instruction:
                    system_instruction += "\n" + content_text
                else:
                    system_instruction = content_text
            elif role == "assistant":
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=content_text)]
                ))
            else:  # user
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=content_text)]
                ))

        if not contents:
            contents = [types.Content(role="user", parts=[types.Part.from_text(text="Please summarize the content.")])]

        is_json = bool(response_format and response_format.get("type") == "json_object")

        eff_timeout = timeout if timeout is not None else self.default_timeout
        call_timeout_ms = int(eff_timeout * 1000)

        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_instruction,
            response_mime_type="application/json" if is_json else None,
            http_options=types.HttpOptions(timeout=call_timeout_ms),
        )
        if max_tokens:
            config.max_output_tokens = max_tokens

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=active_model,
                    contents=contents,
                    config=config,
                )
                return response.text or ""
            except errors.APIError as e:
                code = getattr(e, "code", 500)
                msg = getattr(e, "message", str(e)) or str(e)
                # Never retry client-side/auth/invalid model or quota exhausted errors (AI-03)
                if code in (400, 401, 403, 404) or (code == 429 and "quota" in msg.lower()) or attempt >= max_retries:
                    self._handle_api_error(e, active_model)
                # Transient error (500, 502, 503, 504): backoff with jitter and retry
                backoff = (0.5 * (2 ** attempt)) + (random.uniform(0.1, 0.3))
                logger.warning(
                    "Gemini transient API error (%d) on attempt %d/%d; retrying in %.2fs: %s",
                    code, attempt + 1, max_retries, backoff, str(e)
                )
                time.sleep(backoff)
            except Exception as e:
                err_str = str(e)
                is_timeout = (
                    isinstance(e, TimeoutError)
                    or (getattr(errors, "httpx", None) is not None and isinstance(e, errors.httpx.TimeoutException))
                    or "timeout" in err_str.lower()
                    or "timed out" in err_str.lower()
                    or "deadline" in err_str.lower()
                )
                if attempt >= max_retries or not is_timeout:
                    if is_timeout:
                        raise ProviderTimeoutError(
                            f"Inference request timed out after {eff_timeout}s",
                            provider=self.provider_id,
                            model=active_model,
                        )
                    raise ProviderError(f"Google Gemini execution error: {err_str}", provider=self.provider_id, model=active_model)
                backoff = (0.5 * (2 ** attempt)) + (random.uniform(0.1, 0.3))
                logger.warning(
                    "Gemini network/timeout on attempt %d/%d; retrying in %.2fs: %s",
                    attempt + 1, max_retries, backoff, err_str
                )
                time.sleep(backoff)

    def embed_text(self, text: str) -> List[float]:
        """Generates vector embeddings using Gemini Embedding model."""
        if not text or not text.strip():
            return []
        client = self._get_client()
        call_timeout_ms = int(self.default_timeout * 1000)
        try:
            kwargs = {
                "model": "gemini-embedding-001",
                "contents": text.strip()[:10000],
            }
            if hasattr(types, "EmbedContentConfig"):
                kwargs["config"] = types.EmbedContentConfig(
                    http_options=types.HttpOptions(timeout=call_timeout_ms)
                )
            res = client.models.embed_content(**kwargs)
            if hasattr(res, "embeddings") and res.embeddings:
                return list(res.embeddings[0].values)
            return []
        except Exception as e:
            logger.warning("Gemini embedding generation failed: %s", str(e))
            raise ProviderError(f"Gemini embedding error: {str(e)}", provider=self.provider_id)

    def _handle_api_error(self, e: errors.APIError, model: str):
        code = getattr(e, "code", 500)
        msg = getattr(e, "message", str(e)) or str(e)

        if code in (401, 403) or "API_KEY_INVALID" in msg or "PERMISSION_DENIED" in msg:
            raise ProviderAuthError(f"Gemini authentication failed: {msg}", provider=self.provider_id, model=model, status_code=code)
        if code == 404 or "NOT_FOUND" in msg:
            raise ProviderInvalidModelError(f"Model '{model}' not found in Gemini API: {msg}", provider=self.provider_id, model=model, status_code=code)
        if code == 400 or "INVALID_ARGUMENT" in msg:
            raise ProviderBadRequestError(f"Invalid request parameters for Gemini: {msg}", provider=self.provider_id, model=model, status_code=code)
        if code == 429 or "RESOURCE_EXHAUSTED" in msg:
            if "quota" in msg.lower():
                raise ProviderQuotaError(f"Gemini quota exhausted: {msg}", provider=self.provider_id, model=model, status_code=code)
            raise ProviderRateLimitError(f"Gemini rate limit exceeded: {msg}", provider=self.provider_id, model=model, status_code=code)
        if code == 504 or "deadline" in msg.lower() or "timeout" in msg.lower():
            raise ProviderTimeoutError(f"Gemini request timed out ({code}): {msg}", provider=self.provider_id, model=model, status_code=code)
        if code in (500, 502, 503):
            raise ProviderUnavailableError(f"Gemini service unavailable ({code}): {msg}", provider=self.provider_id, model=model, status_code=code)

        raise ProviderError(f"Gemini API error ({code}): {msg}", provider=self.provider_id, model=model, status_code=code)

    def list_models(self) -> List[Dict[str, Any]]:
        return get_models_for_provider("gemini")
