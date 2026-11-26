"""End-to-end test of a running Kahroba backend through its HTTP API.

For each example: create a fresh project, then lint -> RTL schematic -> cocotb
simulation -> synthesis -> RTL-to-GDS -> layout render -> thermal map, and check
the results (tests pass, DRC/LVS clean, timing met, files exist).
Usage: python e2e.py <round-name> [--quick]
"""
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:8770/api"
CASES = [
    # project, template, overrides, simulators
    ("counter", "counter", {}, ["icarus", "verilator"]),
    ("mac8", "mac8", {}, ["icarus", "verilator"]),
    ("pwm", "pwm_vhdl", {}, ["ghdl"]),
    ("mac8_45", "mac8", {"target": {"pdk": "nangate45", "scl": "nangate45"}, "clock": {"port": "clk", "period_ns": 5.0}}, []),
    ("mac8_7", "mac8", {"target": {"pdk": "asap7", "scl": "asap7sc7p5t"}, "clock": {"port": "clk", "period_ns": 1.0}}, []),
]


TOKEN = open(__import__("os").path.expanduser("~/.kahroba/token")).read().strip()


def call(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"X-Kahroba-Token": TOKEN}
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def run(project, kind, body=None):
    j = call(f"/projects/{project}/run/{kind}", "POST", body or {})
    while True:
        time.sleep(1.5)
        s = call(f"/jobs/{j['id']}")
        if s["status"] not in ("queued", "running") and s["ended"]:
            return s


def main():
    rnd = sys.argv[1] if len(sys.argv) > 1 else "r"
    rows, ok_all = [], True
    for name, tpl, over, sims in CASES:
        p = f"{name}_{rnd}"
        try:
            call(f"/projects/{p}", "GET")
        except Exception:  # noqa: BLE001
            call("/projects", "POST", {"name": p, "template": tpl})
        if over:
            cfg = call(f"/projects/{p}")["config"]
            cfg.update(over)
            call(f"/projects/{p}", "PUT", cfg)
        row = {"project": p, "checks": {}}
        chk = row["checks"]
        t0 = time.time()
        if not name.startswith("mac8_"):
            lint = run(p, "lint")
            chk["lint"] = lint["status"] == "ok" and lint["result"].get("errors", 1) == 0
            rtl = run(p, "rtl")
            chk["rtl_schematic"] = rtl["status"] == "ok" and bool(rtl["result"].get("json"))
            for sim in sims:
                s = run(p, "sim", {"simulator": sim})
                r = s["result"]
                chk[f"sim_{sim}"] = s["status"] == "ok" and r.get("failed") == 0 and r.get("passed", 0) > 0 and bool(r.get("waves"))
        syn = run(p, "synth")
        chk["synth"] = syn["status"] == "ok" and (syn["result"].get("cells") or 0) > 0
        row["synth_cells"] = syn["result"].get("cells")
        row["synth_area_um2"] = syn["result"].get("area_um2")
        row["synth_slack_ns"] = syn["result"].get("slack_ns")
        imp = run(p, "impl")
        r = imp["result"]
        chk["gds"] = imp["status"] == "ok" and bool(r.get("gds"))
        chk["timing_met"] = (r.get("setup_wns_ns") or -1) >= 0 and (r.get("hold_wns_ns") or -1) >= 0
        chk["route_drc_clean"] = (r.get("route_drc") or 0) == 0
        if r.get("flow") == "librelane":
            chk["signoff_drc_clean"] = (r.get("magic_drc") or 0) + (r.get("klayout_drc") or 0) == 0
            chk["lvs_clean"] = r.get("lvs_errors") == 0
        for k in ("setup_wns_ns", "hold_wns_ns", "fmax_mhz", "power_w", "cell_area_um2", "die_area_um2",
                  "slew_viol", "lvs_errors", "magic_drc", "klayout_drc", "route_drc"):
            row[k] = r.get(k)
        if r.get("gds"):
            lay = run(p, "layout", {"gds": r["gds"], "pdk": r["pdk"]})
            chk["layout_png"] = lay["status"] == "ok"
            th = run(p, "thermal", {"impl_run": r["run_dir"]})
            tr = th["result"]
            chk["thermal"] = th["status"] == "ok" and tr.get("t_max") is not None
            # energy balance: mean rise must equal P * theta_JA
            if chk["thermal"]:
                rise = tr["t_mean"] - tr["t_amb"]
                chk["thermal_energy_balance"] = abs(rise - tr["check_uniform_rise_k"]) <= 1e-6 + 1e-6 * rise
                row["thermal_tmax_c"] = tr["t_max"]
                row["cellpower_sum_w"] = tr["power_w"]
        row["seconds"] = round(time.time() - t0, 1)
        row["pass"] = all(chk.values())
        ok_all &= row["pass"]
        rows.append(row)
        print(json.dumps(row), flush=True)
    print("ROUND", rnd, "PASS" if ok_all else "FAIL", flush=True)
    with open(f"/tmp/e2e_{rnd}.json", "w") as f:
        json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
