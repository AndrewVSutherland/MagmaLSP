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

def done(path):
    # complete = parseable JSON whose "code" is a nonempty STRING; an empty/truncated/
    # invalid/mistyped out-file (agent died mid-write, or wrote e.g. a list) must be
    # retried, not skipped forever
    try:
        with open(path) as f:
            c = json.load(f).get("code")
        return isinstance(c, str) and bool(c.strip())
    except (OSError, json.JSONDecodeError, AttributeError):
        return False

for j in json.load(open(os.path.join(jd, "manifest.json"))):
    if not done(j["out"]):
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
  exit $st
' _ {}
XARGS_ST=$?   # 123 = some agent invocation failed; completeness is judged below regardless

# Final truth = validated result files, not process statuses (a rerun that repairs earlier
# failures should exit 0). Recount with the same done() validation used for pending.
REMAINING=$(python3 - "$JOBDIR" <<'EOF'
import json, os, sys
jd = sys.argv[1]

def done(path):
    try:
        with open(path) as f:
            c = json.load(f).get("code")
        return isinstance(c, str) and bool(c.strip())
    except (OSError, json.JSONDecodeError, AttributeError):
        return False

print(sum(1 for j in json.load(open(os.path.join(jd, "manifest.json"))) if not done(j["out"])))
EOF
)
echo "RUN-COMPLETE: $((TOTAL - REMAINING))/$TOTAL validated result files (agent exit failures: xargs status $XARGS_ST)"
[ "$REMAINING" -eq 0 ] || { echo "RUN-INCOMPLETE: $REMAINING job(s) missing/invalid — rerun this script to retry them"; exit 1; }
