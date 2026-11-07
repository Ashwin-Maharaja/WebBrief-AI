"""
WebBrief AI — Multi-Provider BYOK & Credential Security Manager (v1.6.0).
Manages BYOK credentials for Cloudflare Workers AI and GroqCloud across
local self-hosted and hosted serverless environments.
"""

import os
import re
import tempfile
from typing import Dict, Any, Tuple, Optional
from dotenv import load_dotenv

from services.providers.cloudflare import CloudflareProvider
from services.providers.groq import GroqProvider
from services.providers.router import AIRouter
from services.providers.catalogue import get_default_model

# Standard configuration keys
ENV_PRIMARY_PROVIDER = "PRIMARY_PROVIDER"
ENV_CF_ACCOUNT_ID = "CLOUDFLARE_ACCOUNT_ID"
ENV_CF_API_TOKEN = "CLOUDFLARE_API_TOKEN"
ENV_CF_MODEL = "CLOUDFLARE_DEFAULT_MODEL"

ENV_GROQ_API_KEY = "GROQ_API_KEY"
ENV_GROQ_MODEL = "GROQ_DEFAULT_MODEL"

ENV_FALLBACK_ENABLED = "FALLBACK_ENABLED"
ENV_FALLBACK_PROVIDER = "FALLBACK_PROVIDER"
ENV_FALLBACK_MODEL = "FALLBACK_MODEL"

