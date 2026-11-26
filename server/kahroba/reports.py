"""Parse tool outputs into small JSON results for the UI."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_WARN = re.compile(r"%(Warning|Error)(-[A-Z0-9_]+)?:\s*(\S+?):(\d+):(?:\d+:)?\s*(.*)")
_GHDL = re.compile(r"^(\S+?):(\d+):(\d+):\s*(?:(warning|error|note):)?\s*(.*)$")


def lint_result(lines: list[str]) -> dict:
    msgs = []
    for ln in lines:
        m = _WARN.search(ln)
        if m:
            msgs.append({"sev": m.group(1).lower(), "code": (m.group(2) or "")[1:],
                         "file": m.group(3), "line": int(m.group(4)), "msg": m.group(5)})
            continue
        m = _GHDL.match(ln)
        if m and m.group(1).endswith((".vhd", ".vhdl")):
            msgs.append({"sev": m.group(4) or "error", "code": "ghdl", "file": m.group(1),
                         "line": int(m.group(2)), "msg": m.group(5)})
    return {"messages": msgs,
            "errors": sum(m["sev"] == "error" for m in msgs),
            "warnings": sum(m["sev"] == "warning" for m in msgs)}


def _read(p: Path) -> str:
    return p.read_text(errors="replace") if p.exists() else ""


def _num(pat: str, text: str) -> float | None:
    m = re.search(pat, text, re.M)
    return float(m.group(1)) if m else None


def synth_result(out: Path, top: str, pdk: str | None = None) -> dict:
    r: dict = {"netlist": str(out / f"{top}.netlist.v"), "json": str(out / f"{top}.netlist.json")}
    try:
        raw = _read(out / "stat.json")
        st = json.loads(raw[raw.index("{"):] if "{" in raw else "{}")  # skip echoed command
        mods = st.get("modules") or {}
        d = st.get("design") or mods.get("\\" + top) or (next(iter(mods.values())) if mods else {})
        r["cells"] = d.get("num_cells")
        r["area_um2"] = d.get("area")
        r["cell_types"] = d.get("num_cells_by_type") or {}
    except json.JSONDecodeError:
        pass
    txt = _read(out / "stat.txt")
    r["stat_text"] = txt
    if r.get("cells") is None:
        r["cells"] = _num(r"Number of cells:\s+(\d+)", txt)
        r["area_um2"] = _num(r"Chip area for (?:top )?module.*?:\s+([\d.]+)", txt)
    tmax = _read(out / "timing_max.rpt")
    if tmax:
        r["timing_max"] = tmax
        r["timing_min"] = _read(out / "timing_min.rpt")
        r["wns_ns"] = _num(r"(-?[\d.]+)\s*$", _read(out / "wns.rpt"))
        r["tns_ns"] = _num(r"(-?[\d.]+)\s*$", _read(out / "tns.rpt"))
        r["slack_ns"] = _num(r"^\s*(-?[\d.]+)\s+slack", tmax)
        r["power_text"] = _read(out / "power.rpt")
        r["power_w"] = _num(r"^Total\s+\S+\s+\S+\s+\S+\s+(\S+)", r["power_text"])
        if pdk == "asap7":  # ps -> ns
            for k in ("wns_ns", "tns_ns", "slack_ns"):
                if r.get(k) is not None:
                    r[k] /= 1000.0
    return r


def sim_result(run: Path) -> dict:
    r: dict = {"tests": [], "passed": 0, "failed": 0}
    x = run / "results.xml"
    if x.exists():
        for tc in ET.parse(x).getroot().iter("testcase"):
            fail = tc.find("failure") is not None or tc.find("error") is not None
            props = {p.get("name"): p.get("value") for p in tc.iter("property")}
            st = tc.get("sim_time_ns")
            try:
                if st is None and "sim_time_stop" in props:
                    st = float(props["sim_time_stop"]) - float(props.get("sim_time_start", 0))
            except ValueError:
                st = None
            r["tests"].append({"name": tc.get("name"), "ok": not fail, "sim_time_ns": st,
                               "time_s": tc.get("time"), "unit": props.get("sim_time_unit", "ns")})
            r["failed" if fail else "passed"] += 1
    waves = sorted([p for p in (run / "sim_build").rglob("*")
                    if p.suffix in (".fst", ".vcd", ".ghw")], key=lambda p: p.stat().st_mtime)
    r["waves"] = str(waves[-1]) if waves else None
    return r


KEYS = {
    "design__instance__count__stdcell": "cells",
    "design__instance__area__stdcell": "cell_area_um2",
    "design__instance__count": "instances_total",
    "design__instance__count__class:sequential_cell": "flops",
    "design__instance__count__class:timing_repair_buffer": "repair_buffers",
    "design__instance__count__class:clock_buffer": "clock_buffers",
    "design__die__area": "die_area_um2",
    "design__core__area": "core_area_um2",
    "design__instance__utilization": "utilization",
    "timing__setup__ws": "setup_wns_ns",
    "timing__setup__tns": "setup_tns_ns",
    "timing__hold__ws": "hold_wns_ns",
    "timing__hold__tns": "hold_tns_ns",
    "clock__skew__worst_setup": "clock_skew_ns",
    "power__total": "power_w",
    "power__internal__total": "power_internal_w",
    "power__switching__total": "power_switching_w",
    "power__leakage__total": "power_leakage_w",
    "route__wirelength": "wirelength_um",
    "route__drc_errors": "route_drc",
    "magic__drc_error__count": "magic_drc",
    "klayout__drc_error__count": "klayout_drc",
    "design__lvs_error__count": "lvs_errors",
    "design__xor_difference__count": "xor_diff",
    "antenna__violating__nets": "antenna_nets",
    "design__max_slew_violation__count": "slew_viol",
    "design__max_cap_violation__count": "cap_viol",
    "ir__drop__worst": "ir_drop_worst_v",
}


def librelane_result(rd: Path, top: str, pdk: str) -> dict:
    m = json.loads(_read(rd / "final" / "metrics.json") or "{}")
    r: dict = {"flow": "librelane", "pdk": pdk, "run_dir": str(rd)}
    for k, v in KEYS.items():
        if k in m:
            r[v] = m[k]
    if "cells" not in r and "design__instance__count" in m:  # older metric names
        r["cells"], r["cell_area_um2"] = m["design__instance__count"], m.get("design__instance__area")
    # Fmax from the worst setup slack across corners
    try:
        per = float(json.loads(_read(rd / "resolved.json"))["CLOCK_PERIOD"])
        ws = r.get("setup_wns_ns")
        if ws is not None:
            r["clock_period_ns"] = per
            crit = per - float(ws)  # critical path delay ~ period - worst slack
            r["fmax_mhz"] = 1000.0 / crit if crit > 0 else None
    except Exception:  # noqa: BLE001
        pass
    gds = sorted((rd / "final" / "gds").glob("*.gds"))
    r["gds"] = str(gds[0]) if gds else None
    sta = sorted(rd.glob("*-openroad-stapostpnr"))
    if sta:
        r["sta_summary"] = _read(sta[-1] / "summary.rpt")
        worst = sorted(sta[-1].glob("*/max.rpt"))
        r["sta_corners"] = sorted(p.name for p in sta[-1].iterdir() if p.is_dir())
        nom = [p for p in worst if "nom_tt" in str(p)] or worst
        r["timing_max"] = _read(nom[0])[:60000] if nom else ""
    r["metrics"] = m
    r["steps"] = [p.name for p in sorted(rd.iterdir()) if p.is_dir() and p.name[:2].isdigit()] \
        if rd.exists() else []
    return r


def orfs_result(wh: Path, top: str, pdk: str) -> dict:
    base = f"{pdk}/{top}/base"
    r: dict = {"flow": "orfs", "pdk": pdk, "run_dir": str(wh)}
    rep = {}
    for f in sorted((wh / "logs" / base).glob("*.json")):
        try:
            rep.update(json.loads(_read(f)))
        except json.JSONDecodeError:
            pass
    for k, v in KEYS.items():
        for pref in ("finish__", "detailedroute__", "globalroute__", "cts__", "floorplan__"):
            if pref + k in rep and v not in r:
                r[v] = rep[pref + k]
    if pdk == "asap7":  # ASAP7 reports time in ps; Kahroba shows ns
        for k in ("setup_wns_ns", "setup_tns_ns", "hold_wns_ns", "hold_tns_ns", "clock_skew_ns"):
            if isinstance(r.get(k), (int, float)):
                r[k] = r[k] / 1000.0
    for pref in ("finish__", "detailedroute__", "globalroute__", "cts__"):
        if rep.get(pref + "timing__fmax"):
            r["fmax_mhz"] = float(rep[pref + "timing__fmax"]) / 1e6
            break
    gds = wh / "results" / base / "6_final.gds"
    r["gds"] = str(gds) if gds.exists() else None
    fin = _read(wh / "reports" / base / "6_finish.rpt")
    r["timing_max"] = fin[:60000]
    r["metrics"] = rep
    return r


def thermal_inputs(rd: Path, top: str) -> dict:
    """Files the thermal job needs from a finished implementation run."""
    if (rd / "final").exists():  # LibreLane
        res = json.loads(_read(rd / "resolved.json"))
        lib = res.get("LIB") or {}
        corner = res.get("DEFAULT_CORNER", "nom_tt_025C_1v80")
        libs = next((v for k, v in lib.items() if k.replace("*", "") in corner or k == corner), None) \
            or next(iter(lib.values()), [])
        spef = sorted((rd / "final" / "spef").rglob("*nom*.spef")) or sorted((rd / "final" / "spef").rglob("*.spef"))
        return {"odb": str(next((rd / "final" / "odb").glob("*.odb"))),
                "sdc": str(next((rd / "final" / "sdc").glob("*.sdc"))),
                "spef": str(spef[0]) if spef else None, "libs": libs}
    # ORFS work home
    res = next(rd.glob("results/*/*/base"))
    plat = res.parts[-3]
    from .config import ORFS
    pl = ORFS / "flow" / "platforms" / plat
    libs = [str(pl / "lib" / "NangateOpenCellLibrary_typical.lib")] if plat == "nangate45" \
        else [str(x) for x in sorted((pl / "lib" / "NLDM").glob("*RVT_TT*.lib*"))]
    spef = res / "6_final.spef"
    return {"odb": str(res / "6_final.odb"), "sdc": str(res / "6_final.sdc"),
            "spef": str(spef) if spef.exists() else None, "libs": libs}
