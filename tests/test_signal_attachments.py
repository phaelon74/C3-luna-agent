"""Tests for Signal attachment classification helpers."""

from __future__ import annotations

from mose.signal_attachments import _decode_attachment_result, _normalize_mime, _suffix_allowed


def test_decode_attachment_result_data_base64():
    raw = b"hello"
    import base64
    b64 = base64.b64encode(raw).decode()
    result = _decode_attachment_result({"dataBase64": b64})
    assert result == raw


def test_suffix_allowed():
    assert _suffix_allowed("queue.json", [".json", ".log"])
    assert not _suffix_allowed("file.pdf", [".json"])


def test_normalize_mime():
    assert _normalize_mime("image/jpeg; charset=binary") == "image/jpeg"
