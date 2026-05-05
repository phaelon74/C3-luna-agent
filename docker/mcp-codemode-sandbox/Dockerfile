# Isolated Deno runtime for Code Mode: portal runs `docker exec` per call.
FROM denoland/deno:alpine

USER deno
WORKDIR /app
COPY docker/mcp-codemode-sandbox/runner.ts /app/runner.ts

# Long-running no-op — portal uses `docker exec ... deno run ... /app/runner.ts`
# for actual work. Use plain busybox tail so the idle CMD doesn't need Deno's
# writable cache dir (container is read_only:true, only /tmp is tmpfs).
CMD ["tail", "-f", "/dev/null"]
