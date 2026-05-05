"""JSON Schema → TypeScript types, signatures, examples, and bundled ``mcp`` declarations."""

from __future__ import annotations

import json
import re
from typing import Any

# --- Identifiers (TS object keys use snake_case derived from server/tool names) ---


def sanitize_server_ts(server: str) -> str:
    """``plex-ops-admin`` → ``plex_ops_admin`` (valid TS identifier segment)."""
    return server.replace("-", "_")


def _segment_title(s: str) -> str:
    if not s:
        return ""
    return s[0].upper() + s[1:].lower()


def server_pascal(server: str) -> str:
    """``plex-ops-admin`` → ``PlexOpsAdmin``."""
    parts: list[str] = []
    for segment in re.split(r"[-_]+", server):
        if segment:
            parts.append(_segment_title(segment))
    return "".join(parts) or "Server"


def tool_pascal(tool: str) -> str:
    """``sessions_get_active`` → ``SessionsGetActive``."""
    parts: list[str] = []
    for segment in tool.split("_"):
        if segment:
            parts.append(_segment_title(segment))
    return "".join(parts) or "Tool"


def input_interface_name(server: str, tool_bare: str) -> str:
    return f"{server_pascal(server)}{tool_pascal(tool_bare)}Input"


# --- $ref / defs ---


def resolve_schema(schema: Any, root: dict[str, Any]) -> Any:
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        key = ref.rsplit("/", 1)[-1]
        inner = root.get("$defs", {}).get(key)
        if isinstance(inner, dict):
            return inner
    return schema


# --- Example JSON from schema (MoE-3B: every search hit needs a copy-pasteable snippet) ---


def _pick_non_null_branch(branches: list[Any], root: dict[str, Any]) -> Any:
    """For ``anyOf`` / ``oneOf`` choose the first non-null branch."""
    for b in branches:
        rb = resolve_schema(b, root)
        if isinstance(rb, dict) and rb.get("type") == "null":
            continue
        return rb
    return branches[0] if branches else None


def example_value(name: str, schema: Any, root: dict[str, Any], depth: int = 0) -> Any:
    if depth > 12:
        return None
    schema = resolve_schema(schema, root)
    if not isinstance(schema, dict):
        return None

    if "default" in schema:
        return schema["default"]
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]

    # anyOf / oneOf: walk into the first non-null branch (FastMCP emits these for ``int | None``).
    for combinator in ("anyOf", "oneOf"):
        branches = schema.get(combinator)
        if isinstance(branches, list) and branches:
            picked = _pick_non_null_branch(branches, root)
            if picked is not None:
                return example_value(name, picked, root, depth + 1)

    t = schema.get("type")
    if isinstance(t, list):
        # e.g. ["string", "null"] — pick first non-null
        for x in t:
            if x != "null":
                return example_value(name, {**schema, "type": x}, root, depth + 1)
        return None

    if t == "string":
        n = name.lower()
        if "url" in n:
            return "https://example.com"
        if "id" in n and n.endswith("id"):
            return "1"
        return "example"
    if t == "integer":
        base = schema.get("minimum", 0)
        return int(base)
    if t == "number":
        return float(schema.get("minimum", 0.0))
    if t == "boolean":
        return False
    if t == "array":
        item_schema = schema.get("items", {})
        one = example_value(f"{name}_item", item_schema, root, depth + 1)
        return [one] if one is not None else []
    if t == "object":
        props = schema.get("properties") or {}
        required = list(schema.get("required") or [])
        out: dict[str, Any] = {}
        for k in required:
            if k in props:
                v = example_value(k, props[k], root, depth + 1)
                if v is not None:
                    out[k] = v
        # If everything optional, still emit a minimal object when properties exist
        if not out and props:
            first_key = next(iter(props))
            v = example_value(first_key, props[first_key], root, depth + 1)
            if v is not None:
                out[first_key] = v
        return out

    # any / missing type
    if "properties" in schema:
        return example_value(name, {**schema, "type": "object"}, root, depth + 1)
    return None


