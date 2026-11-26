#!/usr/bin/env bash
# Dev helper (inside the Kahroba distro): copy the repo's server, examples,
# browser UI and Surfer web build to ~/.kahroba/app and (re)start the backend.
set -e
SRC=${1:-/mnt/a/Kahroba/src}
A=$HOME/.kahroba/app
mkdir -p $A/ui/node_modules/monaco-editor
rm -rf $A/server $A/examples $A/ui/renderer
cp -r $SRC/server $SRC/examples $A/
cp -r $SRC/app/renderer $A/ui/
[ -d $A/ui/node_modules/monaco-editor/min ] || cp -r $SRC/app/node_modules/monaco-editor/min $A/ui/node_modules/monaco-editor/
[ -d $SRC/app/vendor/surfer-web ] && rm -rf $A/surfer-web && cp -r $SRC/app/vendor/surfer-web $A/surfer-web
pkill -f "python -m kahroba.app" 2>/dev/null || true
sleep 1
cd $A/server && nohup $HOME/.kahroba/venv/bin/python -m kahroba.app > $HOME/.kahroba/server.log 2>&1 &
for i in $(seq 1 30); do curl -sf 127.0.0.1:8770/api/health && break; sleep 1; done; echo
