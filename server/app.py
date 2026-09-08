"""Dashboard sidecar for claims-improvement-james.

Serves:
- /api/runs                      local experiment runs (runs/<run>/<paper>/...)
- /api/runs/{run}/{paper}/...    artifact, trace, evaluation, source payload
- /api/network/...               cached proxy for the public Claims API (mainnet by default)
- /api/agenda                    persistent next-step checklist (server/data/agenda.json)
- /api/skill                     the mounted skill pack (files + hashes)
- /api/handoff                   a ready-to-paste prompt for the next Claude session
- /                              the built Vue dashboard (dashboard/dist)

Run:  .venv/bin/python server/app.py --port 8795
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from miner.agent_v1.skillpack import load_skill_pack  # noqa: E402

RUNS_DIR = ROOT / "runs"
DATA_DIR = ROOT / "server" / "data"
DIST_DIR = ROOT / "dashboard" / "dist"
CLAIMS_API = os.getenv("CLAIMS_PUBLIC_API", "https://api.claims111.ai").rstrip("/")
NETWORK = os.getenv("CLAIMS_DASHBOARD_NETWORK", "mainnet")
MY_UID = int(os.getenv("CLAIMS_MY_UID", "192"))
MY_HOTKEY = os.getenv("CLAIMS_MY_HOTKEY", "5H1fBPPQHJvrwo93S7s191x1emiD3FHT4eERs5GwqTTSyevP")

app = FastAPI(title="claims-improvement-james dashboard", version="0.1.0")
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


# ------------------------------------------------------------------ utilities
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", value or ""):
        raise HTTPException(status_code=400, detail="invalid path segment")
    return value


def _cached_get(path: str, params: dict[str, Any] | None = None, ttl: float = 60.0) -> Any:
    key = json.dumps([path, params or {}], sort_keys=True)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    response = requests.get(f"{CLAIMS_API}{path}", params=params, timeout=60)
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text[:300])
    payload = response.json()
    with _cache_lock:
        _cache[key] = (now, payload)
    return payload


# ------------------------------------------------------------------ local runs
def _paper_summary(run_name: str, paper_dir: Path) -> dict[str, Any]:
    meta = _read_json(paper_dir / "run_meta.json") or {}
    evaluation = _read_json(paper_dir / "evaluation.json") or {}
    artifact_path = paper_dir / "agent_output.json"
    manifest = _read_json(paper_dir / "backend_manifest.json") or {}
    trace = _read_json(paper_dir / "staged_trace.json") or {}
    stats = evaluation.get("stats") or {}
    artifact_exists = artifact_path.exists()
    claim_count = stats.get("claims")
    if claim_count is None and artifact_exists:
        artifact = _read_json(artifact_path) or {}
        claim_count = len(((artifact.get("logic") or {}).get("claims")) or [])
        stats = {"claims": claim_count, "evidence_records": len(((artifact.get("evidence") or {}).get("records")) or [])}
    usage = manifest.get("usage") or {}
    return {
        "run_name": run_name,
        "paper_id": paper_dir.name,
        "title": meta.get("title") or (_read_json(paper_dir / "paper.json") or {}).get("title") or paper_dir.name,
        "status": meta.get("status") or ("completed" if artifact_exists else "missing"),
        "error": meta.get("error"),
        "runtime": meta.get("runtime") or manifest.get("runtime"),
        "model": meta.get("model") or manifest.get("model"),
        "models": manifest.get("models") or trace.get("models"),
        "note": meta.get("note"),
        "started_at": meta.get("started_at"),
        "ended_at": meta.get("ended_at"),
        "wall_seconds": meta.get("wall_seconds") or manifest.get("elapsed_seconds"),
        "cost_usd": usage.get("cost_usd") if usage else stats.get("cost_usd"),
        "tokens": usage.get("total_tokens") if usage else stats.get("total_tokens"),
        "stats": stats,
        "quality_estimate": evaluation.get("quality_estimate"),
        "diagnostic_quality_estimate": (evaluation.get("deterministic") or {}).get("diagnostic_quality_estimate"),
        "adjudication_quality_estimate": evaluation.get("adjudication_quality_estimate"),
        "severity_summary": (evaluation.get("deterministic") or {}).get("severity_summary"),
        "judge": {k: (evaluation.get("judge") or {}).get(k) for k in ("verdicts", "rejected_count", "weak_count", "accepted_count", "model")} if evaluation.get("judge") else None,
        "has_trace": (paper_dir / "staged_trace.json").exists(),
        "has_evaluation": (paper_dir / "evaluation.json").exists(),
        "artifact_bytes": artifact_path.stat().st_size if artifact_exists else 0,
        "updated_at": max((p.stat().st_mtime for p in paper_dir.glob("*.json")), default=0),
    }


def _neuron_task_summary(task_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for paper_dir in sorted(task_dir.iterdir()):
        if paper_dir.is_dir():
            rows.append(_paper_summary(f"neuron/{task_dir.parent.name}/{task_dir.name}", paper_dir))
    return rows


@app.get("/api/runs")
def list_runs() -> Any:
    runs: list[dict[str, Any]] = []
    if not RUNS_DIR.exists():
        return {"runs": []}
    for run_dir in sorted(RUNS_DIR.iterdir()):
        if not run_dir.is_dir():
            continue
        if run_dir.name == "neuron":
            for network_dir in sorted(run_dir.iterdir()):
                if not network_dir.is_dir():
                    continue
                for task_dir in sorted(network_dir.iterdir(), reverse=True):
                    if task_dir.is_dir() and task_dir.name.startswith("task_"):
                        papers = _neuron_task_summary(task_dir)
                        runs.append(_run_row(f"neuron/{network_dir.name}/{task_dir.name}", papers, kind="neuron"))
            continue
        papers = [_paper_summary(run_dir.name, paper_dir) for paper_dir in sorted(run_dir.iterdir()) if paper_dir.is_dir()]
        runs.append(_run_row(run_dir.name, papers, kind="experiment"))
    runs.sort(key=lambda row: row.get("updated_at") or 0, reverse=True)
    return {"runs": runs}


def _run_row(name: str, papers: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    completed = [p for p in papers if p["status"] == "completed"]
    claims = [p["stats"].get("claims") or 0 for p in completed]
    quality = [p["quality_estimate"] for p in completed if isinstance(p.get("quality_estimate"), int | float)]
    cost = [p["cost_usd"] for p in completed if isinstance(p.get("cost_usd"), int | float)]
    return {
        "name": name,
        "kind": kind,
        "papers": papers,
        "paper_count": len(papers),
        "completed": len(completed),
        "failed": sum(1 for p in papers if p["status"] == "failed"),
        "avg_claims": round(sum(claims) / len(claims), 1) if claims else None,
        "avg_quality": round(sum(quality) / len(quality), 4) if quality else None,
        "total_cost_usd": round(sum(cost), 4) if cost else None,
        "runtime": next((p.get("runtime") for p in papers if p.get("runtime")), None),
        "model": next((p.get("model") for p in papers if p.get("model")), None),
        "updated_at": max((p.get("updated_at") or 0 for p in papers), default=0),
    }


def _paper_dir(run_name: str, paper_id: str) -> Path:
    parts = [_safe_segment(part) for part in run_name.split("/")]
    paper_dir = RUNS_DIR.joinpath(*parts, _safe_segment(paper_id))
    if not paper_dir.is_dir():
        raise HTTPException(status_code=404, detail="run/paper not found")
    return paper_dir


@app.get("/api/runs/{run_name:path}/paper/{paper_id}/summary")
def paper_summary(run_name: str, paper_id: str) -> Any:
    paper_dir = _paper_dir(run_name, paper_id)
    return _paper_summary(run_name, paper_dir)


@app.get("/api/runs/{run_name:path}/paper/{paper_id}/file/{name}")
def paper_file(run_name: str, paper_id: str, name: str) -> Any:
    paper_dir = _paper_dir(run_name, paper_id)
    allowed = {
        "artifact": "agent_output.json",
        "trace": "staged_trace.json",
        "evaluation": "evaluation.json",
        "source": "source_payload.json",
        "manifest": "backend_manifest.json",
        "meta": "run_meta.json",
        "validation": "agent_validation_report.json",
        "feedback": "validation_feedback.json",
        "stdout": "backend_stdout.txt",
        "stderr": "backend_stderr.txt",
    }
    filename = allowed.get(name)
    if not filename:
        raise HTTPException(status_code=404, detail="unknown file")
    path = paper_dir / filename
    if not path.exists() and name == "source":
        path = paper_dir / "data" / "agent_v1_source_payload.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    if path.suffix == ".json":
        return JSONResponse(content=_read_json(path))
    return JSONResponse(content={"text": path.read_text(encoding="utf-8", errors="replace")[-20000:]})


@app.get("/api/runs/{run_name:path}/paper/{paper_id}/log")
def paper_log(run_name: str, paper_id: str) -> Any:
    parts = [_safe_segment(part) for part in run_name.split("/")]
    log_path = RUNS_DIR.joinpath(*parts[:-1], f"{parts[-1]}.log") if parts else None
    if log_path and log_path.exists():
        return {"text": log_path.read_text(encoding="utf-8", errors="replace")[-30000:]}
    return {"text": ""}


# --------------------------------------------------------------- network proxy
@app.get("/api/network/overview")
def network_overview(network: str = Query(NETWORK), limit: int = Query(12)) -> Any:
    runs = _cached_get("/public/runs", {"network": network, "limit": limit}, ttl=90)
    leaderboard = _cached_get("/public/leaderboard", {"network": network, "period": "week"}, ttl=300)
    stats = _cached_get("/public/stats", {"network": network}, ttl=300)
    me = None
    try:
        me = _cached_get(f"/public/miners/by-hotkey/{MY_HOTKEY}", {"network": network, "limit": 6}, ttl=120)
    except HTTPException:
        pass
    return {"network": network, "runs": runs, "leaderboard": leaderboard, "stats": stats, "me": me, "my_uid": MY_UID, "my_hotkey": MY_HOTKEY}


@app.get("/api/network/runs/{run_id}")
def network_run(run_id: str, network: str = Query(NETWORK)) -> Any:
    run_id = _safe_segment(run_id)
    detail = _cached_get(f"/public/runs/{run_id}", ttl=300)
    batch_scores = _cached_get(f"/public/runs/{run_id}/batch-scores", {"network": network}, ttl=300)
    silver = _cached_get(f"/public/runs/{run_id}/silver-scores", {"network": network}, ttl=300)
    batch = None
    if detail.get("batch_id"):
        try:
            batch = _cached_get(f"/public/batches/{detail['batch_id']}", ttl=600)
        except HTTPException:
            batch = None
    return {"run": detail, "batch_scores": batch_scores, "silver_scores": silver, "batch": batch}


@app.get("/api/network/miners/{uid}")
def network_miner(uid: int, network: str = Query(NETWORK), limit: int = Query(8)) -> Any:
    profile = _cached_get(f"/public/miners/{uid}", {"network": network, "limit": limit}, ttl=120)
    return profile


@app.get("/api/network/miners/{uid}/silver-feedback")
def network_miner_feedback(uid: int, network: str = Query(NETWORK), run_id: str | None = None) -> Any:
    params: dict[str, Any] = {"network": network}
    if run_id:
        params["run_id"] = _safe_segment(run_id)
    return _cached_get(f"/public/miners/{uid}/silver-feedback", params, ttl=300)


@app.get("/api/network/papers/{paper_id}")
def network_paper(paper_id: str, network: str = Query(NETWORK)) -> Any:
    paper_id = _safe_segment(paper_id)
    papers = _cached_get("/public/papers", {"network": network}, ttl=1800)
    for paper in papers if isinstance(papers, list) else []:
        if paper.get("paper_id") == paper_id:
            return paper
    raise HTTPException(status_code=404, detail="paper not in public catalog")


# ------------------------------------------------------------------- agenda
AGENDA_PATH = DATA_DIR / "agenda.json"


def _default_agenda() -> dict[str, Any]:
    return {"items": [], "updated_at": None}


@app.get("/api/agenda")
def get_agenda() -> Any:
    return _read_json(AGENDA_PATH) or _default_agenda()


@app.put("/api/agenda")
def put_agenda(payload: dict[str, Any]) -> Any:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    items = payload.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    data = {"items": items, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    AGENDA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


# --------------------------------------------------------------------- skill
@app.get("/api/skill")
def get_skill(dir: str | None = None) -> Any:
    skill_dir = Path(dir) if dir else Path(os.getenv("SUBNET_CLAIMS_AGENT_SKILL_DIR", str(ROOT / "miner" / "agent_v1" / "skills" / "claims_coverage")))
    if not skill_dir.is_absolute():
        skill_dir = ROOT / skill_dir
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail="skill dir not found")
    pack = load_skill_pack(skill_dir)
    return {
        "name": pack.name,
        "root_dir": str(pack.root_dir),
        "sha256": pack.sha256,
        "metadata": pack.metadata,
        "resources": [
            {"path": path, "sha256": resource.sha256, "bytes": len(resource.text.encode("utf-8")), "text": resource.text}
            for path, resource in sorted(pack.resources.items())
        ],
        "available": [p.name for p in (ROOT / "miner" / "agent_v1" / "skills").iterdir() if p.is_dir()],
    }


# ------------------------------------------------------------------ benchmark
@app.get("/api/benchmark/{run_name}")
def benchmark(run_name: str, run_id: str | None = None, refresh: bool = False) -> Any:
    """Comparison of a local experiment against the real Silver scores of the batch it replayed."""
    parts = [_safe_segment(part) for part in run_name.split("/")]
    path = RUNS_DIR.joinpath(*parts, "comparison.json")
    if path.exists() and not refresh:
        return _read_json(path)
    if not run_id:
        raise HTTPException(status_code=400, detail="no comparison.json yet; pass run_id to build one")
    result = subprocess.run(
        [sys.executable, "-m", "tools.compare_silver", "--run-name", run_name, "--run-id", _safe_segment(run_id)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr[-500:])
    return _read_json(path)


@app.get("/api/coverage/{run_name}")
def coverage_estimate(run_name: str) -> Any:
    """Measured coverage of a batch's pre-existing Silver units (tools/estimate_coverage.py)."""
    parts = [_safe_segment(part) for part in run_name.split("/")]
    path = RUNS_DIR.joinpath(*parts, "coverage_estimate.json")
    if not path.exists():
        raise HTTPException(status_code=404, detail="no coverage_estimate.json; run tools.estimate_coverage first")
    return _read_json(path)