def build_example_object(input_schema: dict[str, Any]) -> dict[str, Any]:
    """Required fields + optional fields that ship a ``default`` (capped at 8 keys total).

    Including defaulted optionals is useful for the LLM (shows real values it can
    keep or change) and is bounded because most MCP tools have only a handful of
    documented defaults.
    """
    root = input_schema if isinstance(input_schema, dict) else {}
    props = root.get("properties")
    if not isinstance(props, dict):
        return {}
    required_keys = list(root.get("required") or [])
    required_set = set(required_keys)
    out: dict[str, Any] = {}

    for key in required_keys:
        if key not in props:
            continue
        val = example_value(key, props[key], root, 0)
        if val is not None:
            out[key] = val

    for key, sub in props.items():
        if key in required_set:
            continue
        if isinstance(sub, dict) and "default" in sub:
            val = example_value(key, sub, root, 0)
            if val is not None:
                out[key] = val
        if len(out) >= 8:
            break

    if not out:
        for key, sub in props.items():
            val = example_value(key, sub, root, 0)
            if val is not None:
                out[key] = val
                break
    return out


def build_example_snippet(
    server: str,
    tool_bare: str,
    description: str,
    input_schema: dict[str, Any],
) -> str:
    """3–5 line TS snippet suitable for MoE models (required by search)."""
    st = sanitize_server_ts(server)
    obj = build_example_object(input_schema)
    args_json = json.dumps(obj, indent=2)
    # Indent object for TS
    indented = "\n".join("  " + line for line in args_json.splitlines())
    first_line = (description or "").strip().split("\n", 1)[0].strip()
    comment = first_line[:120] if first_line else f"Call {server}__{tool_bare}"
    return (
        f"// {comment}\n"
        f"const _result = await mcp.{st}.{tool_bare}(\n{indented}\n);\n"
        f"console.log(JSON.stringify(_result, null, 2));"
    )


def ts_signature_line(server: str, tool_bare: str, iface_in: str) -> str:
    st = sanitize_server_ts(server)
    return f"mcp.{st}.{tool_bare}(input: {iface_in}): Promise<unknown>"


# --- JSON Schema → TS interface ---


def _quote_key(k: str) -> str:
    if re.match(r"^[A-Za-z_$][\w$]*$", k):
        return k
    return json.dumps(k)


def json_schema_to_ts_type(schema: Any, root: dict[str, Any], depth: int = 0) -> str:
    if depth > 14:
        return "unknown"
    schema = resolve_schema(schema, root)
    if not isinstance(schema, dict):
        return "unknown"

    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        literals = []
        for v in schema["enum"]:
            if isinstance(v, str):
                literals.append(json.dumps(v))
            elif isinstance(v, bool):
                literals.append("true" if v else "false")
            elif v is None:
                literals.append("null")
            else:
                literals.append(json.dumps(v))
        return " | ".join(literals) if literals else "string"

    # anyOf / oneOf → TS union; allOf → first non-trivial branch (best-effort).
    for combinator in ("anyOf", "oneOf"):
        branches = schema.get(combinator)
        if isinstance(branches, list) and branches:
            parts = [json_schema_to_ts_type(b, root, depth + 1) for b in branches]
            uniq = list(dict.fromkeys(parts))
            return " | ".join(uniq) if uniq else "unknown"
    if isinstance(schema.get("allOf"), list) and schema["allOf"]:
        # Pick the first branch that yields a non-trivial type.
        for b in schema["allOf"]:
            rendered = json_schema_to_ts_type(b, root, depth + 1)
            if rendered not in ("unknown", "Record<string, unknown>"):
                return rendered
        return "unknown"

    t = schema.get("type")
    if isinstance(t, list):
        parts = [json_schema_to_ts_type({**schema, "type": x}, root, depth + 1) for x in t]
        return " | ".join(dict.fromkeys(parts))  # dedupe preserve order

    if t == "string":
        return "string"
    if t == "integer":
        return "number"
    if t == "number":
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        items = schema.get("items", {})
        return f"Array<{json_schema_to_ts_type(items, root, depth + 1)}>"

    if t == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        if not props:
            return "Record<string, unknown>"
        lines: list[str] = ["{"]
        for key, sub in props.items():
            opt = "" if key in required else "?"
            sub = resolve_schema(sub, root)
            doc = ""
            if isinstance(sub, dict) and sub.get("description"):
                doc = f"\n  /** {str(sub['description']).strip().replace('*/', '* /')} */\n"
            lines.append(f"{doc}  {_quote_key(key)}{opt}: {json_schema_to_ts_type(sub, root, depth + 1)};")
        lines.append("}")
        return "\n".join(lines)

    return "unknown"


