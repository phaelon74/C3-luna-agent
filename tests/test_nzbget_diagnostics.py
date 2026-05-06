"""Unit tests for ``docker/nzbget-diagnostics`` (path-prep like test_arr_diagnostics)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
NZB_PKG = ROOT / "docker" / "nzbget-diagnostics"


@pytest.fixture(scope="module", autouse=True)
def _prepend_nzb_path() -> None:
    p = str(NZB_PKG)
    if p not in sys.path:
        sys.path.insert(0, p)


def test_nzbget_client_jsonrpc_payload() -> None:
    from nzbget_diagnostics.client import NzbgetClient

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"jsonrpc": "2.0", "result": "21.0", "id": 1}

    with patch("nzbget_diagnostics.client.httpx.Client") as MockClient:
        inst = MockClient.return_value
        inst.post.return_value = mock_response
        c = NzbgetClient("10.0.0.5", 6789, "nzbget", "secret")
        try:
            assert c.call("version") == "21.0"
            inst.post.assert_called_once()
            call_kw = inst.post.call_args.kwargs
            body = json.loads(call_kw["content"])
            assert body["method"] == "version"
            assert body["params"] == []
        finally:
            c.close()


def test_nzbget_client_rpc_error_raises() -> None:
    from nzbget_diagnostics.client import NzbgetClient

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"jsonrpc": "2.0", "error": {"code": 1, "message": "bad"}, "id": 1}

    with patch("nzbget_diagnostics.client.httpx.Client") as MockClient:
        inst = MockClient.return_value
        inst.post.return_value = mock_response
        c = NzbgetClient("h", 6789, "u", "p")
        try:
            with pytest.raises(RuntimeError, match="nzbget status error"):
                c.call("status")
        finally:
            c.close()


def test_redact_config_masks_password() -> None:
    from nzbget_diagnostics.util import redact_config

    cfg = [
        {"Name": "Server1.Password", "Value": "secret"},
        {"Name": "ControlUsername", "Value": "nzbget"},
    ]
    out = redact_config(cfg)
    assert out[0]["Value"] == "***"
    assert out[1]["Value"] == "nzbget"


def test_editqueue_helper_group_delete() -> None:
    from nzbget_diagnostics.mcp_server import _editqueue

    class _C:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple]] = []

        def call(self, method: str, *params: object) -> object:
            self.calls.append((method, params))
            return True

        def close(self) -> None:
            pass

    c = _C()
    out = json.loads(_editqueue(c, "GroupDelete", "", [7]))  # type: ignore[arg-type]
    assert out is True
    assert c.calls == [("editqueue", ("GroupDelete", "", [7]))]


def test_editqueue_requires_ids() -> None:
    from nzbget_diagnostics.mcp_server import _editqueue

    class _C:
        def call(self, *_a: object) -> object:
            raise AssertionError("should not call")

        def close(self) -> None:
            pass

    out = json.loads(_editqueue(_C(), "GroupDelete", "", []))  # type: ignore[arg-type]
    assert out["error"] == "ids_required"


def test_listfiles_rpc_argument_shape() -> None:
    """Contract: listfiles(0, 0, NZBID) per NZBGet API."""
    recorded: list[tuple[str, tuple]] = []
    nz = 42

    class _C:
        def call(self, method: str, *params: object) -> object:
            recorded.append((method, params))
            return []

        def close(self) -> None:
            pass

    c = _C()
    from nzbget_diagnostics.util import json_response

    json_response(c.call("listfiles", 0, 0, nz))
    assert recorded == [("listfiles", (0, 0, 42))]


def test_editqueue_reorder_param_shape() -> None:
    from nzbget_diagnostics.mcp_server import _editqueue

    class _C:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple]] = []

        def call(self, method: str, *params: object) -> object:
            self.calls.append((method, params))
            return True

        def close(self) -> None:
            pass

    c = _C()
    _editqueue(c, "GroupMoveTop", "", [1])  # type: ignore[arg-type]
    _editqueue(c, "GroupMoveBottom", "", [2])  # type: ignore[arg-type]
    _editqueue(c, "GroupMoveOffset", "-2", [3])  # type: ignore[arg-type]
    assert c.calls == [
        ("editqueue", ("GroupMoveTop", "", [1])),
        ("editqueue", ("GroupMoveBottom", "", [2])),
        ("editqueue", ("GroupMoveOffset", "-2", [3])),
    ]


def test_build_nzbget_app_has_run() -> None:
    from nzbget_diagnostics.mcp_server import build_nzbget_app

    class _C:
        def call(self, *_a: object) -> object:
            return None

        def close(self) -> None:
            pass

    app = build_nzbget_app(_C())  # type: ignore[arg-type]
    assert callable(getattr(app, "run", None))


def test_safe_tool_http_error_json() -> None:
    from nzbget_diagnostics.util import safe_tool

    @safe_tool
    def boom() -> str:
        req = MagicMock()
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        raise httpx.HTTPStatusError("msg", request=req, response=resp)

    out = json.loads(boom())
    assert out["error"] == "http_error"
    assert out["http_status"] == 401


def test_nzbget_read_tool_count_matches_policy() -> None:
    from mose import mcp_write_policy as mp

    assert len(mp._NZBGET_DIAG_READS) == 9  # noqa: SLF001
