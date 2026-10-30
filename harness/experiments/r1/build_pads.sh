#!/bin/bash
# Build every R1 pad file, one repository at a time, committing each finished file (survives restarts: rerun it).
#   harness/experiments/r1/build_pads.sh WORK_DIR
set -u
cd "$(dirname "$0")/../../.."
WORK=${1:?work dir}
TOK=/home/user/models/qwen2-tokenizer/tokenizer.json
mkdir -p "$WORK/repos"
if [ ! -f "$TOK" ]; then  # the Qwen2.5 tokenizer, as published in the npm package @lenml/tokenizer-qwen2_5
  T=$(mktemp -d) && (cd "$T" && npm pack -q @lenml/tokenizer-qwen2_5@3.7.2 >/dev/null && tar -xzf ./*.tgz) \
    && mkdir -p "$(dirname "$TOK")" && cp "$T"/package/models/tokenizer*.json "$(dirname "$TOK")/"
fi
declare -A URL=([werkzeug]=pallets/werkzeug [marshmallow]=marshmallow-code/marshmallow [rich]=Textualize/rich
  [tornado]=tornadoweb/tornado [aiohttp]=aio-libs/aiohttp [scrapy]=scrapy/scrapy [axios]=axios/axios
  [mocha]=mochajs/mocha [fastify]=fastify/fastify [hapi]=hapijs/hapi)
for kind in forward blast newfn; do
  for f in harness/experiments/suite/repos/*/$kind.json; do
    repo=$(basename "$(dirname "$f")")
    out=harness/experiments/r1/pads/$kind/$repo.json
    [ -d "$WORK/repos/$repo/.git" ] || git clone -q --filter=blob:none "https://github.com/${URL[$repo]}.git" "$WORK/repos/$repo"
    while :; do
      PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 python -B -m harness.experiments.r1.pad build "$kind" "$repo" "$WORK/repos/$repo"
      rc=$?
      [ $rc -eq 3 ] || break
    done
    [ $rc -eq 0 ] || { echo "FAILED $kind $repo rc=$rc"; continue; }
    if [ -n "$(git status --porcelain -- "$out")" ]; then
      git add "$out" && git commit -q -m "R1 pads: $kind $repo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01JWST68Fi5NgbZEhS3wvVEz" -- "$out" \
        && for i in 1 2 3 4; do git push -q origin HEAD:feature/option3-contract-axis && break; sleep $((2 ** i)); done
    fi
    echo "done $kind $repo"
  done
done
echo ALL_PADS_DONE
