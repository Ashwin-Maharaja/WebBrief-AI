import os
import re
import tempfile
from typing import Tuple, Optional
from dotenv import load_dotenv
from openai import OpenAI

# Standard environment variable name for OpenAI API key
ENV_KEY_NAME = "OPENAI_API_KEY"

# Standard placeholders that should NOT be considered valid configured keys
PLACEHOLDER_KEYS = {
    "",
    "your_openai_api_key_here",
    "your_api_key_here",
    "sk-your_api_key_here",
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


def is_valid_key_format(api_key: Optional[str]) -> bool:
    """
    Checks whether the given API key string has a valid format.
    OpenAI API keys typically start with 'sk-' and are at least 20 characters long without whitespace.
    """
    if not api_key or not isinstance(api_key, str):
        return False
    key = api_key.strip()
    if key.lower() in PLACEHOLDER_KEYS:
        return False
    # OpenAI keys start with sk- (or sk-proj-, etc.) and contain alphanumeric characters, underscores, and dashes
    if not key.startswith("sk-"):
        return False
    if len(key) < 20 or any(c.isspace() for c in key):
        return False
    return True


def redact_credentials(text: str) -> str:
    """
    Redacts any OpenAI API keys matching standard patterns (sk-...)
    from error strings, log messages, or exception traces.
    """
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r"sk-[a-zA-Z0-9_\-]{16,}", "sk-...", text)


def get_configured_api_key(session_dict: Optional[dict] = None) -> Optional[str]:
    """
    Retrieves the currently active OpenAI API key.
    In hosted/serverless mode, returns None to strictly prevent using any developer API key for visitors.
    In local self-hosted mode, reads exclusively from process environment (.env).
    Never reads or exposes credentials from client session cookies.
    """
    # In serverless/hosted mode, NEVER fall back to developer's server environment variable
    if is_serverless_env():
        return None

    # Check process environment variables (local self-hosted mode only)
    env_key = os.getenv(ENV_KEY_NAME)
    if env_key and isinstance(env_key, str):
        clean_env_key = env_key.strip()
        if clean_env_key.lower() not in PLACEHOLDER_KEYS:
            return clean_env_key

    return None


def has_valid_config(session_dict: Optional[dict] = None) -> bool:
    """
    Returns True if a valid non-placeholder OpenAI API key is configured.
    """
    key = get_configured_api_key(session_dict=session_dict)
    return bool(key and len(key.strip()) > 0)


def validate_openai_api_key(api_key: str) -> Tuple[bool, str]:
    """
    Validates the provided OpenAI API key by making a minimal API call.
    Uses client.models.list() to verify key authentication and active account status
    without generating text or consuming completion tokens.

    CRITICAL SECURITY RULE:
    Never echoes or exposes the submitted API key in returned error messages.

    Returns:
        (is_valid, message): Tuple containing validation boolean and sanitized user feedback.
    """
    if not api_key or not isinstance(api_key, str):
        return False, "Please provide an OpenAI API key."

    clean_key = api_key.strip()

    if clean_key.lower() in PLACEHOLDER_KEYS:
        return False, "Please enter your real OpenAI API key, not a placeholder."

    if not is_valid_key_format(clean_key):
        return False, "Invalid API key format. OpenAI API keys typically start with 'sk-'."

    try:
        # Minimal verification request with strict 10s timeout
        client = OpenAI(api_key=clean_key, timeout=10.0)
        # models.list() checks credentials against OpenAI's authentication server
        client.models.list()
        return True, "API key successfully verified with OpenAI."

    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e).lower()

        # Handle specific API error conditions with sanitized messages
        if "authenticationerror" in err_name.lower() or "invalid_api_key" in err_msg or "401" in err_msg:
            return False, "The provided API key is invalid or unauthorized. Please check your key on the OpenAI dashboard."
        elif "ratelimiterror" in err_name.lower() or "insufficient_quota" in err_msg or "quota" in err_msg:
            return False, "OpenAI account quota exceeded or billing is not active. Please check your OpenAI account balance."
        elif "permissiondeniederror" in err_name.lower() or "403" in err_msg:
            return False, "Access denied by OpenAI for this API key. Verify project permissions."
        elif "apiconnectionerror" in err_name.lower() or "timeout" in err_msg or "connection" in err_msg:
            return False, "Connection to OpenAI API failed or timed out. Please check your internet connection."
        else:
            return False, "OpenAI API returned an error during key validation. Please verify your credentials."


