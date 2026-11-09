"""
WebBrief AI — Notion Service Module
Handles Notion API authentication, workspace search, block conversion,
and exporting single and comparative summary briefs to Notion pages.
Strictly adheres to official Notion API (version 2022-06-28).
"""

import json
import logging
import os
import re
from typing import Dict, Any, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_TIMEOUT = 12  # seconds


class NotionError(Exception):
    """Exception raised when Notion API calls fail."""
    pass


# -------------------------------------------------------------
# Credential Management (BYOK)
# -------------------------------------------------------------
def get_notion_key(
    provided_token: Optional[str] = None,
    session_dict: Optional[Dict[str, Any]] = None,
    vault_config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Retrieves the Notion integration token.
    1. If provided directly in request parameters (e.g. per-request in-memory submission), uses it.
    2. If present in temporary encrypted Redis vault configuration, uses it.
    3. Otherwise in local self-hosted mode, reads strictly from process environment (NOTION_API_KEY/NOTION_TOKEN).
    Never exposes or reads credentials from Flask client-side cookie sessions.
    """
    if provided_token and str(provided_token).strip():
        token = str(provided_token).strip()
        if token.startswith("secret_") or token.startswith("ntn_"):
            return token

    if vault_config and isinstance(vault_config, dict):
        v_tok = vault_config.get("notion_token") or vault_config.get("notion_api_key")
        if v_tok and str(v_tok).strip():
            return str(v_tok).strip()

    token = os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")
    if token and str(token).strip():
        return str(token).strip()

    return None


def has_notion_config(
    session_dict: Optional[Dict[str, Any]] = None,
    provided_token: Optional[str] = None,
    vault_config: Optional[Dict[str, Any]] = None,
) -> bool:
    """Returns True if a Notion integration token is configured."""
    key = get_notion_key(provided_token=provided_token, session_dict=session_dict, vault_config=vault_config)
    return bool(key and len(key) >= 10)


def save_notion_key_to_env(
    key: str,
    env_file_path: Optional[str] = None,
    session_dict: Optional[Dict[str, Any]] = None,
    is_hosted: bool = False,
) -> Tuple[bool, str]:
    """
    Persists the Notion integration token.
    In hosted / serverless mode: requires the encrypted Redis vault. Does not write to ephemeral
    filesystem and NEVER stores tokens in unencrypted Flask client-side session cookies.
    In local self-hosted mode: writes NOTION_API_KEY to local .env configuration file.
    Never exposes raw tokens in return messages.
    """
    if not key or not isinstance(key, str):
        return False, "Invalid Notion integration token provided."

    cleaned_key = key.strip()
    if len(cleaned_key) < 10:
        return False, "Notion integration token is too short."

    # Hosted serverless mode: do not write to disk and never store credentials in Flask client session
    if is_hosted:
        return False, "Persistent hosted Notion integration requires the secure encrypted Redis vault. Please configure REDIS_URL and CREDENTIAL_ENCRYPTION_KEY, or supply token per-request."

    # Local mode: persist to .env
    if not env_file_path:
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_file_path = os.path.join(workspace_dir, ".env")

    try:
        lines_env = []
        key_found = False
        if os.path.isfile(env_file_path):
            with open(env_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("NOTION_API_KEY=") or line.startswith("NOTION_TOKEN="):
                        lines_env.append(f"NOTION_API_KEY={cleaned_key}\n")
                        key_found = True
                    else:
                        lines_env.append(line)

        if not key_found:
            lines_env.append(f"NOTION_API_KEY={cleaned_key}\n")

        with open(env_file_path, "w", encoding="utf-8") as f:
            f.writelines(lines_env)

        os.environ["NOTION_API_KEY"] = cleaned_key
        return True, "Notion integration token saved to local .env configuration."
    except Exception as e:
        logger.error("Failed to write NOTION_API_KEY to .env: %s", str(e))
        return False, f"Failed to save Notion token to local configuration: {str(e)}"


def clear_saved_notion_key(session_dict: Optional[Dict[str, Any]] = None, env_file_path: Optional[str] = None) -> bool:
    """
    Removes the Notion token from session, process environment, and local .env.
    """
    if session_dict is not None and isinstance(session_dict, dict):
        session_dict.pop("_notion_token", None)
        session_dict.pop("notion_api_key", None)

    if "NOTION_API_KEY" in os.environ:
        del os.environ["NOTION_API_KEY"]
    if "NOTION_TOKEN" in os.environ:
        del os.environ["NOTION_TOKEN"]

    if not env_file_path:
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_file_path = os.path.join(workspace_dir, ".env")

    try:
        if os.path.isfile(env_file_path):
            lines_env = []
            with open(env_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not (line.startswith("NOTION_API_KEY=") or line.startswith("NOTION_TOKEN=")):
                        lines_env.append(line)

            with open(env_file_path, "w", encoding="utf-8") as f:
                f.writelines(lines_env)

        return True
    except Exception as e:
        logger.error("Failed to clear Notion key from .env: %s", str(e))
        return False


# -------------------------------------------------------------
# Notion API Client Helpers
# -------------------------------------------------------------
def get_notion_headers(token: str) -> Dict[str, str]:
    """Builds standard authorization headers for Notion API requests."""
    return {
        "Authorization": f"Bearer {token.strip()}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def validate_notion_token(token: str) -> Tuple[bool, str]:
    """
    Validates a Notion integration token via the /v1/users/me endpoint.
    Returns:
        (is_valid: bool, bot_name_or_message: str)
    """
    if not token or not isinstance(token, str):
        return False, "Notion token cannot be empty."

    cleaned_token = token.strip()
    if not (cleaned_token.startswith("secret_") or cleaned_token.startswith("ntn_")):
        return False, "Invalid Notion token format. Notion internal tokens typically start with 'secret_' or 'ntn_'."

    url = f"{NOTION_BASE_URL}/users/me"
    try:
        resp = requests.get(url, headers=get_notion_headers(cleaned_token), timeout=NOTION_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            bot_name = data.get("name") or data.get("bot", {}).get("owner", {}).get("user", {}).get("name") or "Connected Bot"
            return True, f"Successfully connected as '{bot_name}'"
        elif resp.status_code == 401:
            return False, "Authentication failed. Token is invalid or has been revoked."
        elif resp.status_code == 403:
            return False, "Access forbidden. Check your Notion integration permissions."
        else:
            return False, f"Notion API error: {resp.status_code} {resp.text[:100]}"
    except requests.exceptions.Timeout:
        return False, "Connection to Notion timed out. Please try again."
    except requests.exceptions.RequestException as e:
        return False, f"Network error connecting to Notion: {str(e)}"


def get_accessible_pages(token: str) -> List[Dict[str, str]]:
    """
    Queries Notion search API to find pages that have been shared with this integration.
    Returns:
        List of dictionaries with {"id": page_id, "title": page_title, "icon": icon_emoji}
    """
    url = f"{NOTION_BASE_URL}/search"
    payload = {
        "filter": {"value": "page", "property": "object"},
        "page_size": 25,
        "sort": {"direction": "descending", "timestamp": "last_edited_time"}
    }

    try:
        resp = requests.post(
            url,
            headers=get_notion_headers(token),
            json=payload,
            timeout=NOTION_TIMEOUT
        )
        if resp.status_code != 200:
            logger.warning("Notion search failed: %d %s", resp.status_code, resp.text)
            return []

        data = resp.json()
        results = data.get("results", [])
        pages = []

        for p in results:
            page_id = p.get("id")
            # Extract title from properties
            title = "Untitled Page"
            props = p.get("properties", {})
            for prop_val in props.values():
                if prop_val.get("type") == "title":
                    title_parts = prop_val.get("title", [])
                    if title_parts:
                        title = "".join(t.get("plain_text", "") for t in title_parts).strip() or "Untitled Page"
                    break

            # Icon
            icon_data = p.get("icon")
            icon = "📄"
            if icon_data and icon_data.get("type") == "emoji":
                icon = icon_data.get("emoji", "📄")

            pages.append({
                "id": page_id,
                "title": title,
                "icon": icon,
            })

        return pages
    except Exception as e:
        logger.warning("Failed to retrieve accessible Notion pages: %s", str(e))
        return []


# -------------------------------------------------------------
# Notion Block Formatters
# -------------------------------------------------------------
def _rich_text(content: str, bold: bool = False, italic: bool = False, link_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """Creates a Notion rich text array, truncating to Notion's 2000 character block limit."""
    safe_text = str(content or "")[:2000]
    text_obj: Dict[str, Any] = {"content": safe_text}
    if link_url:
        text_obj["link"] = {"url": link_url[:2000]}

    item: Dict[str, Any] = {
        "type": "text",
        "text": text_obj,
        "annotations": {
            "bold": bold,
            "italic": italic,
            "strikethrough": False,
            "underline": False,
            "code": False,
            "color": "default",
        }
    }
    return [item]


def _heading_2(text: str) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": _rich_text(text)}
    }


