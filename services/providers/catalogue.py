"""
WebBrief AI — Verified Model Catalogue (v1.6.0).
Provides curated lists of supported models, readable descriptions, and free-tier compatibility flags.
"""

from typing import Dict, List, Any, Optional

PROVIDER_CATALOGUE: Dict[str, Dict[str, Any]] = {
    "cloudflare": {
        "name": "Cloudflare Workers AI",
        "default_model": "@cf/meta/llama-3.1-8b-instruct",
        "models": [
            {
                "id": "@cf/meta/llama-3.1-8b-instruct",
                "name": "Llama 3.1 8B Instruct (Recommended)",
                "context_window": 8192,
                "free_tier": True,
                "recommended": True,
                "description": "Meta's flagship compact model. Fast, precise, and ideal for webpage summarization.",
            },
            {
                "id": "@cf/meta/llama-3-8b-instruct",
                "name": "Llama 3 8B Instruct",
                "context_window": 8192,
                "free_tier": True,
                "recommended": False,
                "description": "High-accuracy instruction-following model with solid reasoning.",
            },
            {
                "id": "@cf/mistral/mistral-7b-instruct-v0.1",
                "name": "Mistral 7B Instruct v0.1",
                "context_window": 4096,
                "free_tier": True,
                "recommended": False,
                "description": "Compact European LLM with high efficiency and sharp synthesis.",
            },
            {
                "id": "@cf/qwen/qwen1.5-7b-chat",
                "name": "Qwen 1.5 7B Chat",
                "context_window": 8192,
                "free_tier": True,
                "recommended": False,
                "description": "Alibaba's multilingual conversational model with strong multilingual coverage.",
            },
        ],
    },
    "groq": {
        "name": "GroqCloud",
        "default_model": "llama-3.3-70b-versatile",
        "models": [
            {
                "id": "llama-3.3-70b-versatile",
                "name": "Llama 3.3 70B Versatile (Recommended)",
                "context_window": 128000,
                "free_tier": True,
                "recommended": True,
                "description": "State-of-the-art 70B parameter model operating at blazing speed on Groq LPUs.",
            },
            {
                "id": "llama-3.1-8b-instant",
                "name": "Llama 3.1 8B Instant",
                "context_window": 128000,
                "free_tier": True,
                "recommended": False,
                "description": "Sub-second latency with an expansive 128k context window.",
            },
            {
                "id": "mixtral-8x7b-32768",
                "name": "Mixtral 8x7B (MoE)",
                "context_window": 32768,
                "free_tier": True,
                "recommended": False,
                "description": "High-throughput mixture of experts model with balanced performance.",
            },
            {
                "id": "gemma2-9b-it",
                "name": "Gemma 2 9B IT",
                "context_window": 8192,
                "free_tier": True,
                "recommended": False,
                "description": "Google's lightweight instruction-tuned architecture running on Groq LPUs.",
            },
        ],
    },
}


def get_models_for_provider(provider_id: str) -> List[Dict[str, Any]]:
    """Returns the catalogue of verified models for the requested provider."""
    provider_data = PROVIDER_CATALOGUE.get(str(provider_id).lower().strip())
    if not provider_data:
        return []
    return provider_data.get("models", [])


def get_default_model(provider_id: str) -> str:
    """Returns the recommended default model identifier for the provider."""
    provider_data = PROVIDER_CATALOGUE.get(str(provider_id).lower().strip())
    if not provider_data:
        return ""
    return provider_data.get("default_model", "")
