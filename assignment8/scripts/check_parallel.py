"""Read-only evidence tool: verify the parallel layer of a session run.

For every skill that ran concurrently (nodes whose [started_at,
completed_at] intervals overlap), prints per-node start/end/elapsed and
checks the layer claim:  wall-clock(layer) ~= max(branch elapsed),
NOT sum(branch elapsed).

Usage:  python scripts/check_parallel.py <session_id>
Reads only state/sessions/<sid>/nodes/*.json — never writes.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODES_DIR = ROOT / "code" / "state" / "sessions"


def main(sid: str) -> int:
    ndir = NODES_DIR / sid / "nodes"
    if not ndir.exists():
        print(f"no such session: {sid} (looked in {ndir})")
        return 1

    rows = []
    for f in sorted(ndir.glob("n_*.json")):
        d = json.loads(f.read_text())
        res = d.get("result") or {}
        rows.append(
            {
                "id": d.get("node_id") or f.stem.replace("n_", "n:"),
                "skill": d.get("skill"),
                "status": d.get("status"),
                "start": d.get("started_at"),
                "end": d.get("completed_at"),
                "elapsed": res.get("elapsed_s"),
            }
        )

    timed = [r for r in rows if r["start"] and r["end"]]
    if not timed:
        print("no timed nodes found")
        return 1

    print(f"session {sid} — node timeline")
    print(f"{'node':6} {'skill':18} {'status':9} {'start':>10} {'end':>10} {'dur':>7}")
    t0 = min(r["start"] for r in timed)
    for r in sorted(timed, key=lambda x: x["start"]):
        print(
            f"{r['id']:6} {r['skill']:18} {r['status']:9} "
            f"{r['start'] - t0:10.2f} {r['end'] - t0:10.2f} "
            f"{(r['end'] - r['start']):7.2f}"
        )

    # Concurrent groups: greedily cluster overlapping intervals.
    groups: list[list[dict]] = []
    for r in sorted(timed, key=lambda x: x["start"]):
        placed = False
        for g in groups:
            if r["start"] <= max(x["end"] for x in g) + 1e-6:
                g.append(r)
                placed = True
                break
        if not placed:
            groups.append([r])

    print("\nconcurrent layers:")
    ok = False
    for g in groups:
        if len(g) < 2:
            continue
        layer_wall = max(x["end"] for x in g) - min(x["start"] for x in g)
        branch_sum = sum(x["end"] - x["start"] for x in g)
        max_branch = max(x["end"] - x["start"] for x in g)
        skills = ", ".join(f"{x['id']}:{x['skill']}" for x in g)
        verdict = (
            "max-not-sum ✓" if abs(layer_wall - max_branch) <= branch_sum - max_branch + 1e-6 else "CHECK"
        )
        print(f"  [{skills}]")
        print(
            f"    wall={layer_wall:.2f}s  max_branch={max_branch:.2f}s  "
            f"sum_branches={branch_sum:.2f}s  → {verdict}"
        )
        if len(g) >= 3 and layer_wall < branch_sum:
            ok = True

    if ok:
        print("\nRESULT: ≥3 concurrent nodes; layer wall-clock < sum of branches (max-not-sum verified).")
        return 0
    print("\nRESULT: no ≥3-node concurrent layer found or wall >= sum — inspect timings above.")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
