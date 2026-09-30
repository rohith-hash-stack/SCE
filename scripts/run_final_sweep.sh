#!/usr/bin/env bash
# Runs the full 8-arm sweep for each corpus given as an argument, in order,
# then commits and pushes that corpus's raw cell records before moving on.
# Usage: OPENAI_API_KEY=... scripts/run_final_sweep.sh trpc express fastapi django
set -uo pipefail
cd "$(dirname "$0")/.."
BRANCH="${SWEEP_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
for repo in "$@"; do
  out="reports/final_sweep/full/$repo"
  echo "=== [$(date -u +%H:%M:%S)] START $repo"
  python -m benchmarks.final_sweep.runner --repo "$repo" --seeds 42,43,44,45,46,47,48,49,50,51 \
    --temperature 0.4 --workers 8 --tpm-limit 150000 --max-cost-usd 25 --out "$out"
  echo "=== [$(date -u +%H:%M:%S)] RUNNER EXIT $repo $?"
  git add -f "$out"
  git commit -q -m "Final sweep results: $repo (8 arms x 10 seeds, T=0.4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JWST68Fi5NgbZEhS3wvVEz" || echo "nothing to commit for $repo"
  for delay in 0 2 4 8 16; do
    sleep "$delay"
    git push -q origin "HEAD:$BRANCH" && { echo "=== PUSHED $repo"; break; }
  done
  echo "=== [$(date -u +%H:%M:%S)] DONE $repo"
done
