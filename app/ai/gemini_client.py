from __future__ import annotations

import asyncio
import base64
import json
import random
import re
from typing import Any

import httpx

from app.config import settings
from app.core.exceptions import GeminiError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MODEL = "gemini-2.5-flash"
_REST_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{_MODEL}:generateContent"
_TIMEOUT = 60.0  # Bigger payloads (10-slide decks with charts/roadmaps) need more headroom.
_MAX_OUTPUT_TOKENS = 8192  # Was 4096 — tight for 10+ slide decks with chart/roadmap arrays.
_DEFAULT_RETRIES = 4  # Was 2 — 503s from Google are transient bursts; back off and try again.
_RETRYABLE_STATUSES = {500, 502, 503, 504, 408, 429}

_IMAGE_MODEL = "gemini-2.5-flash-image"
_IMAGE_REST_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{_IMAGE_MODEL}:generateContent"
_IMAGE_TIMEOUT = 60.0


def init_gemini() -> None:
    logger.info("Gemini client ready (REST)")


def _parse_quota_response(body: str) -> tuple[str, str | None, float | None]:
    """Parse a Gemini 429 response.

    Returns (friendly_message, quota_id, retry_after_seconds).
    retry_after_seconds is set when Gemini suggests retrying (per-minute caps);
    None for daily/permanent quotas.
    """
    try:
        data = json.loads(body)
    except Exception:
        return f"Quota exceeded: {body[:300]}", None, None
    err = (data.get("error") or {})
    details = err.get("details") or []
    quota_id: str | None = None
    retry_after: float | None = None
    for d in details:
        if d.get("@type", "").endswith("QuotaFailure"):
            for v in d.get("violations", []):
                quota_id = v.get("quotaId") or v.get("quotaMetric") or quota_id
                break
        if d.get("@type", "").endswith("RetryInfo"):
            delay_str = d.get("retryDelay") or ""
            # Gemini returns "47s" or "1.5s" — parse the leading number.
            if delay_str.endswith("s"):
                try:
                    retry_after = float(delay_str[:-1])
                except ValueError:
                    pass
    plain = err.get("message", "Quota exceeded")
    parts = ["Quota exceeded:"]
    if quota_id:
        parts.append(f"limit `{quota_id}`")
    if retry_after is not None:
        parts.append(f"(retry after {retry_after:.0f}s)")
    parts.append("— " + plain.split(". For more information")[0])
    return " ".join(parts), quota_id, retry_after


def _is_per_minute_quota(quota_id: str | None) -> bool:
    """True when the quota will reset within a minute — worth retrying."""
    if not quota_id:
        return False
    qid = quota_id.lower()
    return "perminute" in qid or "per_minute" in qid


def _friendly_quota_message(body: str) -> str:
    msg, _, _ = _parse_quota_response(body)
    return msg


async def _backoff_sleep(attempt: int) -> None:
    """Exponential backoff with jitter. attempt is 0-indexed."""
    base = min(2.0 ** attempt, 16.0)
    await asyncio.sleep(base + random.uniform(0, 0.5))


def _is_retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUSES
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
        return True
    return False


# Last response's token usage. Captured by generate_json* on success so the
# streaming endpoint can forward it without changing the public return type.
last_token_usage: dict[str, int] | None = None


def get_last_token_count() -> int:
    """Back-compat shim — returns just the total token count from the last
    call. Prefer reading `last_token_usage` directly for full breakdown."""
    return (last_token_usage or {}).get("total", 0)


