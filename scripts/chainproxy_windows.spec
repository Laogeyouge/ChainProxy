# PyInstaller spec for ChainProxy on Windows.
#
# Usage:
#   pyinstaller scripts/chainproxy_windows.spec
#
# Outputs:
#   dist/ChainProxy/ChainProxy.exe       (single dir bundle, recommended)
#   dist/ChainProxy/_internal/...        (Qt + Python runtime)
#
# We deliberately use --onedir (not --onefile):
#   - First-launch is instant (no extraction step)
#   - Easier to ship mihomo.exe alongside the GUI
#   - Inno Setup wraps the dir into a single installer.exe
#
# To bundle mihomo.exe with the build, drop it next to this spec at
# scripts/mihomo.exe before running pyinstaller. Otherwise the GUI will
# look for it in the user's PATH / common install dirs at startup.
import os
from pathlib import Path

REPO = Path(SPECPATH).resolve().parent  # scripts/ → repo root

block_cipher = None

# Optional: bundle mihomo.exe if it sits next to this spec
binaries = []
mihomo_local = REPO / "scripts" / "mihomo.exe"
if mihomo_local.exists():
    binaries.append((str(mihomo_local), "."))

datas = [
    (str(REPO / "config.example.json"), "."),
    (str(REPO / "icon.png"), "."),
    (str(REPO / "icon.ico"), "."),
]

a = Analysis(
    [str(REPO / "chainproxy_qt.py")],
    pathex=[str(REPO)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "core",
        "core._common",
        "core._windows",
        # winreg / ctypes / winreg are stdlib but PyInstaller's analyzer
        # sometimes misses them when only used inside string-eval'd code.
        "winreg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Don't drag in the macOS-only backend; PyInstaller would still
        # collect it and cause "fcntl missing" warnings.
        "core._macos",
        "fcntl",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChainProxy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX often triggers Defender heuristics
    console=False,        # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(REPO / "icon.ico"),
    # uac_admin=False — we elevate per-launch via ShellExecute, not at app
    # startup. The whole point of running the GUI unelevated is to keep the
    # user's normal config-edit UX clean of UAC prompts.
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ChainProxy",
)
