"""Paths and tool environment for the Kahroba backend (runs inside WSL)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

HOME = Path.home()
KDIR = Path(os.environ.get("KAHROBA_HOME", HOME / ".kahroba"))
PROJECTS = Path(os.environ.get("KAHROBA_PROJECTS", HOME / "projects"))
ORFS = Path(os.environ.get("KAHROBA_ORFS", KDIR / "orfs"))
CIEL = Path(os.environ.get("PDK_ROOT", HOME / ".ciel"))
VENV = KDIR / "venv"
SERVER_ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("KAHROBA_HOST", "127.0.0.1")
PORT = int(os.environ.get("KAHROBA_PORT", "8770"))
VERSION = "1.1.0"


@lru_cache(maxsize=1)
def tool_env() -> dict[str, str]:
    """Environment of the LibreLane Nix devshell (captured at install time),
    with the Kahroba venv put first so cocotb and our Python win."""
    env = dict(os.environ)
    f = KDIR / "ll.env"
    if f.exists():
        for item in f.read_bytes().split(b"\0"):
            if b"=" not in item:
                continue
            k, v = item.split(b"=", 1)
            k = k.decode(errors="replace")
            if k in ("PWD", "OLDPWD", "SHLVL", "_", "HOME", "USER", "TERM"):
                continue
            env[k] = v.decode(errors="replace")
    env["PATH"] = f"{VENV}/bin:{env.get('PATH', '')}:/usr/local/bin:/usr/bin:/bin"
    env["PDK_ROOT"] = str(CIEL)
    xdg = KDIR / "xdg"
    xdg.mkdir(mode=0o700, parents=True, exist_ok=True)
    env["XDG_RUNTIME_DIR"] = str(xdg)  # Qt (OpenROAD GUI reports, KLayout) needs it
    env.setdefault("LANG", "C.UTF-8")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def which(tool: str) -> str | None:
    for d in tool_env()["PATH"].split(":"):
        p = Path(d) / tool
        if d and p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None
