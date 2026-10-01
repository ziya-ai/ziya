#!/usr/bin/env bash
# Localize the runaway synchronous loop in the freezing conversation by
# mounting the real MarkdownRenderer once per message in its OWN subprocess.
#
# A synchronous infinite loop cannot be caught by an in-process watchdog
# (jest's timeout never fires because the event loop never turns), so the
# deadline is enforced from outside with `timeout`. A killed run IS the hit.
#
# usage: scripts/freeze-bisect.sh [stub_d3] [msg ...]
#   stub_d3 = 1 (default) stubs D3Renderer  -> hang implicates markdown/diff path
#   stub_d3 = 0           runs real D3      -> hang implicates a diagram plugin
set -u

cd "$(dirname "$0")/../frontend" || exit 1

STUB="${1:-1}"
shift || true
MSGS=("$@")
if [ ${#MSGS[@]} -eq 0 ]; then MSGS=(1 3 5 7 9 11); fi

DEADLINE="${FREEZE_DEADLINE:-40}"

for i in "${MSGS[@]}"; do
    out=$(CI=true FREEZE_MSG="$i" FREEZE_STUB_D3="$STUB" \
        timeout "$DEADLINE" npx craco test \
        --watchAll=false \
        --testPathPattern=freezeRenderBisect \
        --transformIgnorePatterns "node_modules/(?!(marked)/)" 2>&1)
    rc=$?
    if [ $rc -eq 124 ]; then
        echo "msg $i : *** HANG *** (killed at ${DEADLINE}s, stub_d3=$STUB)"
    elif echo "$out" | grep -q "MOUNT OK"; then
        echo "msg $i : ok  $(echo "$out" | grep -o 'MOUNT OK in [0-9]*ms' | head -1)"
    else
        echo "msg $i : FAILED rc=$rc"
        echo "$out" | grep -E "●|Error|error" | head -8 | sed 's/^/         /'
    fi
done
