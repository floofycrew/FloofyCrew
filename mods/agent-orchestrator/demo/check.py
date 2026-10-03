#!/usr/bin/env python3
"""check.py -- did the demo run exercise the whole feature, and is the run hub-and-spoke?

    python3 demo/check.py [--site ~/floofy-demo/status-page] [--data <mod .floofy dir>] [--run <run key>]

Two halves, both read-only:

1. the SITE the four workers produced (index.html / styles.css / app.js / facts.json / check.sh): every
   file exists, index.html references the other two, the <ul id> worker-1 chose is the one app.js and
   check.sh use, facts.json parses, and the workers' own check.sh exits 0.
2. the BUS LOG of the newest (or named) run under the mod's data dir: at least one worker -> orchestrator
   ask, at least one orchestrator -> worker relay ask, at least one orchestrator -> worker answer carrying
   reply_to, a report row per worker, the run closed, every worker's final status at progress 100, and
   -- the point of the topology -- ZERO rows from a worker to a worker.

Exit 0 when every check passes, 1 otherwise; each line is PASS/FAIL so a failure names itself.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), what))
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + (f"  — {detail}" if detail and not ok else ""))


def default_data_dir() -> Path | None:
    """The mod's ``.floofy`` runtime dir: ``<data home>/mods/agent-orchestrator/.floofy`` (a dev link resolves into the checkout)."""
    env = os.environ.get("AGENT_ORCHESTRATOR_DATA")
    if env:
        return Path(env).expanduser()
    home = Path(os.environ.get("KIRO_HOME") or (Path.home() / ".kiro"))
    candidates = [home / "crew" / "floofycrew" / "mods" / "agent-orchestrator",
                  home / "crew" / "floofy" / "mods" / "agent-orchestrator"]
    for c in candidates:
        if (c / ".floofy").is_dir():
            return (c / ".floofy").resolve()
    hits = sorted((home / "crew").glob("*/mods/agent-orchestrator/.floofy/runs")) if (home / "crew").is_dir() else []
    return hits[0].parent.resolve() if hits else None