# --------------------------------------------------------------------- status
@app.get("/api/status")
def status() -> Any:
    env_path = ROOT / ".env"
    env: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                if not re.search(r"KEY|SECRET|TOKEN|PASSWORD", key, re.IGNORECASE):
                    env[key.strip()] = value.strip()
    processes = []
    try:
        out = subprocess.run(["bash", "-lc", "ps -eo pid,etimes,rss,args | grep -E 'neurons\\.miner|tools\\.run_paper|server/app\\.py' | grep -v grep"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) == 4:
                processes.append({"pid": int(parts[0]), "elapsed_seconds": int(parts[1]), "rss_kb": int(parts[2]), "command": parts[3][:200]})
    except Exception:
        pass
    git = {}
    try:
        git["improvement_head"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        pass
    try:
        git["upstream_head"] = subprocess.run(["git", "-C", str(ROOT.parent / "Claims"), "log", "-1", "--format=%h %s (%cd)", "--date=short"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        pass
    return {"root": str(ROOT), "env": env, "processes": processes, "git": git, "network": NETWORK, "my_uid": MY_UID, "server_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# -------------------------------------------------------------------- handoff
@app.get("/api/handoff")
def handoff() -> Any:
    runs = list_runs()["runs"]
    agenda = get_agenda()
    open_items = [item for item in agenda.get("items", []) if item.get("status") != "done"]
    latest = runs[:5]
    lines = [
        "# Claims miner improvement: session handoff",
        f"Repo: {ROOT}  (live miner untouched at {ROOT.parent / 'Claims'})",
        "Run a paper: .venv/bin/python -m tools.run_paper --pdf papers/<id>.pdf --paper-id <id> --run-name <exp> --judge",
        "Evaluate: .venv/bin/python -m tools.evaluate runs/<exp>/<id> --judge",
        "Dashboard: ./run.sh  (http://localhost:8795)",
        "",
        "## Latest runs",
    ]
    for run in latest:
        lines.append(f"- {run['name']}: papers={run['paper_count']} completed={run['completed']} avg_claims={run['avg_claims']} avg_quality={run['avg_quality']} cost=${run['total_cost_usd']}")
    lines.append("")
    lines.append("## Open agenda items")
    for item in open_items:
        lines.append(f"- [{item.get('status', 'todo')}] {item.get('title')}: {item.get('detail', '')}")
    lines.append("")
    lines.append("## Loop")
    lines.append("1. Inspect evaluation.json findings and judge verdicts for the latest run.")
    lines.append("2. Edit miner/agent_v1/skills/claims_coverage (SKILL.md, prompts/*.md) or miner/agent_v1/staged/pipeline.py.")
    lines.append("3. Re-run the same paper under a new --run-name and compare in the dashboard (Compare view).")
    lines.append("4. When claims/quality improve without regressions, update agenda and deploy via scripts/run_mainnet_miner_pm2.sh.")
    return {"prompt": "\n".join(lines)}


# ------------------------------------------------------------------ frontend
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Any:
        candidate = DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST_DIR / "index.html")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8795)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
