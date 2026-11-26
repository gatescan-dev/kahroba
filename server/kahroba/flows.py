"""Flow steps: each returns a jobs.Job with a bash script, a stage detector
and a result parser. All paths are absolute inside WSL."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from . import jobs, pdks, projects, reports
from .config import ORFS, SERVER_ROOT, VENV

q = shlex.quote
MARK = re.compile(r"^\[kahroba\] stage: (.+)$")


def _marker_stage(line: str) -> str | None:
    m = MARK.match(line)
    return m.group(1).strip() if m else None


def _job(cfg: dict, kind: str, title: str, script: str, stages: list[str],
         stage_of=None, finish=None) -> jobs.Job:
    jid = jobs.new_id(kind)
    root = projects.root(cfg["name"])
    run = root / "runs" / jid
    return jobs.Job(id=jid, project=cfg["name"], kind=kind, title=title,
                    script="cd " + q(str(root)) + "\n" + script, cwd=root, run_dir=run,
                    stages=stages, stage_of=stage_of or _marker_stage, finish=finish)


def _abs(cfg: dict, rels: list[str]) -> list[str]:
    r = projects.root(cfg["name"])
    return [str(r / s) for s in rels]


def _need_top(cfg: dict) -> str:
    if not cfg.get("top"):
        raise projects.ProjectError("set the top module first (Project settings)")
    return cfg["top"]


def _yosys_read(cfg: dict) -> tuple[str, str]:
    """(yosys flags, read commands) for the project's sources."""
    ver, vhd = projects.sources(cfg)
    ver, vhd = _abs(cfg, ver), _abs(cfg, vhd)
    cmds, flags = [], ""
    if ver:
        cmds.append("read_verilog -sv " + " ".join(q(f) for f in ver))
    if vhd:
        flags = "-m ghdl"
        cmds.append("ghdl --std=08 -fsynopsys " + " ".join(q(f) for f in vhd) + " -e " + cfg["top"])
    if not cmds:
        raise projects.ProjectError("no HDL sources in src/")
    return flags, "\n".join(cmds)


# ---------------------------------------------------------------- lint
def lint(cfg: dict) -> jobs.Job:
    top = _need_top(cfg)
    ver, vhd = projects.sources(cfg)
    s = ["echo '[kahroba] stage: Lint'"]
    if ver:
        s.append("verilator --lint-only -Wall -Wno-fatal -Wno-DECLFILENAME --top-module "
                 + q(top) + " " + " ".join(q(f) for f in _abs(cfg, ver)))
    if vhd:
        s.append("mkdir -p $RUN/ghdl && ghdl -a --std=08 -fsynopsys --workdir=$RUN/ghdl "
                 + " ".join(q(f) for f in _abs(cfg, vhd))
                 + " && ghdl -e --std=08 -fsynopsys --workdir=$RUN/ghdl " + q(top))
    j = _job(cfg, "lint", "Lint", "", ["Lint"])
    j.script += f"RUN={q(str(j.run_dir))}\nset -o pipefail\n" + "\n".join(s) + "\n"
    j.finish = lambda job: reports.lint_result(job.lines)
    return j


# ---------------------------------------------------------------- RTL schematic
def elaborate(cfg: dict) -> jobs.Job:
    top = _need_top(cfg)
    flags, read = _yosys_read(cfg)
    j = _job(cfg, "rtl", "RTL elaboration", "", ["Elaborate", "Schematic"])
    out = j.run_dir
    ys = f"""{read}
hierarchy -check -top {top}
proc; opt_clean
tee -o {out}/rtl_stat.txt stat
write_json {out}/rtl.json
"""
    j.script += f"""set -euo pipefail
mkdir -p {q(str(out))}
cat > {q(str(out))}/rtl.ys <<'EOF'
{ys}EOF
echo '[kahroba] stage: Elaborate'
yosys {flags} -q -l {q(str(out))}/yosys.log {q(str(out))}/rtl.ys
echo '[kahroba] stage: Schematic'
yosys {flags} -q -p "{read.replace('"', '\\"')}; hierarchy -top {top}; proc; opt_clean; show -format svg -prefix {out}/rtl_dot -width -stretch {top}" >/dev/null 2>&1 || true
"""
    j.finish = lambda job: {"json": str(out / "rtl.json"),
                            "svg": str(out / "rtl_dot.svg") if (out / "rtl_dot.svg").exists() else None,
                            "stat": (out / "rtl_stat.txt").read_text() if (out / "rtl_stat.txt").exists() else ""}
    return j


