"""
WebBrief AI — Google Gemini BYOK & Credential Security Manager.
Manages BYOK credentials for Google Gemini Developer API across
local self-hosted and hosted serverless environments.
"""

import os
import re
import tempfile
from typing import Dict, Any, Tuple, Optional
from dotenv import load_dotenv

from services.providers.gemini import GeminiProvider
from services.providers.router import AIRouter
from services.providers.catalogue import (
    get_default_model,
    get_allowed_models,
    is_valid_model,
    validate_model_id,
)

# Standard configuration keys
ENV_GEMINI_API_KEY = "GEMINI_API_KEY"
ENV_GEMINI_MODEL = "GEMINI_DEFAULT_MODEL"

# Placeholder values that must not be treated as valid credentials
PLACEHOLDER_KEYS = {
    "",
    "your_gemini_api_key_here",
    "your_api_key_here",
    "placeholder",
    "none",
}


def is_serverless_env() -> bool:
    """
    Returns True if the current environment is hosted in a serverless platform
    like Vercel, AWS Lambda, or Netlify functions where the filesystem is ephemeral or read-only.
    """
    return bool(
        os.getenv("VERCEL")
        or os.getenv("VERCEL_ENV")
        or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
        or os.getenv("NETLIFY")
        or os.getenv("SERVERLESS")
        or os.getenv("HOSTED_MODE")
        or os.getenv("WEBBRIEF_HOSTED")
    )


