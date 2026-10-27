# Demo Runbook — YouTube Video (Parts 1–5)

Replay command for any saved session (zero API quota):
`cd code && uv run python replay.py <session_id>`

Pre-video checklist: `uv run pytest tests/test_recovery.py -q` → 22 passed ·
gateway on :8108 (auto-starts) · screenshots of `state/backups/` if you reset memory.

---

## 0. Setup (30 s, terminal already open)

Show: `ls code/prompts/` (coder.md + review_sentiment.md exist), `cat .env | grep GEMINI_MODEL`.

## 1. Five base queries

**Live (fast):**
```
uv run flow.py "Say hello in one short sentence."          # 2 nodes, ~10 s
uv run flow.py "Read /nonexistent/path.txt and tell me what's in it."   # 2 nodes, fail-fast
```

**Replay (long ones):**
```
uv run python replay.py s8-3968623e    # A: 4 nodes planner→researcher→distiller→formatter
uv run python replay.py s8-0a9d2171    # I: 7 nodes, coder + sandbox, Berlin–Paris 1,640,756
```

**Kill + resume (the money shot):**
1. `cat code/state/sessions/s8-5c5d72a6/graph.json | python3 -m json.tool | head -40`
   — pause on: planner complete, 3 researchers running, coder/formatter pending (kill point).
2. `cd code && uv run flow.py --resume s8-5c5d72a6`
   — watch running→pending reset, researchers re-run, sandbox print
   `Fastest growing city: Kinshasa (5.13%)`.

Mention the honest notes (README): 9.7 s hello vs "<3 s" bound under the 4 s cooldown;
kill shape = 3 running due to per-batch flush; answer source-dependent + sandbox-verified.

## 2. Parallel fan-out

```
python scripts/check_parallel.py s8-4d752709
```
Point at the three lines: wall = 51.62 s = max branch, sum = 142.84 s —
"maximum, not sum". Note same finish timestamp (gather barrier) in the timeline.

## 3. Critic pass + fail with recovery

```
python3 - <<'EOF'
import json
for sid in ["s8-826cd93e", "s8-9d227709"]:
    print("==", sid)
    import glob
    for f in sorted(glob.glob(f"code/state/sessions/{sid}/nodes/n_*.json")):
        d = json.load(open(f)); o = (d.get("result") or {}).get("output")
        if d["skill"] == "critic": print("  critic:", json.dumps(o))
        if d["skill"] == "formatter": print("  answer:", str(o)[:160])
EOF
```
Show: run 1 verdict `pass` · run 2 verdict `fail` → `critic-fail recovery: planner node
n:5` → corrected JSON → second critic `pass`. Same instruction, two review texts.

## 4. Coder skill

- `cat code/prompts/coder.md` — scroll the sections (when to emit, schema, examples).
- `python3 -c "import json; print(json.load(open('code/state/sessions/s8-4d752709/nodes/n_005.json'))['result']['output']['code'])"` — emitted Python.
- `python3 -c "import json; print(json.load(open('code/state/sessions/s8-4d752709/nodes/n_007.json'))['result']['output']['stdout'])"` — sandbox execution, exit 0.

## 5. New skill — review_sentiment (run LIVE, ~45 s)

```
uv run flow.py "Classify the sentiment of each Galaxy Buds review as positive, neutral, or negative, then summarize the overall reception:
1. Amazing sound quality and the noise cancelling is best in class.
2. They hurt my ears after an hour and the battery dies too fast.
3. Okay for the price, nothing special but gets the job done."
```
Expect 5 nodes, `positive / negative / neutral` all correct. Then:
- `grep -A5 "review_sentiment" agent_config.yaml` — yaml entry.
- `grep "review_sentiment" prompts/planner.md` — planner knows it.
- `grep "review_sentiment" ../gateway/agent_routing.yaml` — one config pin line.
- `grep provider code/state/sessions/<new-sid>/nodes/n_00*.json` — show `provider: gemini`.
- `python3 ../scripts/check_parallel.py <new-sid>` — 3 classifiers concurrent.

## 6. Close on README

Scroll `assignment8/README.md` — the five PART headings with tables (the "big font"
results), then `pytest` → 22 passed, then the Change Inventory table:
flow.py/gateway code/tests untouched.

---

Timing budget: setup 1 min · Part 1 replay-heavy 4 min · Part 2 1 min · Part 3 2 min ·
Part 4 2 min · Part 5 live 2 min · close 1 min ≈ **13 minutes**.
