// Code Mode sandbox runner: stdin (user TS) -> WebSocket RPC -> portal aggregator.

type RpcCall = {
  type: "call";
  id: string;
  server_ts: string;
  tool: string;
  arguments: Record<string, unknown>;
};

type RpcDone = {
  type: "done";
  stdout: string;
  stderr: string;
  return_value: unknown;
  errors: Array<Record<string, unknown>>;
};

type Err = { kind: string; message: string; failing_call?: Record<string, unknown> };

function wsUrl(host: string, port: string): string {
  const h = host.trim() || "mose-mcp-portal";
  const p = port.trim() || "9001";
  return `ws://${h}:${p}/`;
}

async function readStdinText(): Promise<string> {
  const chunks: Uint8Array[] = [];
  const reader = Deno.stdin.readable.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.length;
  }
  return new TextDecoder().decode(out);
}

function buildMcp(
  send: (serverTs: string, tool: string, args: Record<string, unknown>) => Promise<unknown>,
): Record<string, unknown> {
  return new Proxy(
    {},
    {
      get(_t, sk) {
        const serverKey = String(sk);
        return new Proxy(
          {},
          {
            get(_t2, tk) {
              const toolKey = String(tk);
              return (input: unknown) =>
                send(serverKey, toolKey, input && typeof input === "object"
                  ? input as Record<string, unknown>
                  : {});
            },
          },
        );
      },
    },
  );
}

async function main(): Promise<void> {
  const token = Deno.env.get("PORTAL_SESSION_TOKEN") ?? "";
  const host = Deno.env.get("PORTAL_RPC_HOST") ?? "mose-mcp-portal";
  const port = Deno.env.get("PORTAL_RPC_PORT") ?? "9001";

  const stdoutBuf: string[] = [];
  const stderrBuf: string[] = [];
  const errors: Err[] = [];

  const origLog = console.log.bind(console);
  const origErr = console.error.bind(console);
  const stringifySafe = (a: unknown): string => {
    if (typeof a === "string") return a;
    try {
      return JSON.stringify(a);
    } catch (_e) {
      return String(a);
    }
  };
  console.log = (...args: unknown[]) => {
    stdoutBuf.push(args.map(stringifySafe).join(" ") + "\n");
  };
  console.error = (...args: unknown[]) => {
    stderrBuf.push(args.map(stringifySafe).join(" ") + "\n");
  };

  const userCode = await readStdinText();

  const ws = new WebSocket(wsUrl(host, port));
  await new Promise<void>((resolve, reject) => {
    ws.onopen = () => resolve();
    ws.onerror = () => reject(new Error("WebSocket connect failed"));
  });

  const pending = new Map<string, (m: Record<string, unknown>) => void>();

  await new Promise<void>((resolve, reject) => {
    ws.onmessage = (ev: MessageEvent) => {
      const m = JSON.parse(String(ev.data)) as Record<string, unknown>;
      if (m.type === "ready") {
        resolve();
      } else if (m.type === "error") {
        reject(new Error(String(m.message ?? "hello failed")));
      } else {
        reject(new Error(`unexpected message before ready: ${String(m.type)}`));
      }
    };
    ws.send(JSON.stringify({ type: "hello", token }));
  });

  ws.onmessage = (ev: MessageEvent) => {
    const m = JSON.parse(String(ev.data)) as Record<string, unknown>;
    if (m.type === "call_result") {
      const id = String(m.id ?? "");
      const fn = pending.get(id);
      if (fn) fn(m);
    }
  };

  // Upstream MCP servers return `content[].text` which is almost always a
  // JSON-encoded string. The portal aggregator forwards the raw string here.
  // Auto-parse so `await mcp.x.y(args)` resolves to the object the LLM (and
  // mcp.d.ts) expect — otherwise property access silently yields undefined.
  function maybeParseJson(value: unknown): unknown {
    if (typeof value !== "string") return value;
    const trimmed = value.trim();
    if (!trimmed) return value;
    const first = trimmed[0];
    if (first !== "{" && first !== "[" && first !== '"' && first !== "-" && (first < "0" || first > "9") &&
        trimmed !== "true" && trimmed !== "false" && trimmed !== "null") {
      return value;
    }
    try {
      return JSON.parse(trimmed);
    } catch (_e) {
      return value;
    }
  }

  async function rpcCall(
    serverTs: string,
    tool: string,
    args: Record<string, unknown>,
  ): Promise<unknown> {
    const id = crypto.randomUUID();
    const msg: RpcCall = { type: "call", id, server_ts: serverTs, tool, arguments: args };
    return await new Promise((resolve, reject) => {
      pending.set(id, (m) => {
        pending.delete(id);
        if (m.ok === true) resolve(maybeParseJson(m.result));
        else reject(new Error(String(m.error ?? "MCP call failed")));
      });
      ws.send(JSON.stringify(msg));
    });
  }

  const mcp = buildMcp(rpcCall);

  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor as new (
    ...args: string[]
  ) => (...fnArgs: unknown[]) => Promise<unknown>;

  try {
    const fn = new AsyncFunction("mcp", `return await (async () => {\n${userCode}\n})();`);
    await fn(mcp);
  } catch (e) {
    errors.push({
      kind: "runtime",
      message: String(e),
    });
  }

  const done: RpcDone = {
    type: "done",
    stdout: stdoutBuf.join(""),
    stderr: stderrBuf.join(""),
    return_value: null,
    errors,
  };
  await new Promise<void>((resolve) => {
    ws.onclose = () => resolve();
    try {
      ws.send(JSON.stringify(done));
    } catch (_e) {
      resolve();
      return;
    }
    ws.close(1000, "done");
    setTimeout(resolve, 1500);
  });
  console.log = origLog;
  console.error = origErr;
}

await main();
