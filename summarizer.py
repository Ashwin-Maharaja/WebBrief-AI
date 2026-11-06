import json
import os
import re
from dotenv import load_dotenv
from openai import OpenAI
from chunker import chunk_text, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP

load_dotenv()


class SummarizerError(Exception):
    """Exception raised when AI summarization fails."""
    pass


MODES = {
    "quick": {
        "name": "Quick Summary",
        "directive": (
            "STYLE & MODE DIRECTIVE: Quick Summary\n"
            "- Be exceptionally concise, highlighting only the primary purpose and headline conclusions.\n"
            "- Keep the overview limited to 2-3 tight sentences.\n"
            "- Select only the top 2-3 most essential key points.\n"
            "- Minimize peripheral details; focus on high-impact takeaways."
        )
    },
    "detailed": {
        "name": "Detailed Summary",
        "directive": (
            "STYLE & MODE DIRECTIVE: Detailed Summary\n"
            "- Provide an in-depth, comprehensive explanation of the webpage.\n"
            "- Elaborate on context, causes, background, mechanisms, and implications.\n"
            "- Include thorough key points and comprehensive supporting details."
        )
    },
    "bullet": {
        "name": "Bullet-Point Summary",
        "directive": (
            "STYLE & MODE DIRECTIVE: Bullet-Point Summary\n"
            "- Maximize scannability and structural clarity.\n"
            "- Keep the overview minimal (1-2 sentences).\n"
            "- Present all core insights, arguments, and details as concise, well-formatted bullet points.\n"
            "- Avoid dense prose paragraphs; emphasize scannable takeaways."
        )
    },
    "technical": {
        "name": "Technical Summary",
        "directive": (
            "STYLE & MODE DIRECTIVE: Technical Summary\n"
            "- Strictly preserve domain-specific terminology, acronyms, and technical precision.\n"
            "- Highlight system architecture, parameters, protocols, specifications, and data points.\n"
            "- Retain technical relationships and constraints accurately without oversimplification."
        )
    }
}

DEFAULT_MODE = "quick"


def build_summarization_prompt(article_text, mode=DEFAULT_MODE):
    """
    Constructs the prompt and system instructions for single-pass structured summarization,
    adapting tone and depth based on the selected mode.
    """
    mode_info = MODES.get(mode, MODES[DEFAULT_MODE])
    directive = mode_info["directive"]

    instructions = (
        "You are an expert webpage summarizer. Your task is to produce an accurate, "
        "well-organized, and faithful structured summary of the provided webpage content.\n\n"
        f"{directive}\n\n"
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
        '  "overview": "A cohesive overview of what the webpage is about (tailored to mode style).",\n'
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
        f"Summarize the following webpage content into the structured JSON format using mode '{mode_info['name']}':\n\n{article_text}"
    )

    return instructions, user_input


def build_chunk_map_prompt(chunk_content, chunk_index, total_chunks, mode=DEFAULT_MODE):
    """
    Constructs the prompt for the Map phase: summarizing an individual chunk
    accurately and concisely to extract essential facts and context, adapted to mode.
    """
    mode_info = MODES.get(mode, MODES[DEFAULT_MODE])
    instructions = (
        f"You are summarizing section {chunk_index} of {total_chunks} of a long webpage.\n"
        f"Current summarization mode: {mode_info['name']}.\n"
        "Extract the core facts, key points, and critical context from this section.\n"
        "STRICT FACTUALITY RULES:\n"
        "- Do not extrapolate, assume, or hallucinate.\n"
        "- Preserve the original order of events, arguments, and facts.\n"
        "- Respond in clean, concise prose or bullet points without meta-commentary."
    )

    user_input = f"Webpage Section {chunk_index}/{total_chunks}:\n\n{chunk_content}"
    return instructions, user_input