def _heading_3(text: str) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": _rich_text(text)}
    }


def _paragraph(text: str, bold: bool = False, italic: bool = False) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text, bold=bold, italic=italic)}
    }


def _bullet(text: str) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich_text(text)}
    }


def _callout(text: str, emoji: str = "💡", color: str = "gray_background") -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(text),
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color
        }
    }


def _divider() -> Dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


# -------------------------------------------------------------
# Single Summary -> Notion Blocks
# -------------------------------------------------------------
def format_single_summary_blocks(summary: Dict[str, Any], title: str, url: str, mode_name: str) -> List[Dict[str, Any]]:
    """Converts a standard WebBrief AI summary into Notion blocks."""
    blocks: List[Dict[str, Any]] = []

    # Metadata callout
    meta_info = f"Source: {url}\nMode: {mode_name} • Generated with WebBrief AI"
    blocks.append(_callout(meta_info, emoji="🌐", color="blue_background"))
    blocks.append(_divider())

    # 1. Overview
    overview = summary.get("overview") or summary.get("tldr") or ""
    if overview:
        blocks.append(_heading_2("Overview"))
        blocks.append(_paragraph(overview))

    # 2. Key Points
    key_points = summary.get("key_points") or summary.get("key_takeaways") or []
    if key_points:
        blocks.append(_heading_2("Key Points"))
        for kp in key_points:
            blocks.append(_bullet(str(kp)))

    # 3. Important Details
    details = summary.get("important_details") or summary.get("core_summary") or []
    if details:
        blocks.append(_heading_2("Important Details"))
        if isinstance(details, list):
            for d in details:
                blocks.append(_bullet(str(d)))
        else:
            blocks.append(_paragraph(str(details)))

    # 4. Conclusion
    conclusion = summary.get("conclusion")
    if conclusion:
        blocks.append(_heading_2("Conclusion"))
        blocks.append(_callout(str(conclusion), emoji="🎯", color="green_background"))

    return blocks


