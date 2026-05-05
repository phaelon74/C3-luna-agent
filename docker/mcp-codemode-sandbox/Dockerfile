# Isolated Deno runtime for Code Mode: portal runs `docker exec` per call.
FROM denoland/deno:alpine

USER deno
WORKDIR /app
COPY docker/mcp-codemode-sandbox/runner.ts /app/runner.ts

# Long-running no-op — portal uses `docker exec ... deno run ... /app/runner.ts`.
CMD ["deno", "eval", "await new Promise(() => {})"]
