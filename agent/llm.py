"""LLM clients, media transcription/description, usage accounting + encoder.

Extracted from ``agent.handler`` (Plano 23 · Fase B5, master §2.3). Cohesive home
for everything that talks to the Techify LLM proxy *outside* the agentic AGNO
loop, plus the usage bookkeeping shared by every billable call:

* :func:`get_client` / :func:`get_async_client` — cached OpenAI / AsyncOpenAI
  clients pointed at the Techify proxy (``LLM_API_BASE_URL``).
* :func:`record_usage` / :func:`record_usage_tokens` — write a ``usage`` row and
  thread tokens/cost into the current execution, then nudge the balance monitor.
* :func:`transcribe_audio` / :func:`describe_image` / :func:`transcribe_document`
  — direct, NON-agentic media calls (the agent's reasoning loop lives in
  ``agno_engine``).
* :func:`encode_history_for_split` — re-encode assistant history as JSON arrays
  for the ``split_messages`` output format.

Every function takes the ``handler`` so it can read live config (``api_key`` /
``audio_model`` / …), the cached clients, ``pricing_fn`` and ``_get_contact``.
``AgentHandler`` keeps thin delegates with the same method names so existing
callers (webhook/sandbox/messaging_service/tests) are unchanged.

DECISION (Plano 23 Q1): ``generate_improvement`` keeps an ISOLATED SYNC client —
it reuses :func:`get_client` here rather than being forced async; less churn.

Naming: user-visible "OpenRouter" wording is migrated to "Techify" in the
messages this module owns (the provider is the Techify proxy).
"""

from __future__ import annotations

import base64
import html as _html
import json
import logging
import mimetypes
import re
import zipfile
from pathlib import Path

from openai import OpenAI, AsyncOpenAI

from agent.execution import add_execution_usage, track_step
from config.settings import LLM_API_BASE_URL

logger = logging.getLogger(__name__)

# Plain-text document extensions read directly from disk (no LLM needed).
TEXT_DOC_EXTS = {
    "txt", "text", "md", "markdown", "csv", "tsv", "log", "json", "xml",
    "html", "htm", "yaml", "yml", "ini", "cfg", "conf", "srt", "vtt", "rtf",
}
# Max characters of locally-extracted text fed back as the "transcription".
DOC_TEXT_LIMIT = 20000


# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #
def get_client(handler) -> OpenAI:
    if handler._client is None or handler._client.api_key != handler.api_key:
        handler._client = OpenAI(
            base_url=LLM_API_BASE_URL,
            api_key=handler.api_key,
        )
    return handler._client


def get_async_client(handler) -> AsyncOpenAI:
    if handler._async_client is None or handler._async_client.api_key != handler.api_key:
        handler._async_client = AsyncOpenAI(
            base_url=LLM_API_BASE_URL,
            api_key=handler.api_key,
        )
    return handler._async_client


def test_api_key(api_key: str) -> tuple[bool, str]:
    """Test if an API key is valid against the Techify proxy."""
    try:
        client = OpenAI(
            base_url=LLM_API_BASE_URL,
            api_key=api_key,
        )
        client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "test"}],
            max_tokens=5,
        )
        return True, "API key válida!"
    except Exception as e:
        return False, f"Erro: {e}"


# --------------------------------------------------------------------------- #
# Usage accounting
# --------------------------------------------------------------------------- #
def _trigger_balance_check() -> None:
    # Trigger a low-balance check after every billable call. The monitor
    # rate-limits actual fetches so this is cheap on a hot path.
    try:
        from server import balance_monitor
        balance_monitor.trigger_check_async()
    except Exception:
        pass


def record_usage(handler, phone: str, call_type: str, model: str, response) -> None:
    """Extract usage from an OpenAI-compatible response and record it."""
    try:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or 0
        cost_usd = 0.0
        if handler.pricing_fn:
            prompt_price, completion_price = handler.pricing_fn(model)
            cost_usd = (prompt_tokens * prompt_price) + (completion_tokens * completion_price)
        contact = handler._get_contact(phone)
        contact.add_usage(call_type, model, prompt_tokens, completion_tokens, total_tokens, cost_usd)
        add_execution_usage(total_tokens, cost_usd)
        logger.debug("Usage recorded for %s: %s %s tokens=%d cost=%.6f",
                     phone, call_type, model, total_tokens, cost_usd)
    except Exception as e:
        logger.warning("Failed to record usage: %s", e)
    _trigger_balance_check()


