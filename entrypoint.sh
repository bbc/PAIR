#!/usr/bin/env bash
set -euo pipefail

# If arguments are provided, run them as command
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

# If a main script exists, you can enable this later by uncommenting:
# if [ -f /workspace/src/main.py ]; then
#   exec python /workspace/src/main.py
# fi

echo "Container is ready. Working dir: $(pwd)."
echo "Tip: pass a command to run, e.g. 'docker compose run --rm app python -V'"

# Keep container alive for interactive session
exec bash
