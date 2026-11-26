#!/usr/bin/env bash
# Kahroba: base packages, user and WSL settings. Run as root inside the distro.
set -euo pipefail
U=${KAHROBA_USER:-kahroba}
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=60 update -q
apt-get -o Acquire::Retries=10 -o Acquire::http::Timeout=60 install -y -q --no-install-recommends \
  ca-certificates curl git xz-utils zstd make bc time file less nano \
  python3 python3-venv python3-pip libpython3.12t64 g++ libc6-dev zlib1g-dev graphviz iverilog ghdl \
  sudo locales uidmap systemd systemd-sysv dbus
locale-gen en_US.UTF-8 >/dev/null 2>&1 || true
id -u "$U" >/dev/null 2>&1 || useradd -m -s /bin/bash "$U"
echo "$U ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$U
chmod 440 /etc/sudoers.d/90-$U
cat > /etc/wsl.conf <<EOF
[boot]
systemd=true
[user]
default=$U
[interop]
appendWindowsPath=false
EOF
apt-get clean
echo ROOT_OK