def record_usage_tokens(handler, phone: str, call_type: str, model: str,
                        prompt_tokens: int, completion_tokens: int,
                        total_tokens: int) -> None:
    """Record usage from explicit token counts (AGNO metrics path).

    Mirrors :func:`record_usage` but takes raw token numbers instead of an
    OpenAI response object, since the AGNO engine reports usage via
    ``RunMetrics`` rather than a ``response.usage`` attribute.
    """
    try:
        cost_usd = 0.0
        if handler.pricing_fn:
            prompt_price, completion_price = handler.pricing_fn(model)
            cost_usd = (prompt_tokens * prompt_price) + (completion_tokens * completion_price)
        contact = handler._get_contact(phone)
        contact.add_usage(call_type, model, prompt_tokens, completion_tokens,
                          total_tokens, cost_usd)
        add_execution_usage(total_tokens, cost_usd)
        logger.debug("Usage recorded for %s: %s %s tokens=%d cost=%.6f",
                     phone, call_type, model, total_tokens, cost_usd)
    except Exception as e:
        logger.warning("Failed to record usage: %s", e)
    _trigger_balance_check()


# --------------------------------------------------------------------------- #
# Media: audio / image / document (direct, non-agentic)
# --------------------------------------------------------------------------- #
def transcribe_audio(handler, audio_path: str, phone: str = "") -> str:
    """Transcribe an audio file using the configured audio model."""
    if not handler.api_key:
        return ""
    try:
        p = Path(audio_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        if not p.exists():
            logger.warning("Audio file not found for transcription: %s", audio_path)
            return ""
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode()
        # Determine format from extension
        ext = p.suffix.lower().lstrip(".")
        if ext in ("oga", "ogg", "opus"):
            fmt = "ogg"
        elif ext == "mp3":
            fmt = "mp3"
        elif ext == "wav":
            fmt = "wav"
        else:
            fmt = "ogg"

        client = get_client(handler)
        response = client.chat.completions.create(
            model=handler.audio_model,
            timeout=60,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": b64, "format": fmt},
                    },
                    {
                        "type": "text",
                        "text": "Transcreva este áudio fielmente em português. Retorne apenas a transcrição, sem comentários adicionais.",
                    },
                ],
            }],
            max_tokens=2048,
        )
        record_usage(handler, phone, "audio", handler.audio_model, response)
        result = response.choices[0].message.content.strip()
        track_step("media_processed", {
            "type": "audio",
            "model": handler.audio_model,
            "transcription_length": len(result),
        })
        logger.info("Audio transcribed (%d chars): %s", len(result), result[:80])
        return result
    except Exception as e:
        logger.error("Audio transcription failed: %s", e)
        track_step("error", {"error": str(e), "phase": "audio_transcription"}, status="error")
        return ""


def describe_image(handler, image_path: str, phone: str = "") -> str:
    """Describe an image using the configured image model."""
    if not handler.api_key:
        return ""
    try:
        p = Path(image_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        if not p.exists():
            logger.warning("Image file not found for description: %s", image_path)
            return ""
        data = p.read_bytes()
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        b64 = base64.b64encode(data).decode()

        client = get_client(handler)
        response = client.chat.completions.create(
            model=handler.image_model,
            timeout=60,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                    {
                        "type": "text",
                        "text": "Descreva detalhadamente o conteúdo desta imagem em português.",
                    },
                ],
            }],
            max_tokens=1024,
        )
        record_usage(handler, phone, "image", handler.image_model, response)
        result = response.choices[0].message.content.strip()
        track_step("media_processed", {
            "type": "image",
            "model": handler.image_model,
            "description_length": len(result),
        })
        logger.info("Image described (%d chars): %s", len(result), result[:80])
        return result
    except Exception as e:
        logger.error("Image description failed: %s", e)
        track_step("error", {"error": str(e), "phase": "image_description"}, status="error")
        return ""


def doc_kind(file_name: str, path: Path, mimetype: str) -> str:
    """Classify a document into pdf | docx | text | unsupported.

    Extension (from the original filename first, then the on-disk path)
    wins; mimetype is the fallback since GOWA's auto-download path is often
    UUID-based without a usable suffix.
    """
    ext = ""
    for cand in (file_name, str(path)):
        if cand:
            e = Path(cand).suffix.lower().lstrip(".")
            if e:
                ext = e
                break
    mt = (mimetype or "").lower()
    if ext == "pdf" or "pdf" in mt:
        return "pdf"
    if ext == "docx" or "wordprocessingml" in mt:
        return "docx"
    if (ext in TEXT_DOC_EXTS or mt.startswith("text/")
            or mt in ("application/json", "application/xml")):
        return "text"
    return "unsupported"