# ---------------------------------------------------------------- synthesis
def synth(cfg: dict, opts: dict | None = None) -> jobs.Job:
    top = _need_top(cfg)
    o = {**cfg["synth"], **(opts or {})}
    flags, read = _yosys_read(cfg)
    pdk = cfg["target"]["pdk"]
    j = _job(cfg, "synth", f"Synthesis ({'generic' if o['mode'] == 'generic' else pdk})", "",
             ["Read RTL", "Synthesize", "Map to cells", "Timing", "Reports"])
    out = j.run_dir
    flat = "-flatten" if o.get("flatten", True) else ""
    if o["mode"] == "generic":
        body = f"synth -top {top} {flat}\nopt_clean -purge\n"
        stat = "stat"
        libs: list[str] = []
    else:
        libs = pdks.liberty(pdk, cfg["target"].get("scl"))
        if not libs:
            raise projects.ProjectError(f"PDK {pdk} is not installed (PDK manager)")
        lib = " -liberty ".join(libs)  # all liberty files (ASAP7 is split by cell class)
        abc = {"default": "", "speed": "-D " + str(int(float(cfg['clock']['period_ns']) * 1000)),
               "area": ""}.get(o.get("abc", "default"), "")
        du = " ".join(f"-dont_use {c}" for c in _dont_use(pdk))
        body = (f"synth -top {top} {flat}\n"
                f"dfflibmap -liberty {lib} {du}\n"
                f"abc -liberty {lib} {du} {abc}\n"
                "setundef -zero\nhilomap -singleton -hicell {hi} -locell {lo}\n".format(
                    **_tiecells(pdk))
                + "splitnets\nopt_clean -purge\n")
        stat = f"stat -liberty {lib}"
    # cell port directions for the schematic viewer (netlistsvg)
    schem_libs = "".join(f"read_liberty -lib -ignore_miss_dir {l}\n" for l in libs)
    ys = f"""{read}
hierarchy -check -top {top}
echo on
{body}check
tee -o {out}/stat.txt {stat}
tee -q -o {out}/stat.json {stat} -json
write_verilog -noattr {out}/{top}.netlist.v
{schem_libs}write_json {out}/{top}.netlist.json
"""
    j.script += f"""set -euo pipefail
mkdir -p {q(str(out))}
cat > {q(str(out))}/synth.ys <<'EOF'
{ys}EOF
echo '[kahroba] stage: Read RTL'
yosys {flags} -l {q(str(out))}/yosys.log {q(str(out))}/synth.ys | sed -u -e 's/^.*Executing SYNTH pass.*$/[kahroba] stage: Synthesize\\n&/' -e 's/^.*Executing DFFLIBMAP.*$/[kahroba] stage: Map to cells\\n&/'
"""
    if libs:
        sdc = _sdc(cfg, out)
        j.script += f"""echo '[kahroba] stage: Timing'
cat > {q(str(out))}/sta.tcl <<'EOF'
{''.join(f'read_liberty {l}' + chr(10) for l in libs)}read_verilog {out}/{top}.netlist.v
link_design {top}
read_sdc {sdc}
report_checks -path_delay max -digits 3 -group_path_count 1 > {out}/timing_max.rpt
report_checks -path_delay min -digits 3 > {out}/timing_min.rpt
report_tns > {out}/tns.rpt
report_wns > {out}/wns.rpt
set_power_activity -input -activity 0.1
report_power > {out}/power.rpt
exit
EOF
sta -no_splash -exit {q(str(out))}/sta.tcl || echo '[kahroba] warning: post-synthesis STA failed'
"""
    j.script += "echo '[kahroba] stage: Reports'\n"
    j.finish = lambda job: reports.synth_result(out, top, pdk if libs else None)
    return j


def _dont_use(pdk: str) -> list[str]:
    """Cells the logic mapper must not pick (same idea as LibreLane's DONT_USE_CELLS)."""
    return {
        "sky130A": ["*lpflow*", "*probe*", "*clkbuf_16*", "*diode*", "*fill*", "*tap*", "*decap*", "*conb*"],
        "gf180mcuD": ["*fill*", "*tap*", "*decap*", "*antenna*"],
        "ihp-sg13g2": ["*fill*", "*decap*", "*antenna*", "*sighold*"],
        "nangate45": ["FILLCELL*", "ANTENNA*", "TAPCELL*"],
        "asap7": ["*FILLER*", "*TAP*", "*DECAP*"],
    }.get(pdk, [])


