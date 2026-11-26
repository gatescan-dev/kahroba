# Fetch third-party viewers into app/vendor (not committed):
#   vendor/surfer      native Surfer for Windows (GitLab release)
#   vendor/surfer-web  Surfer web build (from the Surfer VS Code extension), patched for /surfer/
param([string]$SurferVersion = "v0.7.0")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$v = Join-Path $root "app\vendor"; $tmp = Join-Path $env:TEMP "kahroba-vendor"
New-Item -ItemType Directory -Force $v, $tmp | Out-Null
$zip = "$tmp\surfer_win.zip"
curl.exe -sL --retry 10 --retry-all-errors -o $zip "https://gitlab.com/api/v4/projects/42073614/packages/generic/surfer/$SurferVersion/surfer_win_$SurferVersion.zip"
Remove-Item "$v\surfer" -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive $zip "$v\surfer"
$vsix = "$tmp\surfer.vsix"
curl.exe -sL --retry 10 --retry-all-errors -o $vsix "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/surfer-project/vsextensions/surfer/latest/vspackage"
$b = [IO.File]::ReadAllBytes($vsix)
if ($b[0] -eq 0x1f -and $b[1] -eq 0x8b) {
  $in = New-Object IO.Compression.GzipStream((New-Object IO.MemoryStream(,$b)), [IO.Compression.CompressionMode]::Decompress)
  $out = [IO.File]::Create("$vsix.zip"); $in.CopyTo($out); $out.Close()
} else { Copy-Item $vsix "$vsix.zip" -Force }
Remove-Item "$tmp\vsix" -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive "$vsix.zip" "$tmp\vsix"
Remove-Item "$v\surfer-web" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item "$tmp\vsix\extension\surfer" "$v\surfer-web" -Recurse
python (Join-Path $root "tools\vendor_surfer.py") "$v\surfer-web"
Write-Host "vendor ready: $v"