# -------------------------------------------------------------
# Comparative Summary -> Notion Blocks
# -------------------------------------------------------------
def format_comparison_blocks(comp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Converts a WebBrief AI comparative analysis into Notion blocks."""
    blocks: List[Dict[str, Any]] = []

    art_a = comp.get("article_a", {})
    art_b = comp.get("article_b", {})
    title_a = art_a.get("title", "Article A")
    url_a = art_a.get("url", "")
    title_b = art_b.get("title", "Article B")
    url_b = art_b.get("url", "")

    # Header Callout
    meta_text = (
        f"Comparative Research Brief\n"
        f"Source A: {title_a} ({url_a})\n"
        f"Source B: {title_b} ({url_b})"
    )
    blocks.append(_callout(meta_text, emoji="⚖️", color="purple_background"))
    blocks.append(_divider())

    # 1. Individual Overviews
    blocks.append(_heading_2("Source Overviews"))
    if art_a.get("overview"):
        blocks.append(_heading_3(f"Source A: {title_a}"))
        blocks.append(_paragraph(art_a.get("overview")))

    if art_b.get("overview"):
        blocks.append(_heading_3(f"Source B: {title_b}"))
        blocks.append(_paragraph(art_b.get("overview")))

    # 2. Common Arguments & Shared Information
    common = comp.get("common_points", [])
    if common:
        blocks.append(_heading_2("Common Ground & Shared Information"))
        for p in common:
            blocks.append(_bullet(str(p)))

    # 3. Contradictions & Disagreements (Crucial for research)
    contradictions = comp.get("contradictions", [])
    if contradictions:
        blocks.append(_heading_2("Contradictions & Disagreements"))
        for c in contradictions:
            blocks.append(_callout(str(c), emoji="⚠️", color="red_background"))

    # 4. Differing Perspectives
    perspectives = comp.get("differing_perspectives", [])
    if perspectives:
        blocks.append(_heading_2("Differing Perspectives & Framing"))
        for p in perspectives:
            blocks.append(_bullet(str(p)))

    # 5. Key Facts & Claims Comparison
    claims = comp.get("key_facts_claims", [])
    if claims:
        blocks.append(_heading_2("Key Facts & Claims Comparison"))
        for item in claims:
            topic = item.get("topic", "Topic")
            claim_a = item.get("article_a_claim", "")
            claim_b = item.get("article_b_claim", "")
            blocks.append(_heading_3(topic))
            blocks.append(_paragraph(f"• Source A: {claim_a}"))
            blocks.append(_paragraph(f"• Source B: {claim_b}"))

    # 6. Unique Information
    unique_a = comp.get("unique_to_a", [])
    if unique_a:
        blocks.append(_heading_2(f"Unique to {title_a}"))
        for u in unique_a:
            blocks.append(_bullet(str(u)))

    unique_b = comp.get("unique_to_b", [])
    if unique_b:
        blocks.append(_heading_2(f"Unique to {title_b}"))
        for u in unique_b:
            blocks.append(_bullet(str(u)))

    # 7. Conclusions Comparison
    conc_comp = comp.get("conclusions_comparison", {})
    if conc_comp:
        blocks.append(_heading_2("Conclusions Comparison"))
        if conc_comp.get("article_a_conclusion"):
            blocks.append(_paragraph(f"Source A Conclusion: {conc_comp.get('article_a_conclusion')}", italic=True))
        if conc_comp.get("article_b_conclusion"):
            blocks.append(_paragraph(f"Source B Conclusion: {conc_comp.get('article_b_conclusion')}", italic=True))
        if conc_comp.get("comparison_note"):
            blocks.append(_callout(conc_comp.get("comparison_note"), emoji="🔍", color="yellow_background"))

    # 8. Overall Synthesis
    synthesis = comp.get("synthesis", "")
    if synthesis:
        blocks.append(_heading_2("Overall Synthesis & Assessment"))
        blocks.append(_callout(synthesis, emoji="🎯", color="green_background"))

    return blocks


# -------------------------------------------------------------
# Notion Page Creation Engine
# -------------------------------------------------------------
def create_notion_page(
    parent_page_id: str,
    page_title: str,
    blocks: List[Dict[str, Any]],
    token: str,
    emoji_icon: str = "📄"
) -> Dict[str, Any]:
    """
    Creates a new page inside Notion under the specified parent page.
    Handles block batching (Notion max 100 children per request).

    Returns:
        {"success": True, "page_id": ..., "url": ...} or raises NotionError.
    """
    if not token or not token.strip():
        raise NotionError("Missing Notion integration token. Please connect Notion in Settings.")

    clean_parent = parent_page_id.strip().replace("-", "")
    if not clean_parent or not re.match(r"^[0-9a-fA-F]{32}$", clean_parent):
        raise NotionError("Invalid destination Notion Page ID format. Expected a 32-character hexadecimal page ID.")

    # Step 1: Initial page creation with first chunk of blocks (up to 95)
    first_chunk = blocks[:95]
    remaining_chunks = [blocks[i:i + 95] for i in range(95, len(blocks), 95)]

    url = f"{NOTION_BASE_URL}/pages"
    payload = {
        "parent": {"page_id": clean_parent},
        "icon": {"type": "emoji", "emoji": emoji_icon},
        "properties": {
            "title": {
                "title": _rich_text(page_title[:100])
            }
        },
        "children": first_chunk
    }

    try:
        resp = requests.post(
            url,
            headers=get_notion_headers(token),
            json=payload,
            timeout=NOTION_TIMEOUT
        )
    except requests.exceptions.Timeout:
        raise NotionError("Notion request timed out while creating page.")
    except requests.exceptions.RequestException as e:
        raise NotionError(f"Network error communicating with Notion: {str(e)}")

    if resp.status_code == 401:
        raise NotionError("Invalid or expired Notion token. Please reconnect Notion in Settings.")
    elif resp.status_code == 404:
        raise NotionError("Destination page not found. Ensure the page is shared with your integration in Notion (click '...' -> 'Add connections').")
    elif resp.status_code == 403:
        raise NotionError("Notion permission denied. Ensure your integration has 'Insert content' permission.")
    elif resp.status_code == 429:
        raise NotionError("Notion API rate limit exceeded. Please wait a moment and try again.")
    elif resp.status_code not in (200, 201):
        err_msg = resp.text
        try:
            err_json = resp.json()
            err_msg = err_json.get("message", err_msg)
        except Exception:
            pass
        raise NotionError(f"Notion API Error ({resp.status_code}): {err_msg}")

    page_data = resp.json()
    created_page_id = page_data.get("id")
    page_url = page_data.get("url") or f"https://notion.so/{clean_parent}"

    # Step 2: Append any remaining block chunks
    for chunk in remaining_chunks:
        append_url = f"{NOTION_BASE_URL}/blocks/{created_page_id}/children"
        try:
            requests.patch(
                append_url,
                headers=get_notion_headers(token),
                json={"children": chunk},
                timeout=NOTION_TIMEOUT
            )
        except Exception as e:
            logger.warning("Failed to append subsequent block chunk to Notion page: %s", str(e))

    return {
        "success": True,
        "page_id": created_page_id,
        "url": page_url,
        "title": page_title
    }