def build_reduce_prompt(combined_summaries, total_chunks, mode=DEFAULT_MODE):
    """
    Constructs the prompt for the Reduce phase: synthesizing intermediate chunk
    summaries into the final unified structured JSON summary according to mode.
    """
    mode_info = MODES.get(mode, MODES[DEFAULT_MODE])
    directive = mode_info["directive"]

    instructions = (
        f"You are synthesizing intermediate summaries from {total_chunks} sequential sections "
        f"of a long webpage into a single, cohesive, unified structured summary in '{mode_info['name']}' mode.\n\n"
        f"{directive}\n\n"
        "STRICT FACTUALITY AND ACCURACY RULES:\n"
        "1. Strictly preserve the original meaning and factual accuracy of the combined sections.\n"
        "2. Do NOT introduce or assume information that is not present in the section summaries.\n"
        "3. Synthesize the overall narrative, deduplicating overlapping information while preserving key nuances.\n\n"
        "OUTPUT FORMAT:\n"
        "You MUST respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "title": "Unified article or webpage title",\n'
        '  "overview": "A cohesive overview paragraph providing an overarching summary of the full webpage.",\n'
        '  "key_points": [\n'
        '    "First major takeaway from across the article",\n'
        '    "Second major takeaway from across the article"\n'
        "  ],\n"
        '  "important_details": [\n'
        '    "Critical context, metric, or detail from the body",\n'
        '    "Another important supporting fact"\n'
        "  ],\n"
        '  "conclusion": "Final concluding takeaway or outcome (or empty string if none)"\n'
        "}\n\n"
        "Do NOT include markdown formatting like ```json ... ``` or any text outside the JSON object."
    )

    user_input = (
        f"Synthesize the following {total_chunks} sequential section summaries into the final structured JSON summary:\n\n"
        f"{combined_summaries}"
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
    if structured.get("mode_name"):
        parts.append(f"*Mode: {structured['mode_name']}*\n")
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


def _call_openai_responses(client, instructions, user_input):
    """
    Helper to execute an OpenAI call and capture standardized errors.
    """
    try:
        response = client.responses.create(
            model="gpt-5.6-luna",
            instructions=instructions,
            input=user_input
        )
        return response.output_text
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


def summarize_text(article_text, api_key=None, chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=DEFAULT_CHUNK_OVERLAP, mode=DEFAULT_MODE):
    """
    Summarizes webpage text using OpenAI across configurable summarization modes.
    Supports single-pass for short content, or chunking & map-reduce for long webpages.
    
    Modes:
    - quick: Concise overview with highest-impact takeaways.
    - detailed: In-depth explanation with background and nuance.
    - bullet: Scannable bullet points with minimal prose.
    - technical: Preserves technical terminology and specifications.
    
    Returns:
        dict: Validated structured summary with title, overview, key_points, important_details, conclusion, mode, and stats.
    """
    if not article_text or not article_text.strip():
        raise SummarizerError("Cannot summarize empty content.")

    # Validate and normalize mode
    active_mode = (mode or "").strip().lower()
    if active_mode not in MODES:
        active_mode = DEFAULT_MODE

    try:
        from byok import get_configured_api_key
        configured = get_configured_api_key()
    except ImportError:
        configured = os.getenv("OPENAI_API_KEY")

    resolved_key = (api_key or "").strip() or configured

    if not resolved_key:
        raise SummarizerError(
            "OpenAI API key not found. Please configure OPENAI_API_KEY in the environment or provide a key."
        )

    try:
        client = OpenAI(api_key=resolved_key)
    except Exception as e:
        raise SummarizerError(f"Failed to initialize OpenAI client: {str(e)}")

    # Step 1 & 2: Divide content into manageable chunks
    chunks = chunk_text(article_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    if not chunks:
        raise SummarizerError("Webpage does not contain sufficient text content to chunk.")

    total_chunks = len(chunks)

    # Short webpage / Single-chunk path: Direct structured summarization
    if total_chunks == 1:
        instructions, user_input = build_summarization_prompt(chunks[0], mode=active_mode)
        raw_output = _call_openai_responses(client, instructions, user_input)
        result = parse_and_validate_summary(raw_output)
        result["mode"] = active_mode
        result["mode_name"] = MODES[active_mode]["name"]
        result["stats"] = {
            "total_chunks": 1,
            "is_map_reduce": False
        }
        return result

    # Long-form / Multi-chunk path: Map-Reduce Summarization
    intermediate_summaries = []
    chunk_errors = []

    # Map Phase: Summarize each chunk independently
    for idx, chunk in enumerate(chunks, 1):
        try:
            map_inst, map_input = build_chunk_map_prompt(chunk, idx, total_chunks, mode=active_mode)
            chunk_summary = _call_openai_responses(client, map_inst, map_input)
            if chunk_summary and chunk_summary.strip():
                intermediate_summaries.append((idx, chunk_summary.strip()))
        except SummarizerError as e:
            err_text = str(e).lower()
            if "invalid openai api key" in err_text or "rate limit or quota" in err_text:
                raise
            chunk_errors.append((idx, str(e)))

    if not intermediate_summaries:
        error_detail = "; ".join(f"Chunk {i}: {err}" for i, err in chunk_errors[:3])
        raise SummarizerError(f"Failed to process content chunks. Errors encountered: {error_detail}")

    # Combine intermediate summaries preserving original sequential order
    combined_parts = []
    for chunk_num, summary_text in intermediate_summaries:
        combined_parts.append(f"--- Section {chunk_num} of {total_chunks} Summary ---\n{summary_text}")

    combined_text = "\n\n".join(combined_parts)

    # Hierarchical Reduce: if combined intermediate text is exceptionally long
    if len(combined_text) > max(chunk_size * 4, 15000):
        sub_chunks = chunk_text(combined_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        sub_summaries = []
        for s_idx, s_chunk in enumerate(sub_chunks, 1):
            s_inst, s_input = build_chunk_map_prompt(s_chunk, s_idx, len(sub_chunks), mode=active_mode)
            sub_res = _call_openai_responses(client, s_inst, s_input)
            sub_summaries.append(sub_res.strip())
        combined_text = "\n\n".join(sub_summaries)

    # Reduce Phase: Generate final structured summary from combined intermediate summaries
    reduce_inst, reduce_input = build_reduce_prompt(combined_text, total_chunks, mode=active_mode)
    final_raw_output = _call_openai_responses(client, reduce_inst, reduce_input)
    result = parse_and_validate_summary(final_raw_output)

    result["mode"] = active_mode
    result["mode_name"] = MODES[active_mode]["name"]
    result["stats"] = {
        "total_chunks": total_chunks,
        "processed_chunks": len(intermediate_summaries),
        "failed_chunks": len(chunk_errors),
        "is_map_reduce": True
    }

    return result
