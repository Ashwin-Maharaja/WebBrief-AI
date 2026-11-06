import json
import os
import re
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class SummarizerError(Exception):
    """Exception raised when AI summarization fails."""
    pass


def build_summarization_prompt(article_text):
    """
    Constructs the prompt and system instructions for structured summarization.
    Separates prompt engineering from network/API communication.
    """
    instructions = (
        "You are an expert webpage summarizer. Your task is to produce an accurate, "
        "well-organized, and faithful structured summary of the provided webpage content.\n\n"
        "STRICT FACTUALITY AND ACCURACY RULES:\n"
        "1. Strictly preserve the original meaning and factual accuracy of the source.\n"
        "2. Do NOT introduce or assume information, metrics, or claims that are not present in the source.\n"
        "3. If the webpage has limited content or lacks sufficient information for specific sections "
        "(such as Important Details or Conclusion), keep them brief or leave the list/string empty. "
        "Never hallucinate or invent filler details.\n\n"
        "OUTPUT FORMAT:\n"
        "You MUST respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "title": "Article or webpage title (infer from content if not explicitly stated)",\n'
        '  "overview": "A concise paragraph giving a high-level overview of what the webpage is about.",\n'
        '  "key_points": [\n'
        '    "First key takeaway or major point",\n'
        '    "Second key takeaway or major point"\n'
        "  ],\n"
        '  "important_details": [\n'
        '    "Specific detail, specification, background, or supporting context",\n'
        '    "Another relevant detail"\n'
        "  ],\n"
        '  "conclusion": "A concise concluding takeaway, status, or call to action (or empty string if none)"\n'
        "}\n\n"
        "Do NOT include markdown formatting like ```json ... ``` or any text outside the JSON object."
    )

    user_input = (
        f"Summarize the following webpage content into the structured JSON format:\n\n{article_text}"
    )

    return instructions, user_input


def parse_and_validate_summary(raw_text):
    """
    Parses and validates the structured summary output from the AI model.
    Handles markdown code fences, malformed formatting, and missing keys gracefully.
    """
    if not raw_text or not isinstance(raw_text, str):
        raise SummarizerError("Received empty response from the AI model.")

    cleaned = raw_text.strip()

    # Strip markdown code blocks if the model enclosed the JSON
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    parsed = None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: attempt to extract JSON object using regex
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

    # If JSON parsing completely failed, build a graceful structured fallback from raw text
    if not isinstance(parsed, dict):
        paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
        title = paragraphs[0][:100] if paragraphs else "Webpage Summary"
        overview = paragraphs[1] if len(paragraphs) > 1 else (paragraphs[0] if paragraphs else "No overview available.")
        remaining = paragraphs[2:] if len(paragraphs) > 2 else []

        parsed = {
            "title": title,
            "overview": overview,
            "key_points": remaining[:3] if remaining else [],
            "important_details": remaining[3:6] if len(remaining) > 3 else [],
            "conclusion": remaining[-1] if len(remaining) > 6 else ""
        }

    # Validate and normalize keys
    title = str(parsed.get("title") or "Webpage Summary").strip()
    overview = str(parsed.get("overview") or "").strip()

    def normalize_list(val):
        if isinstance(val, list):
            return [str(item).strip() for item in val if item and str(item).strip()]
        elif isinstance(val, str) and val.strip():
            # If returned as a bulleted string, split into lines
            lines = [re.sub(r"^[-*•\d.]+\s*", "", line).strip() for line in val.split("\n") if line.strip()]
            return [line for line in lines if line]
        return []

    key_points = normalize_list(parsed.get("key_points"))
    important_details = normalize_list(parsed.get("important_details"))
    conclusion = str(parsed.get("conclusion") or "").strip()

    return {
        "title": title,
        "overview": overview,
        "key_points": key_points,
        "important_details": important_details,
        "conclusion": conclusion
    }


def format_summary_as_text(structured):
    """
    Formats a structured summary dictionary into clean markdown text
    (ideal for copying to clipboard or legacy text fallback).
    """
    if isinstance(structured, str):
        return structured

    parts = []
    if structured.get("title"):
        parts.append(f"# {structured['title']}\n")
    if structured.get("overview"):
        parts.append(f"## Overview\n{structured['overview']}\n")
    if structured.get("key_points"):
        points_str = "\n".join(f"- {pt}" for pt in structured["key_points"])
        parts.append(f"## Key Points\n{points_str}\n")
    if structured.get("important_details"):
        details_str = "\n".join(f"- {d}" for d in structured["important_details"])
        parts.append(f"## Important Details\n{details_str}\n")
    if structured.get("conclusion"):
        parts.append(f"## Conclusion\n{structured['conclusion']}\n")

    return "\n".join(parts).strip()


def summarize_text(article_text, api_key=None):
    """
    Summarizes webpage text using OpenAI with structured JSON output.
    Supports BYOK (Bring Your Own Key) or falls back to OPENAI_API_KEY from environment.
    
    Returns:
        dict: A validated dictionary with title, overview, key_points, important_details, conclusion.
        
    Raises:
        SummarizerError: When API key is missing or OpenAI request fails.
    """
    if not article_text or not article_text.strip():
        raise SummarizerError("Cannot summarize empty content.")

    # Support BYOK while falling back to environment variable
    resolved_key = (api_key or "").strip() or os.getenv("OPENAI_API_KEY")

    if not resolved_key:
        raise SummarizerError(
            "OpenAI API key not found. Please configure OPENAI_API_KEY in the environment or provide a key."
        )

    try:
        client = OpenAI(api_key=resolved_key)
    except Exception as e:
        raise SummarizerError(f"Failed to initialize OpenAI client: {str(e)}")

    instructions, user_input = build_summarization_prompt(article_text)

    try:
        response = client.responses.create(
            model="gpt-5.6-luna",
            instructions=instructions,
            input=user_input
        )
        raw_output = response.output_text
        return parse_and_validate_summary(raw_output)
    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e)

        if "AuthenticationError" in err_name or "invalid_api_key" in err_msg.lower():
            raise SummarizerError("Invalid OpenAI API key. Please check your credentials.")
        elif "RateLimitError" in err_name or "insufficient_quota" in err_msg.lower():
            raise SummarizerError("OpenAI rate limit or quota exceeded. Please check your OpenAI account.")
        elif "APIConnectionError" in err_name:
            raise SummarizerError("Could not connect to OpenAI API. Please check your internet connection.")
        elif "NotFoundError" in err_name:
            raise SummarizerError(f"Specified OpenAI model was not found: {err_msg}")
        elif "BadRequestError" in err_name:
            raise SummarizerError(f"OpenAI request rejected: {err_msg}")
        elif isinstance(e, SummarizerError):
            raise
        else:
            raise SummarizerError(f"Summarization failed: {err_msg}")
