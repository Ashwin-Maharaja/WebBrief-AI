"""
WebBrief AI — Verified Model Catalogue.
Provides curated lists of supported Google Gemini models, readable descriptions, and capabilities.
"""

from typing import Dict, List, Any, Optional

PROVIDER_CATALOGUE: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "name": "Google Gemini",
        "default_model": "gemini-3.8-flash",
        "models": [
            {
                "id": "gemini-3.8-flash",
                "name": "Gemini 3.8 Flash (Recommended)",
                "context_window": 1048576,
                "free_tier": True,
                "recommended": True,
                "description": "Google's flagship workhorse model. Ultra-fast, 1M token context, ideal for comprehensive webpage summarization.",
            },
            {
                "id": "gemini-3.5-flash-lite",
                "name": "Gemini 3.5 Flash-Lite",
                "context_window": 1048576,
                "free_tier": True,
                "recommended": False,
                "description": "Sub-second latency, lightweight model designed for rapid extraction and high-throughput batching.",
            },
        ],
    },
}


def get_models_for_provider(provider_id: str = "gemini") -> List[Dict[str, Any]]:
    """Returns the catalogue of verified models for the requested provider."""
    pid = str(provider_id or "gemini").lower().strip()
    provider_data = PROVIDER_CATALOGUE.get(pid)
    if not provider_data:
        # Default to gemini if unrecognized
        provider_data = PROVIDER_CATALOGUE.get("gemini", {})
    return provider_data.get("models", [])


def get_default_model(provider_id: str = "gemini") -> str:
    """Returns the recommended default model identifier for the provider."""
    pid = str(provider_id or "gemini").lower().strip()
    provider_data = PROVIDER_CATALOGUE.get(pid)
    if not provider_data:
        provider_data = PROVIDER_CATALOGUE.get("gemini", {})
    return provider_data.get("default_model", "gemini-3.8-flash")


def get_allowed_models(provider_id: str = "gemini") -> List[str]:
    """Returns the list of allowlisted model IDs for the given provider."""
    models = get_models_for_provider(provider_id)
    return [m["id"] for m in models if "id" in m]


def is_valid_model(model_id: Optional[str], provider_id: str = "gemini") -> bool:
    """Checks whether the given model_id is allowlisted for the provider."""
    if not model_id or not isinstance(model_id, str):
        return False
    return model_id.strip() in get_allowed_models(provider_id)


def validate_model_id(model_id: Optional[str], provider_id: str = "gemini") -> str:
    """
    Validates model_id against the provider catalogue.
    If model_id is None or empty, returns the provider's default model.
    If model_id is invalid or unsupported, raises ProviderInvalidModelError.
    Never silently substitutes another model.
    """
    if not model_id:
        return get_default_model(provider_id)
    clean_id = str(model_id).strip()
    allowed = get_allowed_models(provider_id)
    if clean_id not in allowed:
        from .exceptions import ProviderInvalidModelError
        raise ProviderInvalidModelError(
            f"Unsupported or unverified model '{clean_id}' for provider '{provider_id}'. "
            f"Allowed models: {', '.join(allowed)}",
            provider=provider_id,
            model=clean_id,
        )
    return clean_id
