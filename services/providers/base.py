"""
WebBrief AI — Abstract Base Provider.
Defines the standard interface that all AI provider adapters must implement.
"""

from abc import ABC, abstractmethod
import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple

from .exceptions import ProviderError

logger = logging.getLogger(__name__)


class BaseProvider(ABC):
    """
    Abstract interface for AI execution providers.
    Encapsulates provider-specific HTTP calls, token authorization,
    payload serialization, and exception mapping.
    """

    def __init__(self, model: Optional[str] = None):
        self._model = (model or "").strip()

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Machine identifier (e.g. 'gemini')."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable display name (e.g. 'Google Gemini')."""
        pass

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Default recommended model identifier for this provider."""
        pass

    @property
    def active_model(self) -> str:
        """Returns the configured model, or the provider's default if none configured."""
        return self._model or self.default_model

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """
        Executes a minimal zero-token or minimal-token verification request.
        Returns:
            (is_valid, message): Validation status and user-facing feedback.
        """
        pass

    @abstractmethod
    def generate_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        timeout: int = 45,
    ) -> str:
        """
        Executes a chat completion against the provider API.
        Returns the raw generated string output.
        """
        pass

    def generate_structured_json(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        timeout: int = 45,
    ) -> Dict[str, Any]:
        """
        Generates structured JSON output from the model.
        Attempts native JSON mode when supported; parses and cleans markdown code blocks gracefully.
        """
        raw_text = self.generate_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            timeout=timeout,
        )

        if not raw_text or not raw_text.strip():
            raise ProviderError("Empty response received from provider.", provider=self.provider_id)

        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass

        # Return structured fallback if model output failed to strictly match JSON
        return {"raw_content": cleaned}

    @abstractmethod
    def list_models(self) -> List[Dict[str, Any]]:
        """
        Returns a list of available models for this provider,
        either queried dynamically or resolved from the verified catalogue.
        """
        pass
