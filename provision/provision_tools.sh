#!/usr/bin/env bash
# Kahroba: install the LibreLane toolchain from the official devshell AppImage
# (Yosys+GHDL/slang plugins, OpenROAD, OpenSTA, Magic, Netgen, KLayout,
# Verilator, Icarus, GHDL, ciel). The AppImage holds a Nix store; we place it at
# /nix so every tool runs natively (no FUSE, no user namespaces). Run as root.
# Usage: provision_tools.sh <path-to-librelane-devshell-x86_64.AppImage>
set -euo pipefail
AI=${1:?AppImage path}
W=/opt/kahroba
mkdir -p "$W"
if [ ! -d /nix/store ]; then
  rm -rf "$W/extract" && mkdir -p "$W/extract" && cd "$W/extract"
  "$AI" --appimage-extract >/dev/null
  mv squashfs-root/nix /nix
  cp -P squashfs-root/entrypoint "$W/devshell"
  rm -rf "$W/extract"
fi
[ -e "$W/devshell" ] || ln -sf "$(ls -d /nix/store/*-devshell/bin/devshell | head -1)" "$W/devshell"
"$W/devshell" bash -c 'for t in librelane yosys openroad sta magic netgen klayout verilator iverilog ghdl ciel; do printf "%-10s %s\n" $t "$(command -v $t || echo MISSING)"; done'
echo TOOLS_OK