def _tiecells(pdk: str) -> dict:
    return {
        "sky130A": {"hi": "sky130_fd_sc_hd__conb_1 HI", "lo": "sky130_fd_sc_hd__conb_1 LO"},
        "gf180mcuD": {"hi": "gf180mcu_fd_sc_mcu7t5v0__tieh Z", "lo": "gf180mcu_fd_sc_mcu7t5v0__tiel ZN"},
        "ihp-sg13g2": {"hi": "sg13g2_tiehi L_HI", "lo": "sg13g2_tielo L_LO"},
        "nangate45": {"hi": "LOGIC1_X1 Z", "lo": "LOGIC0_X1 Z"},
        "asap7": {"hi": "TIEHIx1_ASAP7_75t_R H", "lo": "TIELOx1_ASAP7_75t_R L"},
    }[pdk]


def _sdc(cfg: dict, out: Path) -> str:
    """Use constraints/*.sdc if the user wrote one, else a default one."""
    r = projects.root(cfg["name"])
    user = sorted((r / "constraints").glob("*.sdc"))
    if user:
        return str(user[0])
    clk, per = cfg["clock"]["port"], float(cfg["clock"]["period_ns"])
    if cfg["target"]["pdk"] == "asap7":
        per *= 1000.0  # ASAP7 liberty time unit is ps; Kahroba settings are in ns
    f = out / "default.sdc"
    out.mkdir(parents=True, exist_ok=True)
    f.write_text(f"""# default constraints written by Kahroba
if {{[llength [get_ports -quiet {clk}]] > 0}} {{
  create_clock -name {clk} -period {per} [get_ports {clk}]
  set_input_delay  [expr {per} * 0.2] -clock {clk} [delete_from_list [all_inputs] [get_ports {clk}]]
  set_output_delay [expr {per} * 0.2] -clock {clk} [all_outputs]
}} else {{
  create_clock -name vclk -period {per}
  set_input_delay  0 -clock vclk [all_inputs]
  set_output_delay 0 -clock vclk [all_outputs]
}}
set_load 0.01 [all_outputs]
""")
    return str(f)


# ---------------------------------------------------------------- simulation
def simulate(cfg: dict, opts: dict | None = None) -> jobs.Job:
    top = _need_top(cfg)
    o = {**cfg["sim"], **(opts or {})}
    ver, vhd = projects.sources(cfg)
    simname = o["simulator"]
    mod = o.get("test_module") or f"test_{top}"
    r = projects.root(cfg["name"])
    if not (r / "tb" / f"{mod}.py").exists():
        raise projects.ProjectError(f"testbench tb/{mod}.py not found (use 'New cocotb testbench')")
    if simname == "ghdl" and ver:
        raise projects.ProjectError("GHDL simulates VHDL only; choose Icarus or Verilator for Verilog")
    if simname in ("icarus", "verilator") and vhd:
        raise projects.ProjectError(f"{simname} simulates Verilog only; choose GHDL for VHDL")
    j = _job(cfg, "sim", f"Simulation ({simname}, cocotb)", "", ["Build", "Run tests", "Waves"])
    spec = {"sim": simname, "top": top, "module": mod, "tb_dir": str(r / "tb"),
            "verilog": _abs(cfg, ver), "vhdl": _abs(cfg, vhd), "build_dir": str(j.run_dir / "sim_build"),
            "waves": bool(o.get("waves", True))}
    j.script += f"""set -euo pipefail
mkdir -p {q(str(j.run_dir))}
cat > {q(str(j.run_dir))}/sim.json <<'EOF'
{json.dumps(spec)}
EOF
{VENV}/bin/python {SERVER_ROOT}/sim_run.py {q(str(j.run_dir))}/sim.json
"""
    j.finish = lambda job: reports.sim_result(job.run_dir)
    return j


