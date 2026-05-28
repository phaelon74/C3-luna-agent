"""Download and classify Signal message attachments."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mose.config import Config, SignalConfig
from mose.context_compress import compress_text_if_needed, default_tool_result_token_budget
from mose.incoming_content import ImagePart, IncomingContent
from mose.observe import get_logger, log_event

if TYPE_CHECKING:
    from mose.signal_bot import MoseSignalBot

logger = get_logger("signal_attachments")

SIGNAL_ATTACHMENTS_DIR = "data/signal_attachments"


def _normalize_mime(mime: str) -> str:
    return (mime or "").split(";")[0].strip().lower()


def _suffix_allowed(filename: str, allowed: list[str]) -> bool:
    suf = Path(filename or "").suffix.lower()
    return suf in {s.lower() if s.startswith(".") else f".{s.lower()}" for s in allowed}


def _persist_attachment_bytes(data: bytes, source: str, session_key: str, root: Path) -> Path:
    out_dir = root / SIGNAL_ATTACHMENTS_DIR / session_key
    out_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(data).hexdigest()[:12]
    safe = re.sub(r"[^\w\-.]", "_", source)[:60]
    path = out_dir / f"{safe}_{h}"
    path.write_bytes(data)
    log_event(logger, "signal_attachment_persisted", path=str(path), size=len(data))
    return path


def _decode_attachment_result(result: dict[str, Any]) -> bytes:
    for key in ("dataBase64", "base64", "data"):
        val = result.get(key)
        if isinstance(val, str) and val:
            return base64.b64decode(val)
    if isinstance(result.get("attachment"), str):
        return base64.b64decode(result["attachment"])
    raise ValueError("getAttachment result missing base64 payload")


async def resolve_signal_attachments(
    bot: MoseSignalBot,
    *,
    envelope: dict[str, Any],
    source: str,
    group_id: str,
    caption: str,
    llm: Any,
    config: Config,
    session_key: str,
    status_callback: Any | None = None,
) -> IncomingContent:
    """Build IncomingContent from envelope caption and attachment metadata."""
    signal_cfg = config.signal
    data_msg = envelope.get("dataMessage") or {}
    attachments = data_msg.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []

    incoming = IncomingContent(text=caption or "")
    root = config.root_dir
    image_count = 0

    for meta in attachments:
        if not isinstance(meta, dict):
            continue
        att_id = str(meta.get("id") or meta.get("attachmentId") or "").strip()
        if not att_id:
            incoming.skipped_notes.append("Skipped attachment with no id.")
            continue

        filename = str(meta.get("fileName") or meta.get("filename") or f"attachment_{att_id}")
        content_type = _normalize_mime(str(meta.get("contentType") or meta.get("mimetype") or ""))

        if status_callback is not None:
            try:
                ret = status_callback("signal_attachment", filename)
                if hasattr(ret, "__await__"):
                    await ret
            except Exception:
                pass

        try:
            result = await bot.get_attachment(att_id, group_id=group_id, source=source)
            raw = _decode_attachment_result(result)
        except Exception as e:
            logger.exception("signal_get_attachment_failed", extra={"id": att_id})
            incoming.skipped_notes.append(f"Failed to download {filename}: {e}")
            continue

        if len(raw) > signal_cfg.max_attachment_bytes:
            incoming.skipped_notes.append(
                f"Skipped {filename}: exceeds max size ({len(raw)} > {signal_cfg.max_attachment_bytes} bytes)."
            )
            continue

        _persist_attachment_bytes(raw, filename, session_key, root)

        is_image = content_type in {_normalize_mime(m) for m in signal_cfg.allowed_image_mime_types}
        is_text = _suffix_allowed(filename, signal_cfg.allowed_text_suffixes)

        if is_image:
            if image_count >= signal_cfg.max_images_per_message:
                incoming.skipped_notes.append(f"Skipped image {filename}: max images per message reached.")
                continue
            if not content_type.startswith("image/"):
                content_type = "image/jpeg"
            incoming.images.append(
                ImagePart(
                    mime_type=content_type,
                    data_base64=base64.b64encode(raw).decode("ascii"),
                    filename=filename,
                ),
            )
            image_count += 1
            continue

        if is_text:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
            query = caption.strip() or f"attached file {filename}"
            if status_callback is not None:
                try:
                    ret = status_callback("signal_compress", filename)
                    if hasattr(ret, "__await__"):
                        await ret
                except Exception:
                    pass
            processed = await compress_text_if_needed(
                text,
                llm=llm,
                query_context=query,
                max_output_tokens=default_tool_result_token_budget(),
                source=f"signal_{filename}",
                root=root,
            )
            incoming.file_blocks.append(
                f"[Attached file: {filename} ({content_type or 'text/plain'}, {len(text)} chars)]\n\n"
                f"{processed}"
            )
            continue

        incoming.skipped_notes.append(
            f"Skipped {filename}: unsupported type (use images or {', '.join(signal_cfg.allowed_text_suffixes)})."
        )

    return incoming
