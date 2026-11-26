"""PDK catalog: which technologies exist, which can tape out, which are installed."""
from __future__ import annotations

from pathlib import Path

from .config import CIEL, ORFS

# OpenROAD-flow-scripts commit from the same week as the OpenROAD build that
# ships in the LibreLane 3.0.17 toolchain (2026-02-17)
ORFS_REV = "742100744ac0a7c875b671ace2d49822f15fc517"

# flow: librelane (open PDK through ciel) or orfs (OpenROAD-flow-scripts platform)
CATALOG = [
    {"id": "sky130A", "family": "sky130", "node": "130 nm", "vendor": "SkyWater",
     "tapeout": True, "flow": "librelane",
     "scls": ["sky130_fd_sc_hd", "sky130_fd_sc_hs", "sky130_fd_sc_ms", "sky130_fd_sc_ls", "sky130_fd_sc_hdll"],
     "note": "Open, manufacturable PDK (shuttles: ChipFoundry / Tiny Tapeout)."},
    {"id": "gf180mcuD", "family": "gf180mcu", "node": "180 nm", "vendor": "GlobalFoundries",
     "tapeout": True, "flow": "librelane",
     "scls": ["gf180mcu_fd_sc_mcu7t5v0", "gf180mcu_fd_sc_mcu9t5v0"],
     "note": "Open, manufacturable PDK."},
    {"id": "ihp-sg13g2", "family": "ihp-sg13g2", "node": "130 nm SiGe", "vendor": "IHP",
     "tapeout": True, "flow": "librelane", "scls": ["sg13g2_stdcell"],
     "note": "Open, manufacturable BiCMOS PDK."},
    {"id": "nangate45", "family": "nangate45", "node": "45 nm", "vendor": "FreePDK45 / Nangate",
     "tapeout": False, "flow": "orfs", "scls": ["nangate45"],
     "note": "Academic library, no fab. Good for research numbers."},
    {"id": "asap7", "family": "asap7", "node": "7 nm (predictive)", "vendor": "ASU/ARM ASAP7",
     "tapeout": False, "flow": "orfs", "scls": ["asap7sc7p5t"],
     "note": "Predictive FinFET PDK, no fab. Numbers are estimates."},
]


def _installed(p: dict) -> bool:
    if p["flow"] == "orfs":
        return (ORFS / "flow" / "platforms" / p["id"] / "config.mk").exists()
    return (CIEL / p["id"] / "libs.ref").is_dir()


def catalog() -> list[dict]:
    return [{**p, "installed": _installed(p)} for p in CATALOG]


def get(pid: str) -> dict:
    for p in CATALOG:
        if p["id"] == pid:
            return {**p, "installed": _installed(p)}
    raise KeyError(pid)


def install_script(pid: str) -> str:
    p = get(pid)
    if p["flow"] == "librelane":
        return f"""set -euo pipefail
HF=$(ls /nix/store/*-librelane-*/lib/python3*/site-packages/librelane/pdk_hashes.yaml 2>/dev/null | head -1)
REV=$(grep '^{p['family']}:' "$HF" | awk '{{print $2}}')
echo "[kahroba] stage: download {p['family']} (open_pdks $REV)"
ciel enable --pdk-root "$PDK_ROOT" --pdk-family {p['family']} "$REV"
echo "[kahroba] stage: verify"
ls "$PDK_ROOT/{pid}/libs.ref"
"""
    plats = sorted({q["id"] for q in CATALOG if q["flow"] == "orfs" and (q["id"] == pid or _installed(q))})
    pats = " ".join(f"'/flow/platforms/{x}/'" for x in plats)
    # ORFS revision matched to the OpenROAD build in the LibreLane toolchain
    return f"""set -euo pipefail
O="{ORFS}"
REV={ORFS_REV}
echo "[kahroba] stage: download {pid} (OpenROAD-flow-scripts $REV)"
if [ ! -d "$O/.git" ]; then
  mkdir -p "$O" && cd "$O" && git init -q
  git remote add origin https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts
  git config core.sparseCheckout true
fi
cd "$O"
git sparse-checkout set --no-cone '/flow/Makefile' '/flow/scripts/' '/flow/util/' '/flow/platforms/common/' {pats}
for i in 1 2 3 4 5; do git fetch --depth 1 --filter=blob:none origin "$REV" && break; sleep 10; done
git checkout -q FETCH_HEAD
# this OpenROAD build has no sta::endpoint_count (used only for an info line)
sed -i 's|^\\(utl::info "FLW" 11 .*\\)$|catch {{ \\1 }}|' flow/scripts/write_ref_sdc.tcl
echo "[kahroba] stage: verify"
ls flow/platforms/{pid}/config.mk
"""


def liberty(pid: str, scl: str | None = None) -> list[str]:
    """Typical-corner liberty files for Yosys-only synthesis."""
    p = get(pid)
    scl = scl or p["scls"][0]
    if p["flow"] == "orfs":
        base = ORFS / "flow" / "platforms" / pid
        if pid == "nangate45":
            return [str(base / "lib" / "NangateOpenCellLibrary_typical.lib")]
        libs = sorted((base / "lib" / "NLDM").glob("*RVT_TT*.lib*"))
        return [_plain(x) for x in libs]
    libdir = CIEL / pid / "libs.ref" / scl / "lib"
    pref = {"sky130A": "tt_025C_1v80", "gf180mcuD": "tt_025C_5v00", "ihp-sg13g2": "typ_1p20V_25C"}.get(pid, "tt")
    cand = sorted(libdir.glob(f"*{pref}*.lib")) or sorted(libdir.glob("*.lib"))
    return [str(cand[0])] if cand else []


def _plain(p: Path) -> str:
    """Yosys reads plain liberty only: keep an uncompressed copy of *.lib.gz."""
    if p.suffix != ".gz":
        return str(p)
    import gzip
    import shutil
    from .config import KDIR
    out = KDIR / "libcache" / p.name[:-3]
    if not out.exists() or out.stat().st_mtime < p.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "rb") as fi, open(out, "wb") as fo:
            shutil.copyfileobj(fi, fo)
    return str(out)


def layer_props(pid: str) -> str | None:
    if get(pid)["flow"] == "orfs":
        f = sorted((ORFS / "flow" / "platforms" / pid).rglob("*.lyp"))
    else:
        f = sorted((CIEL / pid / "libs.tech" / "klayout").rglob("*.lyp"))
    return str(f[0]) if f else None


def exists(path: str) -> bool:
    return Path(path).exists()
