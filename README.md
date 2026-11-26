# Kahroba

RTL-to-GDSII studio for Windows. The desktop app runs on Windows; the open-source EDA tools run in a WSL2 distribution.

| Step | Tools |
|---|---|
| Editing | Monaco editor (Verilog, SystemVerilog, VHDL) |
| Lint | Verilator, GHDL |
| Simulation | cocotb with Icarus Verilog, Verilator or GHDL; waveforms in Surfer |
| Synthesis | Yosys (+ GHDL plugin), ABC, OpenSTA |
| Implementation | LibreLane (OpenROAD, OpenSTA, Magic, KLayout, Netgen) for sky130 / gf180mcu / ihp-sg13g2; OpenROAD-flow-scripts for nangate45 / asap7 |
| Reports | timing, power, area, DRC/LVS, power map and thermal estimate |

## Install

Run the installer from the Releases page. On first start the setup wizard enables WSL2, creates the `Kahroba` distribution and downloads the toolchain and the SkyWater 130 nm PDK.

Manual setup from a clone:

```powershell
powershell -ExecutionPolicy Bypass -File provision\setup.ps1
```

## Build the desktop app

```powershell
cd app
npm install
powershell -ExecutionPolicy Bypass -File ..\tools\fetch_vendor.ps1
npm run dist
```

## Layout

- `app/` Electron desktop app (UI)
- `server/` FastAPI backend that runs inside WSL
- `provision/` WSL setup scripts
- `examples/` example projects (counter, mac8, pwm_vhdl)

## License

Apache-2.0