# ---------------------------------------------------------------- implementation
LL_GROUPS = [
    ("Synthesis", ("yosys.", "verilator.lint", "checker.yosys", "checker.lint")),
    ("Floorplan", ("openroad.floorplan", "odb.", "openroad.tapendcap", "openroad.ioplacement",
                   "openroad.generatepdn", "openroad.globalplacementskipio", "checker.pdn")),
    ("Placement", ("openroad.globalplacement", "openroad.repairdesign", "openroad.detailedplacement",
                   "openroad.resizer")),
    ("Clock tree", ("openroad.cts",)),
    ("Routing", ("openroad.globalrouting", "openroad.detailedrouting", "openroad.repairantennas",
                 "openroad.fillinsertion", "openroad.rcx", "checker.trdrc", "checker.wirelength")),
    ("Sign-off STA", ("openroad.stapostpnr", "openroad.irdropreport")),
    ("GDS", ("magic.streamout", "klayout.streamout", "klayout.xor", "checker.xor")),
    ("DRC", ("magic.drc", "klayout.drc", "checker.magicdrc", "checker.klayoutdrc")),
    ("LVS", ("magic.spiceextraction", "netgen.lvs", "checker.lvs", "checker.illegaloverlap")),
]
_LL_STEP = re.compile(r"(?:Running|Starting)\s+'?([A-Za-z]+\.[A-Za-z0-9]+)'?", re.I)
_ORFS_STEP = re.compile(r"\b(1_\d?_?yosys|1_synth|2_\d_floorplan\w*|3_\d_place\w*|4_\d_cts|5_\d_\w*route\w*|5_\d_fillcell|6_\w*(?:final|report|merge)\w*)")


def _ll_stage(line: str) -> str | None:
    s = _marker_stage(line)
    if s:
        return s
    m = _LL_STEP.search(line)
    if not m:
        return None
    sid = m.group(1).lower()
    for name, keys in LL_GROUPS:
        if any(sid.startswith(k) for k in keys):
            return name
    return None


def _orfs_stage(line: str) -> str | None:
    s = _marker_stage(line)
    if s:
        return s
    m = _ORFS_STEP.search(line)
    if not m:
        return None
    t = m.group(1)
    return {"1": "Synthesis", "2": "Floorplan", "3": "Placement", "4": "Clock tree",
            "5": "Routing", "6": "GDS"}[t[0]]


def implement(cfg: dict, opts: dict | None = None) -> jobs.Job:
    top = _need_top(cfg)
    pdk = cfg["target"]["pdk"]
    p = pdks.get(pdk)
    if not p["installed"]:
        raise projects.ProjectError(f"PDK {pdk} is not installed (PDK manager)")
    ver, vhd = projects.sources(cfg)
    im = {**cfg["impl"], **(opts or {})}
    if p["flow"] == "librelane":
        return _impl_librelane(cfg, top, pdk, im, _abs(cfg, ver), _abs(cfg, vhd))
    if vhd:
        raise projects.ProjectError("45/7 nm flow (ORFS) takes Verilog; synthesize VHDL first")
    return _impl_orfs(cfg, top, pdk, im, _abs(cfg, ver))


def _impl_librelane(cfg, top, pdk, im, ver, vhd) -> jobs.Job:
    stages = [g for g, _ in LL_GROUPS]
    j = _job(cfg, "impl", f"Implementation (LibreLane, {pdk})", "", stages, stage_of=_ll_stage)
    conf = {
        "DESIGN_NAME": top,
        "CLOCK_PORT": cfg["clock"]["port"],
        "CLOCK_PERIOD": float(cfg["clock"]["period_ns"]),
        "FP_CORE_UTIL": int(im.get("core_util", 40)),
        "PL_TARGET_DENSITY_PCT": int(float(im.get("density", 0.6)) * 100),
    }
    if ver:
        conf["VERILOG_FILES"] = ver
    if vhd:
        conf["VHDL_FILES"] = vhd
    r = projects.root(cfg["name"])
    user_sdc = sorted((r / "constraints").glob("*.sdc"))
    if user_sdc:
        conf["PNR_SDC_FILE"] = str(user_sdc[0])
        conf["SIGNOFF_SDC_FILE"] = str(user_sdc[0])
    if im.get("die"):
        conf["FP_SIZING"] = "absolute"
        conf["DIE_AREA"] = im["die"]
    conf.update(im.get("extra", {}))
    flow = "VHDLClassic" if vhd and not ver else "Classic"
    scl = cfg["target"].get("scl") or pdks.get(pdk)["scls"][0]
    j.script += f"""set -euo pipefail
mkdir -p {q(str(j.run_dir))}
cat > {q(str(j.run_dir))}/config.json <<'EOF'
{json.dumps(conf, indent=1)}
EOF
echo '[kahroba] stage: Synthesis'
"""
    if not im.get("die"):
        j.script += _autosize(cfg, pdk, scl, float(im.get("core_util", 40)) / 100, j.run_dir, "librelane")
    j.script += f"""librelane --pdk-root "$PDK_ROOT" --pdk {pdk} --scl {scl} --flow {flow} --run-tag RUN --overwrite {q(str(j.run_dir))}/config.json
"""
    rd = j.run_dir / "runs" / "RUN"
    j.finish = lambda job: reports.librelane_result(rd, top, pdk)
    return j


