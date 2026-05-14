from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from lxml import html as lxml_html
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.ai.prompt_templates import COMBINED_GENERATION_PROMPT, level_instructions, render
from app.api.dependencies import get_current_user
from app.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.extractors.extractor_factory import extract_content
from app.models.template import Template
from app.models.theme import Theme
from app.models.user import User
from app.schemas.template import PreviewResponse
from app.utils.logger import get_logger
from app.utils.validators import extract_slide_count_from_prompt

router = APIRouter(prefix="/generate", tags=["generation"])
logger = get_logger(__name__)


_ALLOWED_URL_SCHEMES = {"http", "https"}
_ALLOWED_URL_PORTS = {80, 443, None}  # None = scheme default


def _resolve_and_validate_host(url: str) -> None:
    """Resolve hostname → IP and reject if it points at a non-public address.

    Guards against SSRF — without this, a user could submit
    http://169.254.169.254/ (AWS metadata) or http://localhost:6379/ and
    the resulting body would land in the AI-generated slides.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise HTTPException(status_code=400, detail="URL must use http or https")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL is missing a hostname")
    if parsed.port not in _ALLOWED_URL_PORTS:
        raise HTTPException(
            status_code=400, detail="URL port not allowed (only 80/443)"
        )

    if settings.URL_FETCH_ALLOW_PRIVATE:
        return

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not resolve URL host: {exc}"
        ) from exc

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise HTTPException(
                status_code=400,
                detail="URL host resolves to a non-public address",
            )


async def _fetch_with_size_cap(url: str) -> tuple[str, str]:
    """Fetch a URL with manual redirects (each redirect re-validated for SSRF)
    and a streaming byte cap. Returns (final_url, body_text)."""
    max_bytes = settings.MAX_URL_BYTES_MB * 1024 * 1024
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; WACDeckStudio/1.0)",
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
    }

    current_url = url
    async with httpx.AsyncClient(
        timeout=settings.URL_FETCH_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers=headers,
    ) as client:
        for hop in range(settings.URL_FETCH_MAX_REDIRECTS + 1):
            _resolve_and_validate_host(current_url)
            try:
                async with client.stream("GET", current_url) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            raise HTTPException(
                                status_code=400,
                                detail="Redirect without Location header",
                            )
                        current_url = urljoin(current_url, loc)
                        continue

                    if resp.status_code >= 400:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Could not fetch URL: HTTP {resp.status_code}",
                        )

                    ctype = resp.headers.get("content-type", "")
                    if "html" not in ctype and "text" not in ctype:
                        raise HTTPException(
                            status_code=400,
                            detail=f"URL returned unsupported content-type '{ctype}'.",
                        )

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"URL response exceeds "
                                    f"{settings.MAX_URL_BYTES_MB} MB cap"
                                ),
                            )
                        chunks.append(chunk)

                    encoding = resp.charset_encoding or "utf-8"
                    try:
                        body = b"".join(chunks).decode(encoding, errors="replace")
                    except LookupError:
                        body = b"".join(chunks).decode("utf-8", errors="replace")
                    return current_url, body

            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=400, detail=f"Could not fetch URL: {exc}"
                ) from exc

        raise HTTPException(status_code=400, detail="Too many redirects")


async def _extract_file_text(file: UploadFile) -> str:
    """Save upload to temp file and extract text. Only PDF and DOCX are accepted."""
    suffix = Path(file.filename or "upload.txt").suffix.lower() or ".txt"
    if suffix not in {".pdf", ".docx"}:
        raise HTTPException(status_code=400, detail="Only PDF and DOCX files are supported")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        return extract_content(Path(tmp_path))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def _extract_url_text(url: str) -> tuple[str, str]:
    """Fetch a URL and return (title, readable_text). Raises HTTPException on failure.

    SSRF-hardened via `_resolve_and_validate_host` re-applied at every redirect
    hop, with a streamed byte cap to bound memory.
    """
    _final_url, body = await _fetch_with_size_cap(url)

    try:
        doc = lxml_html.fromstring(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse HTML: {exc}") from exc

    for tag in doc.xpath(
        "//script | //style | //noscript | //nav | //footer | //header"
        " | //aside | //form | //iframe | //svg"
    ):
        parent = tag.getparent()
        if parent is not None:
            parent.remove(tag)

    title_el = doc.find(".//title")
    title = (title_el.text_content().strip() if title_el is not None else "") or url

    candidates = doc.xpath("//article") or doc.xpath("//main") or doc.xpath("//body")
    root = candidates[0] if candidates else doc
    text = root.text_content()
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = text.strip()

    if not text:
        raise HTTPException(status_code=400, detail="No readable text found at URL.")
    return title, text


@router.post("/sync", response_model=PreviewResponse)
@limiter.limit("20/hour")
async def generate_sync(
    request: Request,
    prompt: str = Form(""),
    slide_count: int = Form(10),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    images: list[UploadFile] = File(default=[]),
    level: str = Form("simple"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreviewResponse:
    """
    Synchronous generation: returns {slides, theme} in one request.
    Single Gemini API call — analysis + slide content combined.

    Source content priority (concatenated, when present):
      prompt + uploaded file text + URL-fetched text + uploaded images (multimodal)
    At least one of {prompt, file, url, images} must be supplied.
    """
    from app.agents.generation.preview_generator_agent import _build_outline
    from app.agents.generation.slide_generator_agent import SlideGeneratorAgent
    from app.agents.generation.template_mapper_agent import TemplateMappingResult

    prompt_slide_count = extract_slide_count_from_prompt(prompt)
    if prompt_slide_count is not None:
        slide_count = prompt_slide_count
    slide_count = max(5, min(20, slide_count))

    # Build the source content from prompt + optional file + optional url
    parts: list[str] = []
    prompt = (prompt or "").strip()
    if prompt:
        parts.append(prompt)

    if file and file.filename:
        try:
            file_text = await _extract_file_text(file)
            if len(file_text) > 50_000:
                file_text = file_text[:50_000] + "\n...[content truncated]"
            parts.append(file_text)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(f"File extraction failed, ignoring file: {exc}")

    url_value = (url or "").strip()
    if url_value:
        title, page_text = await _extract_url_text(url_value)
        if len(page_text) > 50_000:
            page_text = page_text[:50_000] + "\n...[content truncated]"
        parts.append(f"Source: {url_value}\nTitle: {title}\n\n{page_text}")

    # Collect images for multimodal input — capped to keep request size sane.
    image_payloads: list[tuple[bytes, str]] = []
    MAX_IMAGES = 4
    MAX_IMAGE_BYTES = 5 * 1024 * 1024
    for img in (images or [])[:MAX_IMAGES]:
        if not img or not img.filename:
            continue
        mime = (img.content_type or "image/png").lower()
        if not mime.startswith("image/"):
            continue
        data = await img.read()
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"Image '{img.filename}' exceeds 5 MB limit.",
            )
        if data:
            image_payloads.append((data, mime))

    if not parts and not image_payloads:
        raise HTTPException(
            status_code=400,
            detail="Provide a prompt, a document, a URL, or at least one image.",
        )

    content = "\n\n".join(parts) if parts else "Use the attached image(s) as the primary source."
    if not prompt:
        prompt = "Create a presentation from the provided source material."

    combined_prompt = render(
        COMBINED_GENERATION_PROMPT,
        prompt=prompt,
        content=content,
        slide_count=slide_count,
        level=normalized_level,
        level_instructions=level_instructions(normalized_level),
    )
    if image_payloads:
        combined_prompt += (
            "\n\nThe attached images are part of the source material. "
            "Read any visible text, charts, or data from them and incorporate "
            "those facts into the slide content where relevant."
        )

    try:
        if image_payloads:
            result = await gemini_client.generate_json_multimodal(
                combined_prompt, image_payloads
            )
        else:
            result = await gemini_client.generate_json(combined_prompt)
    except Exception as exc:
        logger.error(f"Generation failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Content analysis failed: {exc}")

    # Extract analysis fields for outline building
    analysis = {
        "title": result.get("title", prompt[:60]),
        "summary": result.get("summary", prompt),
        "audience": result.get("audience", "General audience"),
        "tone": result.get("tone", "professional"),
        "estimated_slides": slide_count,
        "sections": result.get("sections", []),
    }

    # Build outline deterministically from sections
    outline = _build_outline(analysis, target_slide_count=slide_count)

    # Use slide contents from the combined response
    contents: list[dict] = result.get("slides", [])

    # Pad/trim to match outline length
    empty = {"heading": "", "body": "", "bullets": [], "stats": [], "quote": "", "caption": ""}
    while len(contents) < len(outline):
        contents.append(dict(empty))
    contents = contents[: len(outline)]

    # 4. Load default theme (first theme in DB)
    theme = (await db.execute(select(Theme))).scalars().first()
    if not theme:
        raise HTTPException(status_code=500, detail="No theme found in database")

    # 5. Load any template (needed for TemplateMappingResult)
    template = (await db.execute(select(Template))).scalars().first()
    if not template:
        raise HTTPException(status_code=500, detail="No templates found in database")

    # 6. Render slides locally — pure Python layout engine
    mapping = TemplateMappingResult(template=template, theme=theme)
    agent = SlideGeneratorAgent()
    slides = agent._build_slides(outline, contents, mapping, logo_url="")

    theme_dict = {
        "id": str(theme.id),
        "name": theme.name,
        "colors": theme.colors,
        "fonts": theme.fonts,
    }

    logger.info(f"Sync generation complete: {len(slides)} slides for user {current_user.id}")
    return PreviewResponse(slides=slides, theme=theme_dict)
