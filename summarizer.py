import json
import logging
import os
import re
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from chunker import chunk_text, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP
from services.providers.router import AIRouter, RouterResult
from services.providers.exceptions import ProviderError

load_dotenv()
logger = logging.getLogger(__name__)


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


def build_summarization_prompt(article_text, mode=DEFAULT_MODE, custom_directive=None, custom_directive_enabled=True):
    """
    Constructs the prompt and system instructions for single-pass structured summarization,
    adapting tone and depth based on the selected mode, with optional user custom directives.
    """
    mode_info = MODES.get(mode, MODES[DEFAULT_MODE])
    directive = mode_info["directive"]

    custom_block = ""
    if custom_directive and custom_directive_enabled:
        clean_directive = str(custom_directive).strip()[:1000]
        if clean_directive:
            custom_block = (
                f"\n\nUSER CUSTOM INSTRUCTIONS:\n"
                f"The user has specified these additional guidelines for this summary:\n"
                f"\"{clean_directive}\"\n"
                f"- Harmonize these user preferences with the '{mode_info['name']}' style.\n"
                f"- Never invent or extrapolate facts to satisfy custom instructions.\n"
                f"- Strictly maintain adherence to the required JSON output schema."
            )

    instructions = (
        "You are an expert webpage summarizer. Your task is to produce an accurate, "
        "well-organized, and faithful structured summary of the provided webpage content.\n\n"
        f"{directive}{custom_block}\n\n"
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


def build_reduce_prompt(combined_summaries, total_chunks, mode=DEFAULT_MODE, custom_directive=None, custom_directive_enabled=True):
    """
    Constructs the prompt for the Reduce phase: synthesizing intermediate chunk
    summaries into the final unified structured JSON summary according to mode,
    with optional user custom directives.
    """
    mode_info = MODES.get(mode, MODES[DEFAULT_MODE])
    directive = mode_info["directive"]

    custom_block = ""
    if custom_directive and custom_directive_enabled:
        clean_directive = str(custom_directive).strip()[:1000]
        if clean_directive:
            custom_block = (
                f"\n\nUSER CUSTOM INSTRUCTIONS:\n"
                f"The user has specified these additional guidelines:\n"
                f"\"{clean_directive}\"\n"
                f"- Harmonize these user preferences with the overarching summary synthesis without fabricating information."
            )

    instructions = (
        f"You are synthesizing intermediate summaries from {total_chunks} sequential sections "
        f"of a long webpage into a single, cohesive, unified structured summary in '{mode_info['name']}' mode.\n\n"
        f"{directive}{custom_block}\n\n"
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


def parse_and_validate_summary(raw_text_or_dict):
    """
    Parses and validates the structured summary output from the AI model.
    Handles markdown code fences, malformed formatting, and missing keys gracefully.
    """
    if not raw_text_or_dict:
        raise SummarizerError("Received empty response from the AI model.")

    if isinstance(raw_text_or_dict, dict):
        parsed = raw_text_or_dict
    elif isinstance(raw_text_or_dict, str):
        cleaned = raw_text_or_dict.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        parsed = None
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
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
    else:
        parsed = {"overview": str(raw_text_or_dict)}

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


def summarize_single_chunk(
    chunk_content: str,
    chunk_index: int,
    total_chunks: int,
    mode: str = DEFAULT_MODE,
    provider_config: Optional[Dict[str, Any]] = None,
    router: Optional[AIRouter] = None,
) -> Tuple[str, RouterResult]:
    """
    Summarizes an individual content chunk in the Map phase (v1.7.0).
    Returns the generated summary text and the provider routing metadata.
    """
    if router is None:
        from byok import get_router, has_valid_config
        req_payload = provider_config or {}
        if not has_valid_config(request_data=req_payload):
            raise SummarizerError("No AI provider configured.")
        router = get_router(request_data=req_payload)

    active_mode = mode if mode in MODES else DEFAULT_MODE
    map_inst, map_input = build_chunk_map_prompt(chunk_content, chunk_index, total_chunks, mode=active_mode)
    messages = [
        {"role": "system", "content": map_inst},
        {"role": "user", "content": map_input},
    ]
    try:
        map_res = router.generate_completion(messages=messages)
    except ProviderError as pe:
        raise SummarizerError(str(pe))
    except Exception as e:
        raise SummarizerError(f"Chunk {chunk_index} summarization failed: {str(e)}")

    raw_content = map_res.content
    if isinstance(raw_content, dict):
        content = str(raw_content.get("content") or raw_content.get("text") or "").strip()
    else:
        content = str(raw_content or "").strip()
    return content, map_res


def synthesize_reduce_summary(
    intermediate_summaries: List[Tuple[int, str]],
    total_chunks: int,
    mode: str = DEFAULT_MODE,
    custom_directive: Optional[str] = None,
    custom_directive_enabled: Optional[bool] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    provider_config: Optional[Dict[str, Any]] = None,
    router: Optional[AIRouter] = None,
) -> Dict[str, Any]:
    """
    Performs the Reduce synthesis phase from intermediate chunk summaries into
    a unified, structured JSON summary according to mode (v1.7.0).
    """
    if not intermediate_summaries:
        raise SummarizerError("Cannot synthesize summary: no intermediate chunk summaries provided.")

    if router is None:
        from byok import get_router, has_valid_config
        req_payload = provider_config or {}
        if not has_valid_config(request_data=req_payload):
            raise SummarizerError("No AI provider configured.")
        router = get_router(request_data=req_payload)

    active_mode = mode if mode in MODES else DEFAULT_MODE
    active_directive = str(custom_directive or "").strip()[:1000]
    active_enabled = bool(custom_directive_enabled) if custom_directive_enabled is not None else bool(active_directive)

    combined_parts = []
    # Preserve original sequential order
    sorted_intermediates = sorted(intermediate_summaries, key=lambda x: x[0])
    for chunk_num, summary_text in sorted_intermediates:
        combined_parts.append(f"--- Section {chunk_num} of {total_chunks} Summary ---\n{summary_text}")

    combined_text = "\n\n".join(combined_parts)

    # Hierarchical Reduce: if combined intermediate text is exceptionally long
    if len(combined_text) > max(chunk_size * 4, 15000):
        sub_chunks = chunk_text(combined_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        sub_summaries = []
        for s_idx, s_chunk in enumerate(sub_chunks, 1):
            s_inst, s_input = build_chunk_map_prompt(s_chunk, s_idx, len(sub_chunks), mode=active_mode)
            sub_res = router.generate_completion(messages=[
                {"role": "system", "content": s_inst},
                {"role": "user", "content": s_input},
            ])
            sub_summaries.append((sub_res.content or "").strip())
        combined_text = "\n\n".join(sub_summaries)

    reduce_inst, reduce_input = build_reduce_prompt(
        combined_text,
        total_chunks,
        mode=active_mode,
        custom_directive=active_directive,
        custom_directive_enabled=active_enabled,
    )
    reduce_messages = [
        {"role": "system", "content": reduce_inst},
        {"role": "user", "content": reduce_input},
    ]

    try:
        final_res = router.generate_structured_json(messages=reduce_messages)
    except ProviderError as pe:
        raise SummarizerError(str(pe))
    except Exception as e:
        raise SummarizerError(f"Failed to synthesize final summary: {str(e)}")

    result = parse_and_validate_summary(final_res.content)
    result["mode"] = active_mode
    result["mode_name"] = MODES[active_mode]["name"]
    if active_directive and active_enabled:
        result["custom_directive_applied"] = True
        result["custom_directive"] = active_directive

    # Provider transparency metadata (Phase 9)
    result["provider"] = final_res.provider_id
    result["provider_name"] = final_res.provider_name
    result["model"] = final_res.model
    result["switched_to_fallback"] = final_res.switched_to_fallback
    if final_res.switched_to_fallback:
        result["fallback_reason"] = final_res.fallback_reason

    result["stats"] = {
        "total_chunks": total_chunks,
        "processed_chunks": len(intermediate_summaries),
        "is_map_reduce": True
    }
    return result


def summarize_text(
    article_text: str,
    api_key: Optional[str] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    mode: str = DEFAULT_MODE,
    custom_directive: Optional[str] = None,
    custom_directive_enabled: Optional[bool] = None,
    router: Optional[AIRouter] = None,
    provider_config: Optional[Dict[str, Any]] = None,
    job_id: Optional[str] = None,
    progress_callback: Optional[Any] = None,
):
    """
    Summarizes webpage text using modular multi-provider AI (Cloudflare Workers AI or GroqCloud).
    Supports single-pass for short content, or chunking & map-reduce for long webpages.
    Supports user-defined custom prompt directives combined harmoniously with the selected mode.
    Supports resumable checkpointing and recovery when job_id is provided (v1.7.0).

    Returns:
        dict: Validated structured summary with title, overview, key_points, important_details, conclusion,
              mode, stats, and provider transparency metadata.
    """
    if not article_text or not article_text.strip():
        raise SummarizerError("Cannot summarize empty content.")

    # Validate and normalize mode
    active_mode = (mode or "").strip().lower()
    if active_mode not in MODES:
        active_mode = DEFAULT_MODE

    # Resolve custom prompt directives
    active_directive = ""
    active_enabled = False

    if custom_directive is not None:
        active_directive = str(custom_directive).strip()[:1000]
        if custom_directive_enabled is not None:
            active_enabled = bool(custom_directive_enabled)
        else:
            active_enabled = bool(active_directive)
    else:
        try:
            from history import get_custom_directives
            dir_info = get_custom_directives()
            active_directive = dir_info.get("custom_directive", "")
            active_enabled = dir_info.get("custom_directive_enabled", False)
        except Exception:
            active_directive = ""
            active_enabled = False

    # Instantiate or retrieve AIRouter
    if router is None:
        from byok import get_router, has_valid_config
        req_payload = provider_config or {}
        if api_key:
            # If a single key was passed, handle Groq key or token
            if api_key.startswith("gsk_"):
                req_payload["groq_api_key"] = api_key
                req_payload["primary_provider"] = "groq"
            else:
                req_payload["api_key"] = api_key

        if not has_valid_config(request_data=req_payload):
            raise SummarizerError(
                "No AI provider configured. Please configure Cloudflare Workers AI or GroqCloud in Settings or Setup."
            )
        router = get_router(request_data=req_payload)

    # Step 1 & 2: Divide content into manageable chunks
    chunks = chunk_text(article_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not chunks:
        raise SummarizerError("Webpage does not contain sufficient text content to chunk.")

    total_chunks = len(chunks)

    # Short webpage / Single-chunk path: Direct structured summarization
    if total_chunks == 1:
        instructions, user_input = build_summarization_prompt(
            chunks[0],
            mode=active_mode,
            custom_directive=active_directive,
            custom_directive_enabled=active_enabled,
        )
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_input},
        ]
        try:
            router_res = router.generate_structured_json(messages=messages)
        except ProviderError as pe:
            raise SummarizerError(str(pe))
        except Exception as e:
            raise SummarizerError(f"Summarization failed: {str(e)}")

        result = parse_and_validate_summary(router_res.content)
        result["mode"] = active_mode
        result["mode_name"] = MODES[active_mode]["name"]
        if active_directive and active_enabled:
            result["custom_directive_applied"] = True
            result["custom_directive"] = active_directive

        # Provider transparency metadata (Phase 9)
        result["provider"] = router_res.provider_id
        result["provider_name"] = router_res.provider_name
        result["model"] = router_res.model
        result["switched_to_fallback"] = router_res.switched_to_fallback
        if router_res.switched_to_fallback:
            result["fallback_reason"] = router_res.fallback_reason

        result["stats"] = {
            "total_chunks": 1,
            "is_map_reduce": False
        }

        if job_id:
            try:
                import job_manager
                job_manager.complete_job(job_id, result)
            except Exception as e:
                logger.debug("Job completion record deferred: %s", str(e))

        return result

    # Long-form / Multi-chunk path: Map-Reduce Summarization with Checkpoint Recovery
    intermediate_summaries: List[Tuple[int, str]] = []
    completed_indices: Dict[int, str] = {}

    # Inspect persistent checkpoints if job_id provided
    if job_id:
        try:
            import job_manager
            existing_chunks = job_manager.get_job_chunks(job_id)
            if not existing_chunks:
                job_manager.initialize_job_chunks(job_id, chunks, article_text=article_text)
            else:
                for ec in existing_chunks:
                    if ec["status"] == "completed" and ec.get("summary_output"):
                        idx = ec["chunk_index"]
                        completed_indices[idx] = ec["summary_output"]
                        intermediate_summaries.append((idx, ec["summary_output"]))
            job_manager.update_job_status(job_id, job_manager.STATUS_PROCESSING)
        except Exception as e:
            logger.warning("Checkpoint inspection failed for job %s: %s", job_id, str(e))

    # Map Phase: Summarize each chunk independently
    chunk_errors = []
    for idx, chunk in enumerate(chunks, 1):
        # Check if already completed and checkpointed
        if idx in completed_indices:
            if progress_callback:
                progress_callback({
                    "event": "chunk_completed",
                    "chunk_index": idx,
                    "total_chunks": total_chunks,
                    "resumed_from_cache": True,
                })
            continue

        # Check if job was paused or cancelled by user
        if job_id:
            try:
                import job_manager
                job_rec = job_manager.get_job(job_id)
                if job_rec and job_rec.get("status") in (job_manager.STATUS_PAUSED, job_manager.STATUS_CANCELLED):
                    status_name = job_rec.get("status")
                    raise SummarizerError(f"Job execution was {status_name.lower()} by user.")
            except SummarizerError:
                raise
            except Exception:
                pass

        try:
            content, map_res = summarize_single_chunk(
                chunk_content=chunk,
                chunk_index=idx,
                total_chunks=total_chunks,
                mode=active_mode,
                provider_config=provider_config,
                router=router,
            )
            if content:
                intermediate_summaries.append((idx, content))

                # Immediately save completed chunk checkpoint before proceeding!
                if job_id:
                    try:
                        import job_manager
                        job_manager.save_chunk_checkpoint(
                            job_id=job_id,
                            chunk_index=idx,
                            summary_output=content,
                            provider=map_res.provider_id,
                            model=map_res.model,
                        )
                    except Exception as ce:
                        logger.warning("Failed to persist chunk checkpoint: %s", str(ce))

                if progress_callback:
                    progress_callback({
                        "event": "chunk_completed",
                        "chunk_index": idx,
                        "total_chunks": total_chunks,
                        "provider": map_res.provider_id,
                        "model": map_res.model,
                        "resumed_from_cache": False,
                    })

        except ProviderError as pe:
            if job_id:
                try:
                    import job_manager
                    job_manager.record_chunk_failure(job_id, idx, str(pe))
                except Exception:
                    pass
            # Fatal authentication errors terminate map immediately
            if "invalid" in str(pe).lower() or "unauthorized" in str(pe).lower():
                raise SummarizerError(str(pe))
            chunk_errors.append((idx, str(pe)))
            raise SummarizerError(f"Chunk {idx} failed: {str(pe)}")
        except Exception as e:
            if job_id:
                try:
                    import job_manager
                    job_manager.record_chunk_failure(job_id, idx, str(e))
                except Exception:
                    pass
            chunk_errors.append((idx, str(e)))
            raise SummarizerError(f"Chunk {idx} failed: {str(e)}")

    if not intermediate_summaries:
        error_detail = "; ".join(f"Chunk {i}: {err}" for i, err in chunk_errors[:3])
        raise SummarizerError(f"Failed to process content chunks. Errors encountered: {error_detail}")

    # Reduce Phase: Generate final structured summary
    if job_id:
        try:
            import job_manager
            job_manager.update_job_status(job_id, job_manager.STATUS_SYNTHESIZING)
        except Exception:
            pass

    final_result = synthesize_reduce_summary(
        intermediate_summaries=intermediate_summaries,
        total_chunks=total_chunks,
        mode=active_mode,
        custom_directive=active_directive,
        custom_directive_enabled=active_enabled,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        provider_config=provider_config,
        router=router,
    )

    if job_id:
        try:
            import job_manager
            job_manager.complete_job(job_id, final_result)
        except Exception as ce:
            logger.warning("Failed to complete job record %s: %s", job_id, str(ce))

    return final_result
