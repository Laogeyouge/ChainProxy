# Build ChainProxy for Windows: mihomo bundle + PyInstaller exe + Inno Setup installer.
#
# Usage from repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
# Optional flags:
#   -SkipInstaller       : only build dist\ChainProxy\, don't run iscc
#   -MihomoVersion v1.x.y: pin mihomo version (default: latest at script time)
#
# Requires: Python 3.10+, pip packages (PyQt6, pywin32, Pillow, pyinstaller).
# Optional: Inno Setup 6 (for installer.exe). Install via:
#   winget install JRSoftware.InnoSetup

param(
    [switch]$SkipInstaller,
    [string]$MihomoVersion = "v1.19.24",
    [switch]$ForceGeodataRefresh
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

# PowerShell 5.1 wraps any stderr line from a native exe as a NativeCommandError
# whenever the host is capturing stderr (CI, redirected runs, IDE tasks).
# Combined with $ErrorActionPreference = "Stop" above, that aborts the script
# on tools that legitimately write progress to stderr — PyInstaller's "INFO"
# stream is the canonical example. Run those commands with EAP scoped to
# "Continue" and rely on $LASTEXITCODE for real failure detection.
function Invoke-Native {
    param([scriptblock]$Block)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $prev }
}

$REPO    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SCRIPTS = Join-Path $REPO "scripts"
Set-Location $REPO