async def generate_json(prompt: str, retries: int = _DEFAULT_RETRIES) -> Any:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": _MAX_OUTPUT_TOKENS,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    params = {"key": settings.GEMINI_API_KEY}

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(retries):
            try:
                resp = await client.post(_REST_URL, params=params, json=payload)
                # 429: parse the specific quota. Per-minute quotas reset fast
                # so we wait the suggested retryDelay and try again. Daily caps
                # have no retryDelay → fail immediately.
                if resp.status_code == 429:
                    body = resp.text
                    logger.error(f"Gemini 429 on attempt {attempt + 1}: {body}")
                    msg, quota_id, retry_s = _parse_quota_response(body)
                    if _is_per_minute_quota(quota_id) and attempt < retries - 1:
                        wait_s = max(retry_s or 0, 30.0)
                        wait_s = min(wait_s, 60.0)  # cap so a stuck loop can't hang too long
                        logger.warning(f"Per-minute quota — waiting {wait_s:.0f}s before retry…")
                        await asyncio.sleep(wait_s)
                        continue
                    raise GeminiError(msg)
                # 5xx / 408: transient — back off and retry.
                if resp.status_code in _RETRYABLE_STATUSES:
                    last_exc = Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    logger.warning(
                        f"Gemini transient {resp.status_code} attempt {attempt + 1}/{retries}; backing off…"
                    )
                    if attempt < retries - 1:
                        await _backoff_sleep(attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if raw.startswith("```"):
                    lines = raw.split("\n")
                    raw = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
                global last_token_usage
                usage = data.get("usageMetadata") or {}
                last_token_usage = {
                    "prompt": int(usage.get("promptTokenCount", 0) or 0),
                    "completion": int(usage.get("candidatesTokenCount", 0) or 0),
                    "total": int(usage.get("totalTokenCount", 0) or 0),
                }
                return json.loads(raw)
            except GeminiError:
                raise
            except json.JSONDecodeError as exc:
                last_exc = exc
                logger.info(f"JSON parse failed on attempt {attempt + 1}, retrying…")
                if attempt < retries - 1:
                    await _backoff_sleep(attempt)
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Gemini error attempt {attempt + 1}/{retries}: {exc}")
                if not _is_retryable_http_error(exc):
                    # Unknown error — still retry once or twice with backoff.
                    pass
                if attempt < retries - 1:
                    await _backoff_sleep(attempt)

    raise GeminiError(f"Gemini failed after {retries} attempts: {last_exc}")


async def generate_json_multimodal(
    prompt: str,
    images: list[tuple[bytes, str]],
    retries: int = _DEFAULT_RETRIES,
) -> Any:
    """Like generate_json, but with one or more inline images attached.

    images: list of (raw_bytes, mime_type) — e.g. [(b"...", "image/png")].
    """
    parts: list[dict] = [{"text": prompt}]
    for img_bytes, mime in images:
        parts.append({
            "inline_data": {
                "mime_type": mime or "image/png",
                "data": base64.b64encode(img_bytes).decode(),
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": _MAX_OUTPUT_TOKENS,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    params = {"key": settings.GEMINI_API_KEY}

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT * 2) as client:
        for attempt in range(retries):
            try:
                resp = await client.post(_REST_URL, params=params, json=payload)
                if resp.status_code == 429:
                    logger.error(f"Gemini 429 (multimodal): {resp.text}")
                    raise GeminiError(_friendly_quota_message(resp.text))
                if resp.status_code in _RETRYABLE_STATUSES:
                    last_exc = Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    logger.warning(
                        f"Gemini multimodal transient {resp.status_code} "
                        f"attempt {attempt + 1}/{retries}; backing off…"
                    )
                    if attempt < retries - 1:
                        await _backoff_sleep(attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if raw.startswith("```"):
                    lines = raw.split("\n")
                    raw = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
                global last_token_usage
                usage = data.get("usageMetadata") or {}
                last_token_usage = {
                    "prompt": int(usage.get("promptTokenCount", 0) or 0),
                    "completion": int(usage.get("candidatesTokenCount", 0) or 0),
                    "total": int(usage.get("totalTokenCount", 0) or 0),
                }
                return json.loads(raw)
            except GeminiError:
                raise
            except json.JSONDecodeError as exc:
                last_exc = exc
                logger.info(f"Multimodal JSON parse failed on attempt {attempt + 1}, retrying…")
                if attempt < retries - 1:
                    await _backoff_sleep(attempt)
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Gemini multimodal error attempt {attempt + 1}/{retries}: {exc}")
                if attempt < retries - 1:
                    await _backoff_sleep(attempt)

    raise GeminiError(f"Gemini multimodal failed after {retries} attempts: {last_exc}")


async def generate_image(prompt: str) -> tuple[bytes, str]:
    """Generate a single image from a text prompt.

    Returns (raw_bytes, mime_type). Uses gemini-2.5-flash-image (Nano Banana).
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
    }
    params = {"key": settings.GEMINI_API_KEY}

    async with httpx.AsyncClient(timeout=_IMAGE_TIMEOUT) as client:
        resp = await client.post(_IMAGE_REST_URL, params=params, json=payload)
        if resp.status_code == 429:
            raise GeminiError(f"Quota exceeded: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise GeminiError(f"Image generation failed: {resp.status_code} {resp.text[:200]}")

        data = resp.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as exc:
            raise GeminiError(f"Unexpected image response shape: {exc}") from exc

        for part in parts:
            inline = part.get("inline_data") or part.get("inlineData")
            if inline and inline.get("data"):
                mime = inline.get("mime_type") or inline.get("mimeType") or "image/png"
                return base64.b64decode(inline["data"]), mime

        raise GeminiError("No image data in response")
