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
