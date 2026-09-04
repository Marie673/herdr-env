#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh
herdr_api '{"id":"agent-view-space:set","method":"agent.view.set","params":{"source":"agent-view-space","label":"current space","filter":{"op":"eq","field":"workspace_id","value":{"context":"current_workspace_id"}}}}'
herdr_state_write current