def extract_docx_text(p: Path) -> str:
    """Extract visible text from a .docx (zip of XML) using stdlib only."""
    try:
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except Exception as e:
        logger.warning("docx extraction failed for %s: %s", p, e)
        return ""
    # Paragraph + line breaks → newlines, then strip every tag.
    xml = xml.replace("</w:p>", "\n").replace("<w:br/>", "\n")
    text = re.sub(r"<[^>]+>", "", xml)
    return _html.unescape(text).strip()


def transcribe_document(handler, document_path: str, phone: str = "",
                        file_name: str = "", mimetype: str = "") -> str:
    """Read/transcribe a document (PDF, DOCX, plain text) into text.

    PDFs go to the configured ``document_model`` via the OpenRouter-style
    ``file`` content part (the model handles both digital and scanned PDFs).
    DOCX and plain-text files are extracted locally with stdlib — no LLM
    call needed. Unsupported formats (legacy .doc, spreadsheets, …) return
    an empty string so the caller falls back to just the document label.
    """
    try:
        p = Path(document_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        if not p.exists():
            logger.warning("Document not found for transcription: %s", document_path)
            return ""

        kind = doc_kind(file_name, p, mimetype)

        if kind == "docx":
            result = extract_docx_text(p)[: DOC_TEXT_LIMIT].strip()
            if result:
                track_step("media_processed", {
                    "type": "document", "model": "local-docx",
                    "transcription_length": len(result),
                })
                logger.info("Document (docx) extracted (%d chars)", len(result))
            return result

        if kind == "text":
            try:
                result = p.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning("Text document read failed for %s: %s", p, e)
                return ""
            result = result[: DOC_TEXT_LIMIT].strip()
            if result:
                track_step("media_processed", {
                    "type": "document", "model": "local-text",
                    "transcription_length": len(result),
                })
                logger.info("Document (text) read (%d chars)", len(result))
            return result

        if kind != "pdf":
            logger.info("Document type unsupported for transcription: %s (%s)",
                        file_name or document_path, mimetype)
            return ""

        # PDF → LLM file input.
        if not handler.api_key:
            return ""
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode()
        client = get_client(handler)
        response = client.chat.completions.create(
            model=handler.document_model,
            timeout=120,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file": {
                            "filename": file_name or p.name or "document.pdf",
                            "file_data": f"data:application/pdf;base64,{b64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extraia e transcreva todo o conteúdo textual deste "
                            "documento em português brasileiro, incluindo tabelas e "
                            "dados relevantes de forma organizada. Retorne apenas o "
                            "conteúdo do documento, sem comentários adicionais."
                        ),
                    },
                ],
            }],
            max_tokens=4096,
        )
        record_usage(handler, phone, "document", handler.document_model, response)
        result = (response.choices[0].message.content or "").strip()
        track_step("media_processed", {
            "type": "document",
            "model": handler.document_model,
            "transcription_length": len(result),
        })
        logger.info("Document transcribed (%d chars): %s", len(result), result[:80])
        return result
    except Exception as e:
        logger.error("Document transcription failed: %s", e)
        track_step("error", {"error": str(e), "phase": "document_transcription"}, status="error")
        return ""


# --------------------------------------------------------------------------- #
# History encoder (split_messages format)
# --------------------------------------------------------------------------- #
def encode_history_for_split(context_messages: list[dict]) -> list[dict]:
    """Re-encode assistant turns as JSON arrays for the split_messages format.

    When split_messages is on, the model is asked to answer with a JSON array
    of strings. But the assistant history is stored already split into clean
    plain text, so the model SEES its own past turns as plain text and mimics
    that pattern — drifting out of the JSON format. The presence of tools
    amplifies this drift dramatically (measured: 1/10 vs 15/15 success).

    Fix: present each assistant turn to the model in the SAME JSON-array shape
    it must produce. Consecutive assistant messages (one turn's split parts)
    are merged into a single array, mirroring one real response. Only the
    LLM-facing copy is changed — stored history and panel display are intact.
    """
    out: list[dict] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            out.append({"role": "assistant",
                        "content": json.dumps(buffer, ensure_ascii=False)})
            buffer.clear()

    for m in context_messages:
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            buffer.append(m.get("content") or "")
        else:
            flush()
            out.append(m)
    flush()
    return out
