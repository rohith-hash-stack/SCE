#!/bin/bash
# Build every R1 pad file, PARALLEL repositories at a time (default 3), committing each finished file.
# Survives restarts: run it again and it continues.
#   harness/experiments/r1/build_pads.sh WORK_DIR
set -u
cd "$(dirname "$0")/../../.."
export WORK=${1:?work dir}
TOK=/home/user/models/qwen2-tokenizer/tokenizer.json
mkdir -p "$WORK/repos"
if [ ! -f "$TOK" ]; then  # the Qwen2.5 tokenizer, as published in the npm package @lenml/tokenizer-qwen2_5
  T=$(mktemp -d) && (cd "$T" && npm pack -q @lenml/tokenizer-qwen2_5@3.7.2 >/dev/null && tar -xzf ./*.tgz) \
    && mkdir -p "$(dirname "$TOK")" && cp "$T"/package/models/tokenizer*.json "$(dirname "$TOK")/"
fi

url() {
  case $1 in
    werkzeug) echo pallets/werkzeug ;; marshmallow) echo marshmallow-code/marshmallow ;; rich) echo Textualize/rich ;;
    tornado) echo tornadoweb/tornado ;; aiohttp) echo aio-libs/aiohttp ;; scrapy) echo scrapy/scrapy ;;
    axios) echo axios/axios ;; mocha) echo mochajs/mocha ;; fastify) echo fastify/fastify ;; hapi) echo hapijs/hapi ;;
  esac
}

one() {  # one type in one repository
  local kind=$1 repo=$2 rc
  local out=harness/experiments/r1/pads/$kind/$repo.json
  (
    flock 8
    [ -d "$WORK/repos/$repo/.git" ] || git clone -q --filter=blob:none "https://github.com/$(url "$repo").git" "$WORK/repos/$repo"
  ) 8>"$WORK/r1pad.clone.$repo.lock"
  while :; do
    PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 python -B -m harness.experiments.r1.pad build "$kind" "$repo" \
      "$WORK/repos/$repo" > "$WORK/r1pad.$kind.$repo.log" 2>&1
    rc=$?
    [ $rc -eq 3 ] || break
  done
  [ $rc -eq 0 ] || { echo "FAILED $kind $repo rc=$rc (see $WORK/r1pad.$kind.$repo.log)"; return; }
  (
    flock 9  # one git writer at a time
    if [ -n "$(git status --porcelain -- "$out")" ]; then
      git add "$out" && git commit -q -m "R1 pads: $kind $repo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JWST68Fi5NgbZEhS3wvVEz" -- "$out" \
        && for i in 1 2 3 4; do git push -q origin HEAD:feature/option3-contract-axis && break; sleep $((2 ** i)); done
    fi
  ) 9>"$WORK/r1pad.git.lock"
  echo "done $kind $repo"
}

# Two indexes of one repository's history at once would share the clone's worktree list; that is safe with git,
# but keep one job per repository at a time anyway (forward and newfn of the same repository run one after the other).
for kind in forward blast newfn; do
  for f in harness/experiments/suite/repos/*/$kind.json; do echo "$kind $(basename "$(dirname "$f")")"; done
done > "$WORK/r1pad.jobs"
declare -A busy=()
while read -r kind repo; do
  while :; do
    for r in "${!busy[@]}"; do kill -0 "${busy[$r]}" 2>/dev/null || unset "busy[$r]"; done
    [ "${#busy[@]}" -lt "${PARALLEL:-3}" ] && [ -z "${busy[$repo]:-}" ] && break
    sleep 5
  done
  one "$kind" "$repo" &
  busy[$repo]=$!
done < "$WORK/r1pad.jobs"
wait
echo ALL_PADS_DONE