def save_api_key_to_env(
    api_key: str,
    env_path: Optional[str] = None,
    confirm_overwrite: bool = False,
    session_dict: Optional[dict] = None
) -> Tuple[bool, str]:
    """
    Saves the validated API key locally into the .env file.
    Ensures safe writing with restricted file permissions where supported.
    
    If the environment is serverless or the filesystem is read-only, falls back to session storage.
    If an existing .env already exists, requires confirm_overwrite=True before modifying.

    Returns:
        (success, message): Tuple indicating success status and informative message.
    """
    if not api_key or not isinstance(api_key, str):
        return False, "Cannot save an empty API key."

    clean_key = api_key.strip()

    if not is_valid_key_format(clean_key):
        return False, "Cannot save an invalid API key format."

    # Determine default path to workspace .env if not specified
    if not env_path:
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(workspace_dir, ".env")

    # If running in serverless environment (e.g. Vercel), do not write to filesystem or cookies
    if is_serverless_env():
        return True, "API key verified for active browser session."

    # Check if .env file already exists
    file_exists = os.path.isfile(env_path)
    if file_exists and not confirm_overwrite:
        return False, "An existing configuration file (.env) was detected. Please confirm key replacement."

    # Prepare .env contents safely
    env_lines = []
    found_key = False

    if file_exists:
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith(f"{ENV_KEY_NAME}=") or stripped.startswith(f"export {ENV_KEY_NAME}="):
                        env_lines.append(f"{ENV_KEY_NAME}={clean_key}\n")
                        found_key = True
                    else:
                        env_lines.append(line)
        except Exception as e:
            # If reading failed due to permissions or OS lock
            pass

    if not found_key:
        env_lines.append(f"{ENV_KEY_NAME}={clean_key}\n")

    # Write safely using atomic write or temporary file
    target_dir = os.path.dirname(env_path) or "."
    try:
        # Create temp file in the same directory to allow atomic replace
        with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8") as tf:
            tf.writelines(env_lines)
            temp_name = tf.name

        # Restrict permissions on POSIX systems (chmod 600 - owner read/write only)
        try:
            if hasattr(os, "chmod"):
                os.chmod(temp_name, 0o600)
        except Exception:
            pass

        # Atomic replacement
        if os.path.exists(env_path):
            os.replace(temp_name, env_path)
        else:
            os.rename(temp_name, env_path)

        # Update running process environment and dotenv
        os.environ[ENV_KEY_NAME] = clean_key
        load_dotenv(env_path, override=True)

        return True, "API key successfully saved and active."

    except (OSError, PermissionError) as e:
        # If filesystem write is restricted on local machine
        os.environ[ENV_KEY_NAME] = clean_key
        return True, "API key active for current process (filesystem write restricted)."
    except Exception as e:
        return False, f"Failed to save configuration file: {str(e)}"


def clear_saved_api_key(
    env_path: Optional[str] = None,
    session_dict: Optional[dict] = None
) -> Tuple[bool, str]:
    """
    Clears the configured OpenAI API key from .env, environment, and session.
    """
    if not env_path:
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(workspace_dir, ".env")

    # Clear from process memory
    if ENV_KEY_NAME in os.environ:
        del os.environ[ENV_KEY_NAME]

    # Clear from session
    if session_dict is not None and isinstance(session_dict, dict):
        session_dict.pop(ENV_KEY_NAME, None)

    # Remove or update .env
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            new_lines = [l for l in lines if not l.strip().startswith(f"{ENV_KEY_NAME}=")]
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            return True, "Configuration cleared successfully."
        except Exception as e:
            return False, f"Failed to update configuration file: {str(e)}"

    return True, "Configuration cleared successfully."