def check_site(site: Path) -> str:
    files = {n: site / n for n in ("index.html", "styles.css", "app.js", "facts.json", "check.sh")}
    for name, p in files.items():
        check(p.is_file() and p.stat().st_size > 0, f"site: {name} exists and is not empty", str(p))
    html = files["index.html"].read_text(encoding="utf-8", errors="replace") if files["index.html"].is_file() else ""
    check("styles.css" in html, "site: index.html links styles.css")
    check("app.js" in html, "site: index.html loads app.js")
    uls = re.findall(r"<ul\b[^>]*\bid\s*=\s*[\"']([^\"']+)[\"']", html, flags=re.I)
    check(len(uls) == 1, "site: index.html has exactly one <ul id=...>", f"found {uls}")
    list_id = uls[0] if uls else ""
    check(bool(list_id) and list_id.lower() not in ("facts", "list"), "site: worker-1 chose its own id (not the obvious one)", list_id)
    js = files["app.js"].read_text(encoding="utf-8", errors="replace") if files["app.js"].is_file() else ""
    check(bool(list_id) and list_id in js, "site: app.js targets the id worker-1 chose (learned through the orchestrator)", f"id={list_id!r}")
    check("facts.json" in js, "site: app.js fetches facts.json")
    sh = files["check.sh"].read_text(encoding="utf-8", errors="replace") if files["check.sh"].is_file() else ""
    check(bool(list_id) and list_id in sh, "site: check.sh knows the id too (second relayed question)", f"id={list_id!r}")
    css = files["styles.css"].read_text(encoding="utf-8", errors="replace") if files["styles.css"].is_file() else ""
    check(css.count("{") >= 4, "site: styles.css has >= 4 rules", f"{css.count('{')} rules")
    check("@import" not in css and "url(http" not in css, "site: styles.css has no external imports")
    try:
        facts = json.loads(files["facts.json"].read_text(encoding="utf-8")) if files["facts.json"].is_file() else None
    except ValueError as exc:
        facts = None
        check(False, "site: facts.json parses", str(exc))
    else:
        check(isinstance(facts, list) and len(facts) >= 5 and all(isinstance(f, dict) and {"label", "value"} <= set(f) for f in facts),
              "site: facts.json is a list of >= 5 {label, value} objects")
    if files["check.sh"].is_file():
        try:
            proc = subprocess.run(["bash", str(files["check.sh"])], cwd=str(site), capture_output=True, text=True, timeout=60)
            check(proc.returncode == 0, "site: the workers' own check.sh exits 0", (proc.stdout + proc.stderr).strip()[-400:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            check(False, "site: the workers' own check.sh exits 0", str(exc))
    return list_id


def pick_run(data: Path, wanted: str) -> Path | None:
    runs = data / "runs"
    if not runs.is_dir():
        return None
    dirs = [d for d in runs.iterdir() if (d / "meta.json").is_file()]
    if wanted:
        dirs = [d for d in dirs if wanted in d.name or wanted in json.loads((d / "meta.json").read_text()).get("run", "")]
    if not dirs:
        return None

    def created(d: Path) -> float:
        try:
            return float(json.loads((d / "meta.json").read_text()).get("created_at", 0))
        except (OSError, ValueError):
            return 0.0

    return max(dirs, key=created)


def check_bus(run_dir: Path) -> None:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    rows = []
    bus = run_dir / "bus.jsonl"
    if bus.is_file():
        for line in bus.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    statuses = json.loads((run_dir / "status.json").read_text(encoding="utf-8")) if (run_dir / "status.json").is_file() else {}
    workers = sorted({v for v in meta.get("nicks", {}).values() if v.startswith("worker-")})
    print(f"run: {meta.get('run')}  goal: {meta.get('goal')!r}  workers: {workers}  rows: {len(rows)}")

    is_w = lambda n: str(n).startswith("worker-")  # noqa: E731
    w2o_ask = [r for r in rows if is_w(r.get("from")) and r.get("to") == "orchestrator" and r.get("kind") == "ask"]
    o2w_ask = [r for r in rows if r.get("from") == "orchestrator" and is_w(r.get("to")) and r.get("kind") == "ask"]
    o2w_ans = [r for r in rows if r.get("from") == "orchestrator" and is_w(r.get("to")) and r.get("kind") == "answer"]
    w2w = [r for r in rows if is_w(r.get("from")) and is_w(r.get("to"))]
    reports = {r.get("from") for r in rows if r.get("kind") == "report"}
    sends = [r for r in rows if r.get("kind") == "send"]
    asks_ids = {r.get("id") for r in w2o_ask}

    check(len(workers) >= 4, "bus: at least four workers joined the run", f"{workers}")
    check(bool(meta.get("goal")), "bus: the run has a goal line (bus_run action=goal)", repr(meta.get("goal")))
    check(len(w2o_ask) >= 1, "bus: a worker asked the orchestrator something", f"{len(w2o_ask)} asks")
    check(len(o2w_ask) >= 1, "bus: the orchestrator relayed a question to the owning worker", f"{len(o2w_ask)} relay asks")
    check(any(r.get("reply_to") in asks_ids for r in o2w_ans), "bus: the orchestrator forwarded an answer with reply_to = the original ask",
          f"{len(o2w_ans)} answers, reply_to ids {[r.get('reply_to') for r in o2w_ans]}")
    check(not w2w, "bus: ZERO worker → worker rows (hub-and-spoke held)", f"{[(r.get('from'), r.get('to')) for r in w2w]}")
    check(set(workers) <= reports, "bus: every worker filed a work_report (logged as a report row)", f"reported: {sorted(reports)}")
    check(len(sends) >= 1, "bus: the orchestrator's session_send traffic was logged (seed prompts / the check.sh follow-up)", f"{len(sends)} send rows")
    final = {n: statuses.get(n, {}) for n in workers}
    check(all(int(s.get("progress") or 0) == 100 for s in final.values()), "bus: every worker's final status is progress 100",
          {n: s.get("progress") for n, s in final.items()})
    check(bool(meta.get("closed_at")), "bus: the run was closed (bus_run action=close)")
    print(f"{len(workers)} agents · {meta.get('counters', {}).get('messages', len(rows))} messages on the bus · "
          f"{len(w2o_ask)} asks up, {len(o2w_ask)} relays down, {len(o2w_ans)} answers forwarded")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="~/floofy-demo/status-page")
    ap.add_argument("--data", default="", help="the mod's .floofy dir (default: found under ~/.kiro/crew)")
    ap.add_argument("--run", default="", help="run key or dir-name fragment (default: newest run)")
    ap.add_argument("--bus-only", action="store_true")
    ap.add_argument("--site-only", action="store_true")
    a = ap.parse_args()
    if not a.bus_only:
        check_site(Path(a.site).expanduser())
    if not a.site_only:
        data = Path(a.data).expanduser() if a.data else default_data_dir()
        if data is None:
            check(False, "bus: the mod's data dir was found", "pass --data or set AGENT_ORCHESTRATOR_DATA")
        else:
            run_dir = pick_run(data, a.run)
            if run_dir is None:
                check(False, "bus: a run exists under the data dir", str(data / "runs"))
            else:
                check_bus(run_dir)
    failed = [w for ok, w in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {len(failed)}" if failed else " — the demo exercised the whole feature"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