def redact_credentials(text: str) -> str:
    """
    Redacts any sensitive provider credentials from logs, error messages, and exception traces.
    Masks Gemini API keys (AIza...), Notion tokens, Bearer headers, and legacy tokens.
    """
    if not text or not isinstance(text, str):
        return ""
    # Redact Google Gemini API keys (AIza...)
    sanitized = re.sub(r"AIza[a-zA-Z0-9_\-]{25,}", "AIzaSy[gemini_key_masked]", text)
    # Redact legacy sk- tokens
    sanitized = re.sub(r"sk-[a-zA-Z0-9_\-]{16,}", "sk-...", sanitized)
    # Redact Notion integration keys (secret_... or ntn_...)
    sanitized = re.sub(r"(?:secret_|ntn_)[a-zA-Z0-9_\-]{20,}", "[notion_secret_masked]", sanitized)
    # Redact Authorization Bearer headers
    sanitized = re.sub(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{15,}", r"\1[token_masked]", sanitized, flags=re.IGNORECASE)
    return sanitized


def validate_gemini_credentials(api_key: str, model: Optional[str] = None) -> Tuple[bool, str]:
    """Validates Google Gemini credentials and optional model selection."""
    clean_key = (api_key or "").strip()
    if not clean_key or clean_key.lower() in PLACEHOLDER_KEYS:
        return False, "Please enter your Google Gemini API key."
    if not clean_key.startswith("AIza") and len(clean_key) < 20:
        return False, "Invalid Google Gemini API key format. Gemini keys typically start with 'AIza'."

    if model:
        clean_model = str(model).strip()
        if not is_valid_model(clean_model, "gemini"):
            allowed = get_allowed_models("gemini")
            return False, f"Unsupported or unverified model '{clean_model}'. Allowed models: {', '.join(allowed)}"

    provider = GeminiProvider(api_key=clean_key, model=model)
    return provider.test_connection()


def get_active_provider_config(
    session_dict: Optional[dict] = None,
    request_data: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Resolves the active provider configuration.
    In hosted serverless mode: exclusively resolves from request_data (per-request memory).
    In local self-hosted mode: reads from process environment (.env), with optional overrides from request_data.
    Validates model ID strictly against catalogue if specified; rejects unsupported models without silent substitution.
    """
    req = request_data or {}

    if is_serverless_env():
        # Hosted mode: strictly isolated to per-request payload
        gemini_api_key = req.get("gemini_api_key") or req.get("api_key") or ""
        raw_model = req.get("gemini_model") or req.get("model")
    else:
        # Local self-hosted mode: environment variables with optional request payload overrides
        gemini_api_key = req.get("gemini_api_key") or req.get("api_key") or os.getenv(ENV_GEMINI_API_KEY, "").strip()
        raw_model = req.get("gemini_model") or req.get("model") or os.getenv(ENV_GEMINI_MODEL, "").strip()

    # Sanitize placeholders
    if gemini_api_key.lower() in PLACEHOLDER_KEYS:
        gemini_api_key = ""

    if raw_model:
        gemini_model = validate_model_id(raw_model, "gemini")
    else:
        gemini_model = get_default_model("gemini")

    return {
        "primary_provider": "gemini",
        "gemini_api_key": gemini_api_key,
        "gemini_model": gemini_model,
    }


def has_valid_config(session_dict: Optional[dict] = None, request_data: Optional[dict] = None) -> bool:
    """
    Returns True if valid non-placeholder Gemini credentials are configured.
    """
    try:
        config = get_active_provider_config(session_dict=session_dict, request_data=request_data)
        return bool(config.get("gemini_api_key"))
    except Exception:
        return False


def get_router(session_dict: Optional[dict] = None, request_data: Optional[dict] = None) -> AIRouter:
    """
    Builds and returns an AIRouter configured with current active credentials.
    """
    config = get_active_provider_config(session_dict=session_dict, request_data=request_data)
    return AIRouter(
        primary_provider="gemini",
        primary_credentials={
            "api_key": config.get("gemini_api_key"),
        },
        primary_model=config.get("gemini_model") or get_default_model("gemini"),
    )


def save_provider_config_to_env(
    config_dict: Dict[str, Any],
    env_path: Optional[str] = None,
    confirm_overwrite: bool = False,
) -> Tuple[bool, str]:
    """
    Persists validated provider configuration safely into the local .env file.
    Only runs in local self-hosted mode.
    """
    model_candidate = None
    if "gemini_model" in config_dict and config_dict["gemini_model"]:
        model_candidate = str(config_dict["gemini_model"]).strip()
    elif "model" in config_dict and config_dict["model"]:
        model_candidate = str(config_dict["model"]).strip()

    if model_candidate:
        if not is_valid_model(model_candidate, "gemini"):
            allowed = get_allowed_models("gemini")
            return False, f"Invalid model '{model_candidate}'. Supported models: {', '.join(allowed)}"

    if is_serverless_env():
        return True, "Configuration active for current browser session."

    if not env_path:
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(workspace_dir, ".env")

    file_exists = os.path.isfile(env_path)
    if file_exists and not confirm_overwrite:
        return False, "An existing configuration file (.env) was detected. Please confirm settings replacement."

    updates = {}
    if "gemini_api_key" in config_dict:
        updates[ENV_GEMINI_API_KEY] = str(config_dict["gemini_api_key"]).strip()
    elif "api_key" in config_dict:
        updates[ENV_GEMINI_API_KEY] = str(config_dict["api_key"]).strip()

    if model_candidate:
        updates[ENV_GEMINI_MODEL] = model_candidate

    # Read existing lines if .env exists
    env_lines = []
    keys_handled = set()

    if file_exists:
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    matched_key = None
                    for key in updates.keys():
                        if stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}="):
                            matched_key = key
                            break
                    if matched_key:
                        env_lines.append(f"{matched_key}={updates[matched_key]}\n")
                        keys_handled.add(matched_key)
                    else:
                        env_lines.append(line)
        except Exception:
            pass

    # Add remaining new keys
    for k, v in updates.items():
        if k not in keys_handled:
            env_lines.append(f"{k}={v}\n")

    target_dir = os.path.dirname(env_path) or "."
    try:
        with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8") as tf:
            tf.writelines(env_lines)
            temp_name = tf.name

        try:
            if hasattr(os, "chmod"):
                os.chmod(temp_name, 0o600)
        except Exception:
            pass

        if os.path.exists(env_path):
            os.replace(temp_name, env_path)
        else:
            os.rename(temp_name, env_path)

        for k, v in updates.items():
            os.environ[k] = v
        load_dotenv(env_path, override=True)

        return True, "Google Gemini configuration successfully saved and active."

    except Exception as e:
        return False, f"Failed to save configuration: {str(e)}"


def clear_saved_provider_config(env_path: Optional[str] = None) -> Tuple[bool, str]:
    """Clears stored provider credentials and model configurations."""
    managed_keys = [
        ENV_GEMINI_API_KEY,
        ENV_GEMINI_MODEL,
    ]

    for k in managed_keys:
        if k in os.environ:
            del os.environ[k]

    if not env_path:
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(workspace_dir, ".env")

    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            new_lines = [
                l for l in lines
                if not any(l.strip().startswith(f"{k}=") or l.strip().startswith(f"export {k}=") for k in managed_keys)
            ]
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            return True, "Provider configuration cleared successfully."
        except Exception as e:
            return False, f"Failed to update configuration file: {str(e)}"

    return True, "Configuration cleared successfully."