Write-Host "==[1/6]== Regenerating icon.ico" -ForegroundColor Cyan
Invoke-Native { py scripts\make_icon_windows.py }
if ($LASTEXITCODE -ne 0) { throw "icon generation failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "==[2/6]== Ensuring bundled mihomo.exe is present" -ForegroundColor Cyan
$mihomoLocal = Join-Path $SCRIPTS "mihomo.exe"
if (Test-Path $mihomoLocal) {
    $size = (Get-Item $mihomoLocal).Length
    Write-Host "  found existing $mihomoLocal ($size bytes)"
} else {
    # Download the amd64-compatible build — works on the widest CPU range.
    $asset = "mihomo-windows-amd64-compatible-$MihomoVersion.zip"
    $url   = "https://github.com/MetaCubeX/mihomo/releases/download/$MihomoVersion/$asset"
    Write-Host "  fetching $url ..."
    $tmpZip = Join-Path $env:TEMP "mihomo-bundle.zip"
    $tmpDir = Join-Path $env:TEMP ("mihomo-bundle-" + [guid]::NewGuid().ToString("N"))
    Invoke-WebRequest -Uri $url -OutFile $tmpZip -UseBasicParsing
    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
    $exe = Get-ChildItem -Path $tmpDir -Recurse -Filter "*.exe" | Select-Object -First 1
    if (-not $exe) { throw "mihomo zip did not contain an .exe" }
    Copy-Item -Path $exe.FullName -Destination $mihomoLocal -Force
    Remove-Item -Recurse -Force $tmpDir, $tmpZip
    Write-Host "  installed $mihomoLocal ($((Get-Item $mihomoLocal).Length) bytes)"
}

Write-Host ""
Write-Host "==[3/6]== Ensuring bundled GeoIP/GeoSite snapshot is present" -ForegroundColor Cyan
# Without these files mihomo cold-starts will deadlock on a fresh install:
# its default download URL is on github.com, which is unreachable until
# ChainProxy's proxy is up — and the proxy can't come up without these files.
# We snapshot the latest Loyalsoldier release at build time so the installer
# is self-sufficient.
$geodataDir = Join-Path $SCRIPTS "geodata"
if (-not (Test-Path $geodataDir)) {
    New-Item -ItemType Directory -Path $geodataDir | Out-Null
}
$geodataAssets = @(
    @{ Name = "Country.mmdb";
       Url  = "https://github.com/Loyalsoldier/geoip/releases/latest/download/Country.mmdb" },
    @{ Name = "geoip.dat";
       Url  = "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat" },
    @{ Name = "geosite.dat";
       Url  = "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat" }
)
foreach ($a in $geodataAssets) {
    $dest = Join-Path $geodataDir $a.Name
    if ((Test-Path $dest) -and -not $ForceGeodataRefresh) {
        $size = (Get-Item $dest).Length
        if ($size -gt 1024) {
            Write-Host ("  found {0} ({1:N0} bytes)" -f $a.Name, $size)
            continue
        }
    }
    Write-Host "  fetching $($a.Url) ..."
    try {
        Invoke-WebRequest -Uri $a.Url -OutFile $dest -UseBasicParsing -MaximumRedirection 5
        $size = (Get-Item $dest).Length
        if ($size -lt 1024) {
            throw "downloaded file looks empty: $size bytes"
        }
        Write-Host ("  installed {0} ({1:N0} bytes)" -f $a.Name, $size)
    } catch {
        Write-Warning "geodata download failed for $($a.Name): $_"
        Write-Warning "  Build will continue, but cold-start mihomo on offline / GFW boxes will fail."
        if (Test-Path $dest) { Remove-Item -Force $dest }
    }
}

Write-Host ""
Write-Host "==[4/6]== Cleaning previous dist/ and build/" -ForegroundColor Cyan
if (Test-Path "dist")  { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }

Write-Host ""
Write-Host "==[5/6]== Running PyInstaller" -ForegroundColor Cyan
Invoke-Native { py -m PyInstaller --noconfirm --clean scripts\chainproxy_windows.spec }
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

$exePath = Join-Path $REPO "dist\ChainProxy\ChainProxy.exe"
if (-not (Test-Path $exePath)) { throw "PyInstaller succeeded but $exePath missing" }
Write-Host "  ChainProxy.exe = $((Get-Item $exePath).Length) bytes"

# Sanity: confirm mihomo got copied next to the exe so find_mihomo() picks it up
$bundledMihomo = Join-Path $REPO "dist\ChainProxy\mihomo.exe"
if (-not (Test-Path $bundledMihomo)) {
    Write-Warning "mihomo.exe was NOT bundled into dist\ChainProxy\ — verify the spec's binaries[] path"
} else {
    Write-Host "  bundled mihomo = $((Get-Item $bundledMihomo).Length) bytes"
}

Write-Host ""
Write-Host "==[6/6]== Inno Setup installer" -ForegroundColor Cyan
if ($SkipInstaller) {
    Write-Host "  -SkipInstaller passed; stopping here. Use dist\ChainProxy\ChainProxy.exe to test."
    exit 0
}

# Locate iscc.exe — try PATH first, then standard install dirs (incl. winget's
# per-user install location).
$iscc = (Get-Command "iscc.exe" -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($cand in @(
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $cand) { $iscc = $cand; break }
    }
}
if (-not $iscc) {
    Write-Warning "Inno Setup not found. Install with: winget install JRSoftware.InnoSetup"
    Write-Warning "Skipping installer build. Run scripts\build_windows.ps1 again after installing."
    exit 0
}

# Inno Setup 6.x doesn't ship ChineseSimplified.isl by default — it's an
# "unofficial" community-maintained translation kept in the issrc repo.
# Drop it next to the bundled languages so iscc can find it on $include.
$isccDir = Split-Path -Parent $iscc
$chsLang = Join-Path $isccDir "Languages\ChineseSimplified.isl"
if (-not (Test-Path $chsLang)) {
    Write-Host "  fetching ChineseSimplified.isl (unofficial)..."
    try {
        Invoke-WebRequest `
            -Uri "https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/Unofficial/ChineseSimplified.isl" `
            -OutFile $chsLang -UseBasicParsing
        Write-Host "  installed $chsLang"
    } catch {
        Write-Warning "ChineseSimplified.isl download failed: $_"
        Write-Warning "Will fall back to English-only installer."
    }
}

Write-Host "  using $iscc"
Invoke-Native { & $iscc "$SCRIPTS\installer.iss" }
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (exit $LASTEXITCODE)" }

$installer = Get-ChildItem "$REPO\dist\ChainProxy-Setup-*.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($installer) {
    Write-Host ""
    Write-Host "DONE. Installer: $($installer.FullName)" -ForegroundColor Green
    Write-Host "       Size:      $([math]::Round($installer.Length / 1MB, 1)) MB"
} else {
    Write-Warning "Installer file not found in dist\ — check Inno Setup output"
}
