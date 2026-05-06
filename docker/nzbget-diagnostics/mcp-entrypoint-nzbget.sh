#!/bin/sh
set -e
cd /opt/nzbget-diagnostics
exec python -m nzbget_diagnostics
