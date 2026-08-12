#!/usr/bin/env bash
# Headless generation runner for the Magma eval benchmarks.
#
# Drives one `claude -p` agent per pending job from a gen_jobs.py jobdir, at bounded
# concurrency. Idempotent: a job whose out-file already exists is skipped, so rerunning
# the script retries only missing/failed jobs. Per-job agent transcripts land in
# $JOBDIR/logs/job-N.log.
#
# Usage: run_agents.sh JOBDIR [CONCURRENCY] [MODEL]
#   e.g. python3 eval/gen_jobs.py --tasks hard trap --trials 3 --jobdir /tmp/haiku-jobs
#        bash eval/run_agents.sh /tmp/haiku-jobs 14 haiku
#        python3 eval/collect.py --jobdir /tmp/haiku-jobs --out 'eval/generations_haiku_{bench}.json' --benches hard trap
set -u
JOBDIR=${1:?usage: run_agents.sh JOBDIR [CONCURRENCY] [MODEL]}
PAR=${2:-14}
export MODEL=${3:-haiku}
export JOBDIR

mkdir -p "$JOBDIR/logs"
python3 - "$JOBDIR" <<'EOF' > "$JOBDIR/pending.txt"
import json, os, sys
jd = sys.argv[1]
for j in json.load(open(os.path.join(jd, "manifest.json"))):
    if not os.path.exists(j["out"]):
        print(j["job"])
EOF
TOTAL=$(python3 -c "import json;print(len(json.load(open('$JOBDIR/manifest.json'))))")
echo "$(wc -l < "$JOBDIR/pending.txt") pending of $TOTAL jobs | concurrency $PAR | model $MODEL"

xargs -a "$JOBDIR/pending.txt" -P "$PAR" -I{} bash -c '
  i="$1"
  spec="$JOBDIR/job-$i.txt"
  log="$JOBDIR/logs/job-$i.log"
  timeout 600 claude -p --model "$MODEL" --allowedTools "Read" "Write" "Bash" -- \
    "Read the file $spec and follow the instructions in it exactly. It defines a self-contained evaluation task and the rules you must work under, and tells you where to write your result file." \
    > "$log" 2>&1
  st=$?
  echo "job $i exit $st"
' _ {}

DONE=$(ls "$JOBDIR/out" 2>/dev/null | wc -l)
echo "RUN-COMPLETE: $DONE/$TOTAL result files present"
