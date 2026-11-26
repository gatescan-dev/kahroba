"""Patch the Surfer web build so it can be served under /surfer/ offline:
absolute asset paths -> /surfer/..., no analytics, no service worker.
Usage: python vendor_surfer.py <surfer-web dir>"""
import re
import sys
from pathlib import Path

d = Path(sys.argv[1])
f = d / "index.html"
t = f.read_text(encoding="utf-8")
t = t.replace("'/surfer.js'", "'/surfer/surfer.js'").replace("'/surfer_bg.wasm'", "'/surfer/surfer_bg.wasm'")
t = t.replace('href="/surfer.js"', 'href="/surfer/surfer.js"').replace('href="/surfer_bg.wasm"', 'href="/surfer/surfer_bg.wasm"')
t = t.replace('<base href="/" />', '<base href="/surfer/" />')
t = re.sub(r"<script[^>]*gc\.zgo\.at[^>]*></script>", "", t)
t = re.sub(r"navigator\.serviceWorker\.register\([^)]*\);?", "", t)
f.write_text(t, encoding="utf-8")
(d / "sw.js").unlink(missing_ok=True)
print("patched", f)