# Placeholder values that must not be treated as valid credentials
PLACEHOLDER_KEYS = {
    "",
    "your_openai_api_key_here",
    "your_cloudflare_account_id_here",
    "your_cloudflare_api_token_here",
    "your_groq_api_key_here",
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
    Masks Groq API keys (gsk_...), Cloudflare tokens, and legacy tokens.
    """
    if not text or not isinstance(text, str):
        return ""
    # Redact Groq keys (gsk_...)
    sanitized = re.sub(r"gsk_[a-zA-Z0-9_\-]{20,}", "gsk_...", text)
    # Redact legacy sk- tokens
    sanitized = re.sub(r"sk-[a-zA-Z0-9_\-]{16,}", "sk-...", sanitized)
    # Redact Cloudflare Account IDs (32 hex characters) if exposed in error messages
    sanitized = re.sub(r"\b[0-9a-f]{32}\b", "[account_id_masked]", sanitized, flags=re.IGNORECASE)
    return sanitized


def validate_cloudflare_credentials(account_id: str, api_token: str) -> Tuple[bool, str]:
    """Validates Cloudflare credentials using a zero-token catalogue query."""
    clean_id = (account_id or "").strip()
    clean_token = (api_token or "").strip()

    if not clean_id or clean_id.lower() in PLACEHOLDER_KEYS:
        return False, "Please enter your Cloudflare Account ID."
    if not clean_token or clean_token.lower() in PLACEHOLDER_KEYS:
        return False, "Please enter your Cloudflare API token."

    provider = CloudflareProvider(account_id=clean_id, api_token=clean_token)
    return provider.test_connection()


def validate_groq_credentials(api_key: str) -> Tuple[bool, str]:
    """Validates Groq credentials using the zero-token GET /models endpoint."""
    clean_key = (api_key or "").strip()
    if not clean_key or clean_key.lower() in PLACEHOLDER_KEYS:
        return False, "Please enter your Groq API key."
    if not clean_key.startswith("gsk_") and len(clean_key) < 20:
        return False, "Invalid Groq API key format. Groq keys typically start with 'gsk_'."

    provider = GroqProvider(api_key=clean_key)
    return provider.test_connection()


def get_active_provider_config(
    session_dict: Optional[dict] = None,
    request_data: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Resolves the active provider configuration.
    In hosted serverless mode: exclusively resolves from request_data (per-request memory).
    In local self-hosted mode: reads from process environment (.env), with optional overrides from request_data.
    """
    req = request_data or {}

    if is_serverless_env():
        # Hosted mode: strictly isolated to per-request payload
        primary_provider = req.get("primary_provider") or "cloudflare"
        cf_account_id = req.get("cloudflare_account_id") or ""
        cf_api_token = req.get("cloudflare_api_token") or ""
        cf_model = req.get("cloudflare_model") or get_default_model("cloudflare")

        groq_api_key = req.get("groq_api_key") or req.get("api_key") or ""
        groq_model = req.get("groq_model") or get_default_model("groq")

        fallback_enabled = req.get("fallback_enabled") in (True, "true", "1", 1)
        fallback_provider = req.get("fallback_provider") or ("groq" if primary_provider == "cloudflare" else "cloudflare")
        fallback_model = req.get("fallback_model")
    else:
        # Local self-hosted mode: environment variables with optional request payload overrides
        primary_provider = req.get("primary_provider") or os.getenv(ENV_PRIMARY_PROVIDER, "cloudflare").strip().lower()
        cf_account_id = req.get("cloudflare_account_id") or os.getenv(ENV_CF_ACCOUNT_ID, "").strip()
        cf_api_token = req.get("cloudflare_api_token") or os.getenv(ENV_CF_API_TOKEN, "").strip()
        cf_model = req.get("cloudflare_model") or os.getenv(ENV_CF_MODEL, "").strip() or get_default_model("cloudflare")

        groq_api_key = req.get("groq_api_key") or req.get("api_key") or os.getenv(ENV_GROQ_API_KEY, "").strip()
        groq_model = req.get("groq_model") or os.getenv(ENV_GROQ_MODEL, "").strip() or get_default_model("groq")

        fb_env = os.getenv(ENV_FALLBACK_ENABLED, "false").strip().lower() in ("true", "1", "yes")
        fallback_enabled = req.get("fallback_enabled") in (True, "true", "1", 1) if "fallback_enabled" in req else fb_env
        fallback_provider = req.get("fallback_provider") or os.getenv(ENV_FALLBACK_PROVIDER, "groq" if primary_provider == "cloudflare" else "cloudflare").strip().lower()
        fallback_model = req.get("fallback_model") or os.getenv(ENV_FALLBACK_MODEL, "").strip() or None

    # Sanitize placeholders
    if cf_account_id.lower() in PLACEHOLDER_KEYS:
        cf_account_id = ""
    if cf_api_token.lower() in PLACEHOLDER_KEYS:
        cf_api_token = ""
    if groq_api_key.lower() in PLACEHOLDER_KEYS:
        groq_api_key = ""

    return {
        "primary_provider": primary_provider,
        "cloudflare_account_id": cf_account_id,
        "cloudflare_api_token": cf_api_token,
        "cloudflare_model": cf_model,
        "groq_api_key": groq_api_key,
        "groq_model": groq_model,
        "fallback_enabled": fallback_enabled,
        "fallback_provider": fallback_provider,
        "fallback_model": fallback_model,
    }


def has_valid_config(session_dict: Optional[dict] = None, request_data: Optional[dict] = None) -> bool:
    """
    Returns True if the primary provider has valid non-placeholder credentials configured.
    """
    config = get_active_provider_config(session_dict=session_dict, request_data=request_data)
    primary = config.get("primary_provider", "cloudflare")

    if primary == "cloudflare":
        return bool(config.get("cloudflare_account_id") and config.get("cloudflare_api_token"))
    elif primary == "groq":
        return bool(config.get("groq_api_key"))

    return False


def get_router(session_dict: Optional[dict] = None, request_data: Optional[dict] = None) -> AIRouter:
    """
    Builds and returns an AIRouter configured with current active credentials.
    """
    config = get_active_provider_config(session_dict=session_dict, request_data=request_data)
    primary_id = config.get("primary_provider", "cloudflare")

    primary_creds = {}
    primary_model = None

    if primary_id == "cloudflare":
        primary_creds = {
            "account_id": config.get("cloudflare_account_id"),
            "api_token": config.get("cloudflare_api_token"),
        }
        primary_model = config.get("cloudflare_model")
    else:
        primary_creds = {
            "api_key": config.get("groq_api_key"),
        }
        primary_model = config.get("groq_model")

    fallback_enabled = config.get("fallback_enabled", False)
    fallback_id = config.get("fallback_provider")
    fallback_creds = {}
    fallback_model = config.get("fallback_model")

    if fallback_enabled and fallback_id:
        if fallback_id == "cloudflare":
            fallback_creds = {
                "account_id": config.get("cloudflare_account_id"),
                "api_token": config.get("cloudflare_api_token"),
            }
            if not fallback_model:
                fallback_model = config.get("cloudflare_model")
        elif fallback_id == "groq":
            fallback_creds = {
                "api_key": config.get("groq_api_key"),
            }
            if not fallback_model:
                fallback_model = config.get("groq_model")

    return AIRouter(
        primary_provider=primary_id,
        primary_credentials=primary_creds,
        primary_model=primary_model,
        fallback_enabled=fallback_enabled,
        fallback_provider=fallback_id,
        fallback_credentials=fallback_creds,
        fallback_model=fallback_model,
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
    if is_serverless_env():
        return True, "Configuration active for current browser session."

    if not env_path:
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(workspace_dir, ".env")

    file_exists = os.path.isfile(env_path)
    if file_exists and not confirm_overwrite:
        return False, "An existing configuration file (.env) was detected. Please confirm settings replacement."

    updates = {}
    if "primary_provider" in config_dict:
        updates[ENV_PRIMARY_PROVIDER] = str(config_dict["primary_provider"]).strip().lower()
    if "cloudflare_account_id" in config_dict:
        updates[ENV_CF_ACCOUNT_ID] = str(config_dict["cloudflare_account_id"]).strip()
    if "cloudflare_api_token" in config_dict:
        updates[ENV_CF_API_TOKEN] = str(config_dict["cloudflare_api_token"]).strip()
    if "cloudflare_model" in config_dict:
        updates[ENV_CF_MODEL] = str(config_dict["cloudflare_model"]).strip()

    if "groq_api_key" in config_dict:
        updates[ENV_GROQ_API_KEY] = str(config_dict["groq_api_key"]).strip()
    if "groq_model" in config_dict:
        updates[ENV_GROQ_MODEL] = str(config_dict["groq_model"]).strip()

    if "fallback_enabled" in config_dict:
        updates[ENV_FALLBACK_ENABLED] = "true" if config_dict["fallback_enabled"] else "false"
    if "fallback_provider" in config_dict:
        updates[ENV_FALLBACK_PROVIDER] = str(config_dict["fallback_provider"]).strip().lower()
    if "fallback_model" in config_dict:
        updates[ENV_FALLBACK_MODEL] = str(config_dict["fallback_model"]).strip()

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

        return True, "Provider configuration successfully saved and active."

    except Exception as e:
        return False, f"Failed to save configuration: {str(e)}"


def clear_saved_provider_config(env_path: Optional[str] = None) -> Tuple[bool, str]:
    """Clears all stored provider credentials and routing configurations."""
    managed_keys = [
        ENV_PRIMARY_PROVIDER,
        ENV_CF_ACCOUNT_ID,
        ENV_CF_API_TOKEN,
        ENV_CF_MODEL,
        ENV_GROQ_API_KEY,
        ENV_GROQ_MODEL,
        ENV_FALLBACK_ENABLED,
        ENV_FALLBACK_PROVIDER,
        ENV_FALLBACK_MODEL,
        "OPENAI_API_KEY",  # Clean legacy key as well
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
            return True, "All provider configurations cleared successfully."
        except Exception as e:
            return False, f"Failed to update configuration file: {str(e)}"

    return True, "Configuration cleared successfully."
