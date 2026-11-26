# Kahroba first-run setup for Windows 10/11 (no administrator rights needed
# when WSL2 is already enabled).
#  1) enable WSL2 if missing (admin prompt + one reboot, then resumes)
#  2) import a minimal Ubuntu 24.04 as the "Kahroba" distro
#  3) apt base packages, 4) LibreLane devshell AppImage -> /nix (all EDA tools)
#  5) Python venv (FastAPI + cocotb), 6) SkyWater 130 nm PDK
# Every download resumes after a network drop and is checked against SHA256.
param(
  [string]$Location = "$env:LOCALAPPDATA\Kahroba\wsl",
  [string]$Cache = "$env:LOCALAPPDATA\Kahroba\cache",
  [string]$Distro = "Kahroba",
  [string]$LibreLane = "3.0.17",
  [switch]$SkipPdk
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
function Say($m) { Write-Host "[kahroba] $m" -ForegroundColor Yellow }
function LinuxPath($p) { $p = (Resolve-Path $p).Path; "/mnt/" + $p.Substring(0,1).ToLower() + $p.Substring(2).Replace('\','/') }
function Get-File($url, $out, $sha) {
  for ($i = 0; $i -lt 300; $i++) {
    & curl.exe -sL -C - --connect-timeout 20 --retry 3 --retry-all-errors --speed-limit 2000 --speed-time 45 -o $out $url
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep 3
  }
  if ($sha -and (Get-FileHash $out -Algorithm SHA256).Hash.ToLower() -ne $sha.ToLower()) { Remove-Item $out; throw "checksum mismatch: $out" }
}
function In-Distro([string]$user, [string]$script, [string]$arg = "") {
  $lp = LinuxPath (Join-Path $here $script)
  & wsl.exe -d $Distro -u $user -- bash -c "tr -d '\r' < '$lp' > /tmp/k.sh && bash /tmp/k.sh $arg"
  if ($LASTEXITCODE -ne 0) { throw "$script failed" }
}
New-Item -ItemType Directory -Force $Cache, $Location | Out-Null

# 1. WSL2
& wsl.exe --status *> $null
if ($LASTEXITCODE -ne 0) {
  Say "Enabling WSL2 (administrator prompt)..."
  Start-Process wsl.exe -ArgumentList "--install","--no-distribution" -Verb RunAs -Wait
  $resume = "powershell.exe -ExecutionPolicy Bypass -NoExit -File `"$($MyInvocation.MyCommand.Path)`""
  New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "KahrobaSetup" -Value $resume -Force | Out-Null
  Say "Restart Windows; setup continues after you log in."
  exit 0
}

# 2. distro
$have = (& wsl.exe -l -q) -replace "`0","" | ForEach-Object { $_.Trim() }
if ($have -notcontains $Distro) {
  $base = "ubuntu-base-24.04.3-base-amd64.tar.gz"
  $rel = "http://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release"
  $sums = (& curl.exe -sL --retry 5 "$rel/SHA256SUMS") -split "`n" | Where-Object { $_ -match "$base$" }
  Say "Downloading Ubuntu 24.04 base image..."
  Get-File "$rel/$base" "$Cache\$base" (($sums -split ' ')[0])
  & wsl.exe --import $Distro $Location "$Cache\$base" --version 2
  if ($LASTEXITCODE -ne 0) { throw "wsl --import failed" }
}

# 3. base packages
Say "Installing base packages..."
In-Distro root provision_root.sh
& wsl.exe --terminate $Distro | Out-Null

# 4. EDA toolchain
$ai = "librelane-devshell-x86_64.AppImage"
$api = Invoke-RestMethod "https://api.github.com/repos/librelane/librelane/releases/tags/$LibreLane" -Headers @{ 'User-Agent' = 'kahroba-setup' }
$asset = $api.assets | Where-Object name -eq $ai
Say "Downloading the LibreLane $LibreLane toolchain ($([math]::Round($asset.size/1MB)) MB)..."
Get-File $asset.browser_download_url "$Cache\$ai" ($asset.digest -replace '^sha256:','')
In-Distro root provision_tools.sh (LinuxPath "$Cache\$ai")

# 5. backend venv
Say "Creating the Python environment..."
In-Distro kahroba provision_user.sh

# 6. PDK
if (-not $SkipPdk) {
  Say "Downloading the SkyWater 130 nm PDK..."
  In-Distro kahroba provision_pdk.sh sky130
}
Say "Done. Start Kahroba from the Start menu."
