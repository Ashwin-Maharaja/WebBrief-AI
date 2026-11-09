"""
WebBrief AI — Verified Model Catalogue (v1.6.4).
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
                "id": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "name": "Llama 3.3 70B Instruct Fast",
                "context_window": 8192,
                "free_tier": True,
                "recommended": False,
                "description": "High-capacity 70B parameter model with fp8 acceleration for in-depth analysis.",
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
        "default_model": "llama-3.1-8b-instant",
        "models": [
            {
                "id": "llama-3.1-8b-instant",
                "name": "Llama 3.1 8B Instant (Recommended)",
                "context_window": 128000,
                "free_tier": True,
                "recommended": True,
                "description": "Ultra-low latency inference with an expansive 128k context window. Active and highly reliable on Groq free tier.",
            },
            {
                "id": "openai/gpt-oss-120b",
                "name": "GPT-OSS 120B",
                "context_window": 131072,
                "free_tier": True,
                "recommended": False,
                "description": "Flagship 120B open-weights mixture-of-experts model operating at high speed on Groq LPUs.",
            },
            {
                "id": "openai/gpt-oss-20b",
                "name": "GPT-OSS 20B",
                "context_window": 131072,
                "free_tier": True,
                "recommended": False,
                "description": "High-efficiency 20B open-weights model balancing sub-second latency and sharp extraction.",
            },
            {
                "id": "qwen/qwen3.8-27b",
                "name": "Qwen 3.8 27B",
                "context_window": 131072,
                "free_tier": True,
                "recommended": False,
                "description": "Advanced open architecture with strong synthesis, reasoning, and multilingual comprehension.",
            },
            {
                "id": "llama-3.3-70b-versatile",
                "name": "Llama 3.3 70B Versatile",
                "context_window": 128000,
                "free_tier": False,
                "recommended": False,
                "description": "High-capacity 70B model (requires Groq Tier 1+ or paid billing).",
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
