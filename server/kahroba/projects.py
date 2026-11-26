"""Projects on the WSL filesystem: layout, settings and file access."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .config import PROJECTS

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")
SUBDIRS = ("src", "tb", "constraints", "runs")
HDL_EXT = {".v": "verilog", ".sv": "verilog", ".vh": "verilog", ".svh": "verilog",
           ".vhd": "vhdl", ".vhdl": "vhdl"}
DEFAULT = {
    "top": "",
    "sources": [],
    "clock": {"port": "clk", "period_ns": 10.0},
    "reset": {"port": "rst", "active_low": False},
    "target": {"pdk": "sky130A", "scl": "sky130_fd_sc_hd"},
    "synth": {"mode": "pdk", "flatten": True, "abc": "default"},
    "impl": {"core_util": 40, "density": 0.6, "die": None},
    "sim": {"simulator": "icarus", "test_module": "", "waves": True},
}


class ProjectError(ValueError):
    pass


def root(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise ProjectError("invalid project name")
    p = PROJECTS / name
    return p


def safe(name: str, rel: str) -> Path:
    r = root(name).resolve()
    p = (r / rel).resolve()
    if p != r and r not in p.parents:
        raise ProjectError("path escapes project")
    return p


def load(name: str) -> dict:
    f = root(name) / "kahroba.json"
    if not f.exists():
        raise ProjectError(f"no project {name}")
    cfg = json.loads(f.read_text())
    for k, v in DEFAULT.items():
        if isinstance(v, dict):
            cfg[k] = {**v, **cfg.get(k, {})}
        else:
            cfg.setdefault(k, v)
    cfg["name"] = name
    return cfg


def save(name: str, cfg: dict) -> dict:
    cfg = {k: v for k, v in cfg.items() if k != "name"}
    (root(name) / "kahroba.json").write_text(json.dumps(cfg, indent=2))
    return load(name)


def create(name: str, template: str | None = None) -> dict:
    r = root(name)
    if r.exists():
        raise ProjectError("project exists")
    if template:
        src = Path(__file__).resolve().parents[2] / "examples" / template
        if not src.is_dir():
            raise ProjectError(f"no example {template}")
        shutil.copytree(src, r, ignore=shutil.ignore_patterns("runs", "sim_build", "__pycache__"))
        for d in SUBDIRS:
            (r / d).mkdir(exist_ok=True)
        return load(name)
    for d in SUBDIRS:
        (r / d).mkdir(parents=True, exist_ok=True)
    save(name, dict(DEFAULT))
    return load(name)


def listing() -> list[dict]:
    PROJECTS.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(PROJECTS.iterdir()):
        if (p / "kahroba.json").exists():
            try:
                c = load(p.name)
                out.append({"name": p.name, "top": c["top"], "pdk": c["target"]["pdk"]})
            except Exception:  # noqa: BLE001
                out.append({"name": p.name, "broken": True})
    return out


def tree(name: str) -> list[dict]:
    r = root(name)
    out = []
    for p in sorted(r.rglob("*")):
        rel = p.relative_to(r).as_posix()
        if rel == "runs" or rel.startswith("runs/") or "/sim_build" in "/" + rel or "__pycache__" in rel \
                or rel.endswith(".pyc") or rel.startswith("."):
            continue
        out.append({"path": rel, "dir": p.is_dir(),
                    "size": p.stat().st_size if p.is_file() else 0})
    return out


def sources(cfg: dict) -> tuple[list[str], list[str]]:
    """Verilog and VHDL design sources (project-relative), in order."""
    srcs = cfg["sources"] or [p["path"] for p in tree(cfg["name"])
                              if p["path"].startswith("src/") and not p["dir"]]
    ver = [s for s in srcs if HDL_EXT.get(Path(s).suffix.lower()) == "verilog"]
    vhd = [s for s in srcs if HDL_EXT.get(Path(s).suffix.lower()) == "vhdl"]
    return ver, vhd


def delete_path(name: str, rel: str) -> None:
    p = safe(name, rel)
    if p == root(name).resolve():
        raise ProjectError("refusing to delete project root here")
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
