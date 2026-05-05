"""Tests for mose_portal codegen (JSON Schema → TypeScript)."""

from __future__ import annotations

from mose_portal import codegen


def test_sanitize_server_ts() -> None:
    assert codegen.sanitize_server_ts("plex-ops-admin") == "plex_ops_admin"


def test_input_interface_name() -> None:
    assert codegen.input_interface_name("plex-ops-admin", "sessions_get_active") == (
        "PlexOpsAdminSessionsGetActiveInput"
    )


def test_build_example_object_uses_defaults_and_enum() -> None:
    schema = {
        "type": "object",
        "properties": {
            "page": {"type": "integer", "default": 1},
            "sortKey": {"type": "string", "enum": ["time", "title"]},
        },
        "required": ["sortKey"],
    }
    obj = codegen.build_example_object(schema)
    assert obj["page"] == 1
    assert obj["sortKey"] == "time"


def test_tool_row_to_typescript_includes_example() -> None:
    row = {
        "name": "demo__hello",
        "description": "Say hello",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "default": "world"}},
            "required": [],
        },
        "_server": "demo",
        "_tool_name": "hello",
    }
    iface, sig, ex = codegen.tool_row_to_typescript(row)
    assert "export interface DemoHelloInput" in iface
    assert "mcp.demo.hello" in sig
    assert "await mcp.demo.hello" in ex
    assert "console.log" in ex


def test_generate_mcp_dts_contains_declare_mcp() -> None:
    tools = [
        {
            "name": "s__t1",
            "description": "One",
            "input_schema": {"type": "object", "properties": {}},
            "_server": "s",
            "_tool_name": "t1",
        }
    ]
    dts = codegen.generate_mcp_dts(tools)
    assert "declare const mcp" in dts
    assert "t1:" in dts


def test_generate_mcp_ts_stub_throws_on_call() -> None:
    stub = codegen.generate_mcp_ts_stub([])
    assert "export const mcp" in stub
    assert "throw new Error" in stub


def test_anyof_with_null_yields_union_in_ts_and_non_null_example() -> None:
    """FastMCP emits ``int | None`` as ``anyOf: [{type: integer}, {type: null}]`` — handle it."""
    schema = {
        "type": "object",
        "properties": {
            "page": {
                "anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
                "default": None,
            },
            "limit": {
                "anyOf": [{"type": "integer", "minimum": 5}, {"type": "null"}],
            },
        },
        "required": ["limit"],
    }
    iface = codegen.schema_to_interface("AnyOfInput", schema)
    assert "page?:" in iface
    assert "number" in iface
    assert "null" in iface
    obj = codegen.build_example_object(schema)
    assert obj.get("limit") == 5  # picks non-null branch + uses minimum


def test_oneof_single_type() -> None:
    out = codegen.json_schema_to_ts_type({"oneOf": [{"type": "string"}, {"type": "number"}]}, {})
    assert out == "string | number"