# tiny cores make detailed placement / PDN fail; keep at least this core side (um)
MIN_CORE_UM = {"sky130A": 80.0, "gf180mcuD": 100.0, "ihp-sg13g2": 80.0, "nangate45": 30.0, "asap7": 12.0}


def _autosize(cfg: dict, pdk: str, scl: str, util: float, run: Path, flow: str) -> str:
    """Bash that estimates cell area with a quick Yosys mapping and, for small
    designs, switches the floorplan to an absolute die of at least MIN_CORE_UM."""
    libs = pdks.liberty(pdk, scl)
    if not libs:
        return ""
    flags, read = _yosys_read(cfg)
    lib = " -liberty ".join(libs)
    margin = {"asap7": 2.0, "nangate45": 5.0}.get(pdk, 12.0)
    mincore = MIN_CORE_UM.get(pdk, 80.0)
    est = run / "area_estimate.json"
    py = f"""
import json, math, sys
raw = open('{est}').read(); st = json.loads(raw[raw.index('{{'):])
mods = st.get('modules') or {{}}
d = st.get('design') or (next(iter(mods.values())) if mods else {{}})
area = float(d.get('area') or 0)
side = math.sqrt(area / {util}) if area > 0 else 0
print(f'[kahroba] estimated cell area {{area:.1f}} um^2 -> core side {{side:.1f}} um')
if side < {mincore}:
    w = round({mincore} + 2 * {margin}, 2)
    print(f'[kahroba] small design: using a {{w}} x {{w}} um die')
    if '{flow}' == 'librelane':
        c = json.load(open('{run}/config.json'))
        c['FP_SIZING'] = 'absolute'; c['DIE_AREA'] = [0, 0, w, w]
        json.dump(c, open('{run}/config.json', 'w'), indent=1)
    else:
        m = {margin}
        open('{run}/config.mk', 'a').write(
            f'export DIE_AREA = 0 0 {{w}} {{w}}\\nexport CORE_AREA = {{m}} {{m}} {{w-m}} {{w-m}}\\n')
        mk = open('{run}/config.mk').read().replace('export CORE_UTILIZATION', '# export CORE_UTILIZATION')
        open('{run}/config.mk', 'w').write(mk)
"""
    return f"""yosys {flags} -q -p "{read.replace('"', '\\"')}; synth -top {cfg['top']} -flatten; dfflibmap -liberty {lib}; abc -liberty {lib}; tee -q -o {est} stat -liberty {lib} -json" || true
[ -s {est} ] && python3 - <<'PYEOF'
{py}
PYEOF
"""


def _impl_orfs(cfg, top, pdk, im, ver) -> jobs.Job:
    stages = ["Synthesis", "Floorplan", "Placement", "Clock tree", "Routing", "GDS"]
    j = _job(cfg, "impl", f"Implementation (ORFS, {pdk})", "", stages, stage_of=_orfs_stage)
    sdc = _sdc(cfg, j.run_dir)
    mk = f"""export DESIGN_NAME = {top}
export DESIGN_NICKNAME = {top}
export PLATFORM = {pdk}
export VERILOG_FILES = {' '.join(ver)}
export SDC_FILE = {sdc}
export CORE_UTILIZATION = {int(im.get('core_util', 40))}
export PLACE_DENSITY = {float(im.get('density', 0.6))}
"""
    flow = ORFS / "flow"
    wh = j.run_dir / "orfs"
    j.script += f"""set -euo pipefail
mkdir -p {q(str(wh))}
cat > {q(str(j.run_dir))}/config.mk <<'EOF'
{mk}EOF
echo '[kahroba] stage: Synthesis'
{_autosize(cfg, pdk, pdks.get(pdk)['scls'][0], float(im.get('core_util', 40)) / 100, j.run_dir, 'orfs') if not im.get('die') else ''}
QT_QPA_PLATFORM=offscreen make -C {q(str(flow))} DESIGN_CONFIG={q(str(j.run_dir))}/config.mk WORK_HOME={q(str(wh))} \\
  YOSYS_EXE=$(command -v yosys) OPENROAD_EXE=$(command -v openroad) OPENSTA_EXE=$(command -v sta) \\
  KLAYOUT_CMD=$(command -v klayout) NUM_CORES=$(nproc)
"""
    j.finish = lambda job: reports.orfs_result(wh, top, pdk)
    return j


