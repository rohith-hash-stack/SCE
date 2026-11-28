#!/usr/bin/env bash
# Generated from the 2026-10-19 branch audit (origin/develop is the default branch;
# there is no main/master). Protected: develop, eval/held-out-benchmark and
# eval/two-stage-rag-cache (the tournament cell restores its RAG cache from it).
# Archive unmerged / unrelated-history origin branches as annotated tags
# archive/<branch>, then (only with DRY_RUN=0) push those tags. Never deletes
# anything. Re-runnable: an existing tag is kept if it already points at the
# branch tip, and the script stops if it points anywhere else.
#   DRY_RUN=1 bash archive_branches.sh   # default: print what would happen
#   DRY_RUN=0 bash archive_branches.sh   # create + push the tags
set -euo pipefail
DRY_RUN="${DRY_RUN:-1}"
PROTECTED="main master develop eval/held-out-benchmark eval/two-stage-rag-cache"
BRANCHES=(
  Legacy
  ab-off-20260917T071652Z
  ab-on-20260917T055742Z
  baselines/authentic-external-engines
  claude/pilot-metrics-gate-eval-bartnc
  claude/prism-final-empirical-8arms-kp3tpt
  claude/sce-mcp-local-setup-5qo69f
  claude/semantic-context-engine-hld-cak4gz
  docs/research-paper-synthesis
  experiment/noise-filtering-spike
  feat/behavioral-graph-contracts
  feat/benchmark-optimizations
  feat/hierarchical-intent-profiling
  feat/prism-comparison-benchmark
  feat/synonyms-and-runtime-analysis
  feature/two-pass-phase-b-patches
  harness/eight-arm-slm
  manual-sanity-check/test-framework-support
  pilot-1
  pilot-2-20260917T095118Z
  pilot-4-patched-progress
  pilot-4-progress
  pilot-deepseek-progress
  pilot-execution
  pilot-fastapi-qwen7b-full-progress
  pilot-fastapi-qwen7b-full-v2-progress
  pilot-fastapi-qwen7b-full-v3-progress
  pilot-fastapi-qwen7b-holdout-progress
  pilot-full-20260916T091808Z
  pilot-qwen7b-holdout
  pilot-qwen7b-progress
  pilot-results-20260916T052740Z
  pilot-resweep-07ff0cd-20260921T150448Z
  pilot-resweep-20260921T072845Z
  pilot-resweep-20260921T105300Z
  pilot-sample-20260916T084652Z
  pilot-two-pass-v1-20260922T102710Z
  smoke-deepseek-progress
  smoke-fastapi-qwen7b-progress
  smoke-qwen7b-progress
  test/pagerank-repomap-smoke
  two-pass-artifact-preservation
)
run() { if [ "$DRY_RUN" = 1 ]; then echo "DRY-RUN: $*"; else "$@"; fi; }

git fetch --quiet origin '+refs/heads/*:refs/remotes/origin/*' '+refs/tags/*:refs/tags/*'
tags=()
for b in "${BRANCHES[@]}"; do
  for p in $PROTECTED; do [ "$b" = "$p" ] && { echo "refusing protected branch $b" >&2; exit 1; }; done
  sha=$(git rev-parse --verify --quiet "refs/remotes/origin/$b^{commit}") || { echo "skip: origin/$b not found"; continue; }
  tag="archive/$b"
  if existing=$(git rev-parse --verify --quiet "refs/tags/$tag^{commit}"); then
    [ "$existing" = "$sha" ] || { echo "STOP: $tag exists at $existing, origin/$b is at $sha" >&2; exit 1; }
    echo "kept: $tag -> ${sha:0:10} (already archived)"
  else
    run git tag -a "$tag" "$sha" -m "Archive of origin/$b at ${sha:0:10} ($(git log -1 --format=%cs "$sha"))"
    echo "tag:  $tag -> ${sha:0:10}"
  fi
  tags+=("refs/tags/$tag")
done
echo "${#tags[@]} archive tags"
[ "${#tags[@]}" -gt 0 ] && run git push origin "${tags[@]}"
# Verify on the remote (works in dry-run too: lists what is there now)
git ls-remote --tags origin 'refs/tags/archive/*' | grep -vc '\^{}' || true
