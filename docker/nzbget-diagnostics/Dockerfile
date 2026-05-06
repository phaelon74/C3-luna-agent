# Idle sidecar; portal attaches via `docker exec -i mose-nzbget-diagnostics …`.
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/nzbget-diagnostics

COPY docker/nzbget-diagnostics/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY docker/nzbget-diagnostics/nzbget_diagnostics ./nzbget_diagnostics

ENV PYTHONPATH=/opt/nzbget-diagnostics
ENV PYTHONUNBUFFERED=1

COPY docker/nzbget-diagnostics/mcp-entrypoint-nzbget.sh /usr/local/bin/mcp-entrypoint-nzbget
RUN chmod +x /usr/local/bin/mcp-entrypoint-nzbget

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