# ---------------------------------------------------------------- post-impl tools
def render_layout(cfg: dict, gds: str, pdk: str, out_png: Path, size: int = 1600) -> jobs.Job:
    lyp = pdks.layer_props(pdk) or ""
    j = _job(cfg, "layout", "Render layout", "", ["Render"])
    j.script += f"""set -euo pipefail
mkdir -p {q(str(out_png.parent))}
echo '[kahroba] stage: Render'
QT_QPA_PLATFORM=offscreen klayout -zz -r {SERVER_ROOT}/klayout_render.py -rd gds={q(gds)} -rd lyp={q(lyp)} -rd out={q(str(out_png))} -rd size={size}
"""
    j.finish = lambda job: {"png": str(out_png)}
    return j


def thermal(cfg: dict, impl_run: str, opts: dict | None = None) -> jobs.Job:
    o = {"activity": 0.1, "theta_ja": 40.0, "t_amb": 25.0, "grid": 64, **(opts or {})}
    j = _job(cfg, "thermal", "Power map + thermal estimate", "", ["Power per cell", "Heat solve"])
    spec = reports.thermal_inputs(Path(impl_run), cfg["top"])
    tcl = j.run_dir / "pmap.tcl"
    csv = j.run_dir / "pmap.csv"
    libs = "\n".join(f"read_liberty {l}" for l in spec["libs"])
    j.script += f"""set -euo pipefail
mkdir -p {q(str(j.run_dir))}
cat > {q(str(tcl))} <<'EOF'
{libs}
read_db {spec['odb']}
read_sdc {spec['sdc']}
if {{"{spec.get('spef') or ''}" ne ""}} {{ read_spef {spec.get('spef') or ''} }}
set_power_activity -input -activity {float(o['activity'])}
report_power > {j.run_dir}/power.rpt
set block [ord::get_db_block]
set dbu [[ord::get_db_tech] getDbUnitsPerMicron]
set die [$block getDieArea]
set fp [open {csv} w]
puts $fp "#die,[expr [$die xMin]/double($dbu)],[expr [$die yMin]/double($dbu)],[expr [$die xMax]/double($dbu)],[expr [$die yMax]/double($dbu)]"
set corner [sta::cmd_corner]
foreach inst [$block getInsts] {{
  set si [sta::find_instance [$inst getName]]
  if {{$si eq "NULL" || $si eq ""}} {{ continue }}
  set pw [sta::instance_power $si $corner]
  set bb [$inst getBBox]
  puts $fp "[expr [$bb xMin]/double($dbu)],[expr [$bb yMin]/double($dbu)],[expr [$bb xMax]/double($dbu)],[expr [$bb yMax]/double($dbu)],[lindex $pw 3]"
}}
close $fp
exit
EOF
echo '[kahroba] stage: Power per cell'
openroad -no_splash -exit {q(str(tcl))}
echo '[kahroba] stage: Heat solve'
{VENV}/bin/python -m kahroba.thermal {q(str(csv))} {q(str(j.run_dir))}/thermal.json {o['grid']} {o['theta_ja']} {o['t_amb']}
"""
    j.script = j.script.replace("set -euo pipefail", f"set -euo pipefail\nexport PYTHONPATH={q(str(SERVER_ROOT.parent))}", 1)
    j.finish = lambda job: json.loads((job.run_dir / "thermal.json").read_text())
    return j


def open_klayout(gds: str, pdk: str) -> list[str]:
    lyp = pdks.layer_props(pdk)
    return ["klayout", "-e"] + (["-l", lyp] if lyp else []) + [gds]


def pdk_install(pid: str) -> jobs.Job:
    cfg = {"name": "kahroba-system"}
    projects.root("kahroba-system").mkdir(parents=True, exist_ok=True)
    (projects.root("kahroba-system") / "runs").mkdir(exist_ok=True)
    j = _job(cfg, "pdk", f"Install PDK {pid}", pdks.install_script(pid), ["download", "verify"])
    j.stage_of = lambda line: (lambda s: s and s.split()[0])(_marker_stage(line))
    return j
