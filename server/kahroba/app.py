"""Kahroba HTTP API (FastAPI). Listens on 127.0.0.1 inside WSL; the Windows
app reaches it through WSL localhost forwarding."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from . import flows, hdl, jobs, pdks, projects
from .config import CIEL, ORFS, PROJECTS, VERSION, tool_env, which

app = FastAPI(title="Kahroba", version=VERSION)

# Access token: a fresh random secret per server start, readable only by the
# WSL user (~/.kahroba/token, mode 600). The desktop app reads it through
# wsl.exe; a web page in a Windows browser cannot, so it cannot drive the API.
# KAHROBA_DEV=1 disables the check (browser UI at /ui for development).
import os  # noqa: E402
import secrets  # noqa: E402

from .config import KDIR  # noqa: E402

TOKEN = secrets.token_urlsafe(32)
_tf = KDIR / "token"
_tf.parent.mkdir(parents=True, exist_ok=True)
_tf.write_text(TOKEN)
_tf.chmod(0o600)
DEV = os.environ.get("KAHROBA_DEV") == "1"


@app.middleware("http")
async def _auth(request: Request, call_next):
    p = request.url.path
    if (p.startswith("/api/") and p != "/api/health" and request.method != "OPTIONS" and not DEV):
        t = request.headers.get("x-kahroba-token") or request.query_params.get("t")
        if not t or not secrets.compare_digest(t, TOKEN):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "missing or wrong access token"}, status_code=401)
    return await call_next(request)


# Electron pages are file:// (Origin "null"); the Surfer frame is same-origin.
app.add_middleware(CORSMiddleware, allow_origins=["null"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(projects.ProjectError)
async def _perr(_: Request, e: projects.ProjectError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(e)}, status_code=400)


def _ver(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, env=tool_env(), capture_output=True, text=True, timeout=20)
        s = (out.stdout or out.stderr).strip().splitlines()
        return s[0][:120] if s else None
    except Exception:  # noqa: BLE001
        return None


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


@app.get("/api/tools")
def tools():
    t = {
        "yosys": ["yosys", "-V"], "openroad": ["openroad", "-version"], "sta": ["sta", "-version"],
        "librelane": ["librelane", "--version"], "magic": ["magic", "--version"],
        "netgen": ["netgen", "-batch", "quit"], "klayout": ["klayout", "-v"],
        "verilator": ["verilator", "--version"], "iverilog": ["iverilog", "-V"],
        "ghdl": ["ghdl", "--version"], "ciel": ["ciel", "--version"],
        "cocotb": ["cocotb-config", "--version"],
    }
    out = {k: {"path": which(v[0]), "version": _ver(v) if which(v[0]) else None} for k, v in t.items()}
    out["yosys-ghdl"] = {"path": None, "version": "ok" if _ver(["yosys", "-m", "ghdl", "-p", "help ghdl"]) else None}
    return {"tools": out, "pdk_root": str(CIEL), "orfs": str(ORFS), "projects": str(PROJECTS)}


# ---------------------------------------------------------------- projects
@app.get("/api/projects")
def list_projects():
    return projects.listing()


@app.post("/api/projects")
def new_project(body: dict = Body(...)):
    return projects.create(body["name"], body.get("template"))


@app.get("/api/examples")
def examples():
    root = Path(__file__).resolve().parents[2] / "examples"
    out = []
    for d in sorted(root.glob("*/kahroba.json")):
        c = json.loads(d.read_text())
        out.append({"id": d.parent.name, "top": c.get("top"), "about": c.get("about", "")})
    return out


@app.get("/api/projects/{name}")
def get_project(name: str):
    return {"config": projects.load(name), "tree": projects.tree(name)}


@app.put("/api/projects/{name}")
def put_project(name: str, cfg: dict = Body(...)):
    return projects.save(name, cfg)


@app.get("/api/projects/{name}/file")
def read_file(name: str, path: str):
    p = projects.safe(name, path)
    if not p.is_file():
        raise HTTPException(404, "no such file")
    if p.stat().st_size > 8_000_000:
        raise HTTPException(413, "file too large for the editor")
    return PlainTextResponse(p.read_text(errors="replace"))


@app.put("/api/projects/{name}/file")
def write_file(name: str, path: str, body: str = Body(..., media_type="text/plain")):
    p = projects.safe(name, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return {"ok": True, "size": len(body)}


@app.post("/api/projects/{name}/mkdir")
def mkdir(name: str, body: dict = Body(...)):
    projects.safe(name, body["path"]).mkdir(parents=True, exist_ok=True)
    return {"ok": True}


@app.post("/api/projects/{name}/rename")
def rename(name: str, body: dict = Body(...)):
    a, b = projects.safe(name, body["src"]), projects.safe(name, body["dst"])
    b.parent.mkdir(parents=True, exist_ok=True)
    a.rename(b)
    return {"ok": True}


@app.delete("/api/projects/{name}/file")
def delete_file(name: str, path: str):
    projects.delete_path(name, path)
    return {"ok": True}


@app.get("/api/projects/{name}/raw")
def raw(name: str, path: str):
    p = projects.safe(name, path)
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/file")
def any_file(path: str):
    """Serve a run artifact (GDS png, waves, svg) by absolute path, only inside
    the projects folder."""
    p = Path(path).resolve()
    if PROJECTS.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/projects/{name}/units")
def units(name: str):
    cfg = projects.load(name)
    ver, vhd = projects.sources(cfg)
    out = []
    for rel in ver + vhd:
        t = projects.safe(name, rel).read_text(errors="replace")
        for u in hdl.units(t, rel in vhd):
            out.append({**u, "file": rel, "lang": "vhdl" if rel in vhd else "verilog"})
    return out


@app.post("/api/projects/{name}/new-file")
def new_file(name: str, body: dict = Body(...)):
    kind, base = body["kind"], body["name"]
    if not projects.NAME_RE.match(base):
        raise HTTPException(400, "invalid name")
    cfg = projects.load(name)
    if kind == "verilog":
        rel, text = f"src/{base}.v", hdl.verilog_template(base)
    elif kind == "vhdl":
        rel, text = f"src/{base}.vhd", hdl.vhdl_template(base)
    elif kind == "sdc":
        rel = f"constraints/{base}.sdc"
        clk, per = cfg["clock"]["port"], cfg["clock"]["period_ns"]
        text = f"create_clock -name {clk} -period {per} [get_ports {clk}]\n" \
               f"set_input_delay  {float(per) * 0.2:g} -clock {clk} [delete_from_list [all_inputs] [get_ports {clk}]]\n" \
               f"set_output_delay {float(per) * 0.2:g} -clock {clk} [all_outputs]\n"
    elif kind == "cocotb":
        top = body.get("top") or cfg["top"]
        u = next((x for x in units(name) if x["name"] == top), None)
        if not u:
            raise HTTPException(400, f"top unit {top} not found in sources")
        rel = f"tb/{base}.py"
        text = hdl.cocotb_template(top, u["ports"], cfg["clock"]["port"], cfg["reset"]["port"],
                                   cfg["reset"]["active_low"])
    else:
        raise HTTPException(400, "unknown kind")
    p = projects.safe(name, rel)
    if p.exists():
        raise HTTPException(409, f"{rel} exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return {"path": rel}


# ---------------------------------------------------------------- flows
RUNNERS = {"lint": flows.lint, "rtl": flows.elaborate, "synth": flows.synth,
           "sim": flows.simulate, "impl": flows.implement}


@app.post("/api/projects/{name}/run/{kind}")
def run(name: str, kind: str, body: dict = Body(default={})):
    cfg = projects.load(name)
    if kind in RUNNERS:
        f = RUNNERS[kind]
        j = f(cfg, body) if kind in ("synth", "sim", "impl") else f(cfg)
    elif kind == "thermal":
        j = flows.thermal(cfg, body["impl_run"], body)
    elif kind == "layout":
        out = Path(body["gds"]).with_suffix(".png")
        j = flows.render_layout(cfg, body["gds"], body.get("pdk", cfg["target"]["pdk"]), out,
                                int(body.get("size", 1600)))
    else:
        raise HTTPException(404, "unknown step")
    return jobs.submit(j).info()


@app.post("/api/projects/{name}/klayout")
def klayout(name: str, body: dict = Body(...)):
    projects.load(name)
    subprocess.Popen(flows.open_klayout(body["gds"], body.get("pdk", "sky130A")), env=tool_env(),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"ok": True}


@app.get("/api/jobs")
def list_jobs(project: str | None = None):
    return jobs.all_jobs(project)


@app.get("/api/projects/{name}/history")
def history(name: str):
    """Finished runs kept on disk (survive restarts)."""
    out = []
    for f in sorted((projects.root(name) / "runs").glob("*/job.json"), reverse=True)[:200]:
        try:
            out.append(jobs.clean(json.loads(f.read_text())))
        except json.JSONDecodeError:
            pass
    return out


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404)
    return j.info()


@app.delete("/api/jobs/{jid}")
def cancel(jid: str):
    return {"cancelled": jobs.cancel(jid)}


@app.get("/api/jobs/{jid}/log")
async def log_stream(jid: str, start: int = 0):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404)

    async def gen():
        i = start
        last_state = None
        while True:
            while i < len(j.lines):
                yield f"data: {json.dumps({'i': i, 'line': j.lines[i]})}\n\n"
                i += 1
            state = (j.status, j.current, len(j.done_stages))
            if state != last_state:
                yield f"event: state\ndata: {json.dumps(j.info())}\n\n"
                last_state = state
            if j.status in ("ok", "failed", "cancelled") and i >= len(j.lines) and j.ended:
                yield f"event: end\ndata: {json.dumps(j.info())}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- PDKs
@app.get("/api/pdks")
def pdk_list():
    return pdks.catalog()


@app.post("/api/pdks/{pid}/install")
def pdk_install(pid: str):
    try:
        return jobs.submit(flows.pdk_install(pid)).info()
    except KeyError:
        raise HTTPException(404, "unknown PDK") from None


from fastapi.staticfiles import StaticFiles  # noqa: E402

_APPDIR = Path(__file__).resolve().parents[2]
if (_APPDIR / "surfer-web").is_dir():
    app.mount("/surfer", StaticFiles(directory=_APPDIR / "surfer-web", html=True), name="surfer")
if (_APPDIR / "ui").is_dir():  # browser mode: same UI without Electron
    app.mount("/ui", StaticFiles(directory=_APPDIR / "ui", html=True), name="ui")


def main():
    import uvicorn
    from .config import HOST, PORT
    PROJECTS.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
