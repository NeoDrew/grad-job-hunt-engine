#!/bin/bash
# Daily headless auto-apply runner (optional "applying" tier).
# A scheduler (launchd/cron) fires this once a day; it runs a headless Claude Code agent that reads the
# candidate's brief, works the latest sweep digest top-down, and applies to genuine fits through an
# already-open, logged-in Chrome driven by chromeflow. One summary message per run.
#
# Configure via env (or edit the defaults):
#   ENGINE_DIR   path to this repo                      (default: two levels up)
#   DAILY_LIMIT  max applications per run               (default 10)
#   NOTIFY_CMD   command that sends one message, e.g. a Telegram/Slack script taking the text as $1
#   CLAUDE_BIN   claude CLI path                        (default: found on PATH)
#   MODEL        claude model alias                     (default sonnet)
# Manual test: DAILY_LIMIT=2 ./run.sh --now   (skips the random start jitter)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="${ENGINE_DIR:-$(cd "$HERE/../.." && pwd)}"
DAILY_LIMIT="${DAILY_LIMIT:-10}"
NOTIFY_CMD="${NOTIFY_CMD:-echo}"
MODEL="${MODEL:-sonnet}"
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || true)}"
mkdir -p "$HERE/logs"; LOG="$HERE/logs/run_$(date +%Y-%m-%d).log"
[[ "${1:-}" == "--now" ]] || sleep $(( RANDOM % 600 ))   # human-like start jitter, up to 10 min
[[ -x "$CLAUDE_BIN" ]] || { $NOTIFY_CMD "daily-apply: claude CLI not found"; exit 4; }
pgrep -x "Google Chrome" >/dev/null || { $NOTIFY_CMD "daily-apply skipped: Chrome not running"; exit 5; }
esc() { printf '%s' "$1" | sed -e 's/[&|]/\\&/g'; }
PROMPT=$(sed -e "s|@@ENGINE_DIR@@|$(esc "$ENGINE_DIR")|g" -e "s|@@DAILY_LIMIT@@|$(esc "$DAILY_LIMIT")|g" \
             -e "s|@@NOTIFY_CMD@@|$(esc "$NOTIFY_CMD")|g" "$HERE/prompt.template.txt")
echo "=== run $(date -u +%FT%TZ) cap=$DAILY_LIMIT ===" >> "$LOG"
OUT=$("$CLAUDE_BIN" --dangerously-skip-permissions --model "$MODEL" -p "$PROMPT" 2>>"$LOG"); EC=$?
{ echo "--- exit $EC ---"; echo "$OUT"; } >> "$LOG"
[[ $EC -eq 0 ]] || { $NOTIFY_CMD "daily-apply errored (exit $EC), see $LOG"; exit 7; }
