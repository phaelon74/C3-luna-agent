"""Tests for multimodal agent input handling."""

from __future__ import annotations

from mose.incoming_content import IncomingContent, ImagePart, incoming_content_to_surrogate
from mose.llm_vision import build_user_message_content


def test_surrogate_excludes_base64():
    inc = IncomingContent(
        text="check this",
        images=[ImagePart(mime_type="image/png", data_base64="SECRETBASE64")],
        file_blocks=["[Attached file: a.log (text/plain, 10 chars)]\n\nline1"],
    )
    s = incoming_content_to_surrogate(inc)
    assert "SECRETBASE64" not in s
    assert "check this" in s
    assert "1 image" in s
    assert "a.log" in s


def test_build_user_message_includes_file_block():
    inc = IncomingContent(text="hi", file_blocks=["[Attached file: x.json]\n\n{}"])
    content = build_user_message_content(inc)
    assert isinstance(content, str)
    assert "x.json" in content
