#!/usr/bin/env bash
# Kahroba: download a PDK family with ciel at the revision LibreLane is
# validated against (librelane/pdk_hashes.yaml). Usage: provision_pdk.sh sky130
set -euo pipefail
FAM=${1:-sky130}
HF=$(ls /nix/store/*-librelane-*/lib/python3*/site-packages/librelane/pdk_hashes.yaml | head -1)
REV=$(grep "^$FAM:" "$HF" | awk '{print $2}')
echo "[kahroba] PDK $FAM @ $REV"
for i in 1 2 3 4 5 6; do
  /opt/kahroba/devshell ciel enable --pdk-root "$HOME/.ciel" --pdk-family "$FAM" "$REV" && break
  sleep 10
done
du -sh "$HOME/.ciel"