def schema_to_interface(name: str, input_schema: dict[str, Any]) -> str:
    """Emit ``export interface Name { ... }`` from a JSON Schema object."""
    if not isinstance(input_schema, dict):
        input_schema = {}
    if not input_schema:
        return f"export interface {name} {{}}\n"
    props = input_schema.get("properties")
    if input_schema.get("type") == "object" and isinstance(props, dict) and len(props) == 0:
        return f"export interface {name} {{}}\n"
    body_type = json_schema_to_ts_type(input_schema, input_schema, 0)
    # When the body is already an inline object shape (``{ ... }``), keep its
    # indentation by attaching directly. Otherwise wrap in a passthrough field.
    if body_type.startswith("{"):
        return f"export interface {name} {body_type}\n"
    return f"export interface {name} {{\n  /** passthrough */ payload: {body_type};\n}}\n"


def tool_row_to_typescript(tool_row: dict[str, Any]) -> tuple[str, str, str]:
    """Returns (interface_src, ts_signature, example_snippet)."""
    server = str(tool_row["_server"])
    bare = str(tool_row["_tool_name"])
    desc = str(tool_row.get("description") or "")
    schema = tool_row.get("input_schema") or {"type": "object", "properties": {}}
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}

    iface = input_interface_name(server, bare)
    iface_src = schema_to_interface(iface, schema)
    sig = ts_signature_line(server, bare, iface)
    ex = build_example_snippet(server, bare, desc, schema)
    return iface_src, sig, ex


def generate_mcp_dts(tools: list[dict[str, Any]]) -> str:
    """Full ``mcp.d.ts``: all input interfaces + ``declare const mcp`` tree."""
    blocks: list[str] = [
        "// Auto-generated by mose_portal.codegen — upstream MCP tool typings.\n",
    ]
    iface_names: dict[tuple[str, str], str] = {}
    for t in tools:
        server = str(t["_server"])
        bare = str(t["_tool_name"])
        iface = input_interface_name(server, bare)
        iface_names[(server, bare)] = iface

    seen_ifaces: set[str] = set()
    for t in tools:
        server = str(t["_server"])
        bare = str(t["_tool_name"])
        iface = iface_names[(server, bare)]
        if iface in seen_ifaces:
            continue
        seen_ifaces.add(iface)
        schema = t.get("input_schema") or {}
        if not isinstance(schema, dict):
            schema = {}
        blocks.append(schema_to_interface(iface, schema))

    # declare const mcp: { server: { tool: (input) => Promise<unknown> } }
    lines: list[str] = ["declare const mcp: {"]
    by_server: dict[str, list[tuple[str, str]]] = {}
    for t in tools:
        server = str(t["_server"])
        bare = str(t["_tool_name"])
        by_server.setdefault(server, []).append((bare, iface_names[(server, bare)]))

    for server in sorted(by_server.keys()):
        st = sanitize_server_ts(server)
        lines.append(f"  {st}: {{")
        for bare, iface in sorted(by_server[server], key=lambda x: x[0]):
            desc = ""
            for t in tools:
                if t["_server"] == server and t["_tool_name"] == bare:
                    d = (t.get("description") or "").strip()
                    if d:
                        safe = d.replace("*/", "* /")[:400]
                        desc = f"\n    /** {safe} */\n"
                    break
            lines.append(
                f"{desc}    {bare}: (input: {iface}) => Promise<unknown>;"
            )
        lines.append("  };")
    lines.append("};")
    blocks.append("\n".join(lines))
    return "\n".join(blocks)


def generate_mcp_ts_stub(tools: list[dict[str, Any]]) -> str:
    """Runtime stub (Phase 2+ sandbox replaces with RPC proxy). Every call throws."""
    lines = [
        "// Auto-generated stub — real implementation is injected by the Code Mode sandbox.\n",
        "export const mcp = new Proxy({} as any, {",
        "  get() {",
        "    return new Proxy(() => {}, {",
        "      apply() {",
        "        throw new Error('mcp proxy not wired — use portal_codemode_execute');",
        "      },",
        "      get() { return () => { throw new Error('mcp proxy not wired'); }; }",
        "    });",
        "  }",
        "});",
    ]
    return "\n".join(lines) + "\n"
