"""Structured user input from channels (Signal attachments, etc.)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImagePart:
    """Raw image bytes as base64 (no data: URL prefix)."""

    mime_type: str
    data_base64: str
    filename: str | None = None


@dataclass
class IncomingContent:
    """User turn that may include text, images, and inlined file excerpts."""

    text: str = ""
    images: list[ImagePart] = field(default_factory=list)
    file_blocks: list[str] = field(default_factory=list)
    skipped_notes: list[str] = field(default_factory=list)

    def has_attachments(self) -> bool:
        return bool(self.images or self.file_blocks)

    def is_empty(self) -> bool:
        return not (self.text.strip() or self.images or self.file_blocks)


def incoming_content_to_surrogate(content: IncomingContent) -> str:
    """Text-only representation for memory, search, and logging (no base64)."""
    parts: list[str] = []
    if content.text.strip():
        parts.append(content.text.strip())
    if content.images:
        n = len(content.images)
        parts.append(f"[{n} image(s) attached]")
    for block in content.file_blocks:
        first_line = block.split("\n", 1)[0].strip()
        if first_line.startswith("[Attached file:"):
            parts.append(first_line)
        else:
            parts.append("[attached file]")
    for note in content.skipped_notes:
        parts.append(note)
    return "\n\n".join(parts) if parts else "(empty message)"
