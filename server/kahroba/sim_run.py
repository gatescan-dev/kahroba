"""Run a cocotb testbench with the cocotb Python runner. Arg: sim.json."""
import json
import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

spec = json.loads(Path(sys.argv[1]).read_text())
# cocotb embeds the system Python, so simulators must be system builds too:
# put /usr/bin (apt Icarus, GHDL, g++) right after the venv, ahead of the Nix
# toolchain. Verilator 5.044 comes from Nix (not in /usr/bin) and compiles
# its model with the system g++.
_venv = str(Path(sys.executable).parent)
os.environ["PATH"] = ":".join([_venv, "/usr/bin", "/bin", os.environ.get("PATH", "")])
for _k in ("LD_LIBRARY_PATH", "PYTHONHOME", "NIX_CFLAGS_COMPILE", "NIX_LDFLAGS", "CC", "CXX"):
    os.environ.pop(_k, None)
sim = spec["sim"]
bd = Path(spec["build_dir"])
runner = get_runner(sim)
print("[kahroba] stage: Build", flush=True)
build = dict(hdl_toplevel=spec["top"], build_dir=bd, waves=spec["waves"], always=True)
if spec["vhdl"]:
    build.update(sources=spec["vhdl"], build_args=["--std=08", "-fsynopsys"])
else:
    build.update(sources=spec["verilog"], timescale=("1ns", "1ps"))
    if sim == "verilator":
        build["build_args"] = ["-Wno-fatal", "--trace-fst", "--trace-structs"]
runner.build(**build)
print("[kahroba] stage: Run tests", flush=True)
# run in the build dir (GHDL needs its library there); find the test module via PYTHONPATH
pp = ":".join(x for x in (spec["tb_dir"], os.environ.get("PYTHONPATH", "")) if x)
os.environ["PYTHONPATH"] = pp
sys.path.insert(0, spec["tb_dir"])
test = dict(hdl_toplevel=spec["top"], test_module=spec["module"], test_dir=bd,
            build_dir=bd, waves=spec["waves"], results_xml=str(bd.parent / "results.xml"),
            extra_env={"PYTHONPATH": pp})
if spec["vhdl"]:
    test["test_args"] = ["--std=08", "-fsynopsys"]
try:
    runner.test(**test)
finally:
    print("[kahroba] stage: Waves", flush=True)

# make the job fail when nothing ran or a test failed
import xml.etree.ElementTree as ET  # noqa: E402

xml = bd.parent / "results.xml"
cases = list(ET.parse(xml).getroot().iter("testcase")) if xml.exists() else []
bad = [c.get("name") for c in cases if c.find("failure") is not None or c.find("error") is not None]
if not cases:
    print("[kahroba] error: no cocotb test ran (check the test module name and imports)", flush=True)
    sys.exit(2)
if bad:
    print(f"[kahroba] error: {len(bad)} of {len(cases)} test(s) failed: {', '.join(bad)}", flush=True)
    sys.exit(1)
print(f"[kahroba] all {len(cases)} test(s) passed", flush=True)
