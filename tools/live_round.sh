#!/bin/bash
# Dev tool: one traced live hunt on the running panel, then the analysis.
#   tools/live_round.sh SECONDS [setup.yaml]
S=${1:-300}; SETUP=${2:-setup.yaml}; P=http://127.0.0.1:8765
curl -s --max-time 10 -X POST $P/api/open --data "{}" >/dev/null; sleep 3
curl -s --max-time 10 -X POST $P/api/trace --data '{"on":true}' >/dev/null
curl -s --max-time 10 -X POST $P/api/hunt --data "{}" >/dev/null && echo "hunt started ($S s)"
sleep "$S"
curl -s --max-time 10 -X POST $P/api/stop --data "{}" >/dev/null; sleep 12
curl -s --max-time 10 -X POST $P/api/trace --data '{"on":false}' >/dev/null
D=$(ls -dt debug_trace/*/ | head -1)
python tools/analyze_trace.py "$D" --tracks --setup "$SETUP" 2>&1 | grep -vE "^  close_read"
python - "$D" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1] + "trace.jsonl", encoding="utf-8")]
for r in rows:
    if r["kind"] == "owner":
        print(f'{r["t"]:6.1f} CHECKED {r.get("item")} {r.get("tile")} -> {r.get("owner")}  {str(r.get("read"))[:70]}')
rs = [r for r in rows if r["kind"] == "round"]
print("FINAL:", [(a["id"], a["kind"], a["tile"], a["owner"]) for a in rs[-1]["armies"]] if rs else None)
PY
grep -aE "ALERT|known already|is the one at|map scale measured" rokwatch.log | tail -8
