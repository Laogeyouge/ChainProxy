"""End-to-end test for the system-proxy lifecycle on Windows.

Exercises the actual HKCU registry on this machine. Uses the user's REAL
APPDATA so we can verify the snapshot/restore mechanism is real.

CRITICAL: this test reads, writes, and restores the user's actual
HKCU\Internet Settings keys. It snapshots them at the start and force-restores
them on exit (even if asserts fail). It does NOT touch any keys outside
that path.
"""
import json
import os
import sys
import time
from pathlib import Path

# Use REAL APPDATA so set_system_proxy works against real registry, but
# point CONFIG to a sub-directory we'll clean up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chainproxy_core as core
import winreg

KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def read_state():
    state = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
            try:
                v, _ = winreg.QueryValueEx(k, name)
                state[name] = v
            except FileNotFoundError:
                state[name] = None
    return state


def write_state(s):
    """Restore the user's original state. Mirror of read_state."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD,
                          int(s.get("ProxyEnable") or 0))
        if s.get("ProxyServer") is not None:
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ,
                              str(s["ProxyServer"]))
        if s.get("ProxyOverride") is not None:
            winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ,
                              str(s["ProxyOverride"]))
        else:
            try:
                winreg.DeleteValue(k, "ProxyOverride")
            except FileNotFoundError:
                pass


# Snapshot the user's real state up front so we always restore it
ORIGINAL = read_state()
print(f"[setup] snapshotting user state: ProxyEnable={ORIGINAL.get('ProxyEnable')}, "
      f"ProxyServer={ORIGINAL.get('ProxyServer')!r}")

# Make sure no leftover backup file confuses us
backup_path = core.SUPPORT_DIR / ".proxy_backup.json"
if backup_path.exists():
    print(f"[setup] removing stale backup: {backup_path}")
    backup_path.unlink()


def restore_state():
    """Restore the user's original state and refresh WinINet."""
    write_state(ORIGINAL)
    import ctypes
    ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
    ctypes.windll.wininet.InternetSetOptionW(0, 37, 0, 0)
    if backup_path.exists():
        backup_path.unlink()


_failed = False
try:
    # ─────────────────────────────────────────────────────────────────
    # Test 1: pre-set a custom user state, verify enable→disable restores
    # ─────────────────────────────────────────────────────────────────
    print("\n[test 1] enable→disable restores original")
    pre = {
        "ProxyEnable": 1,
        "ProxyServer": "myairport.local:1234",
        "ProxyOverride": "*example.com;localhost",
    }
    write_state(pre)
    if backup_path.exists():
        backup_path.unlink()

    ok, info = core.set_system_proxy(7890, True)
    assert ok, f"enable failed: {info}"
    s = read_state()
    assert s["ProxyEnable"] == 1
    assert s["ProxyServer"] == "127.0.0.1:7890", f"got {s['ProxyServer']!r}"
    assert "<local>" in s["ProxyOverride"]
    assert backup_path.exists(), "snapshot file must exist after enable"

    # snapshot should have captured pre values
    snap = json.loads(backup_path.read_text(encoding="utf-8"))
    assert snap["ProxyServer"] == "myairport.local:1234", \
        f"snapshot didn't capture original ProxyServer: {snap}"
    print("  enable: ProxyServer rewritten, snapshot captured pre values")

    # disable
    ok, info = core.set_system_proxy(0, False)
    assert ok
    assert info == "restored"
    s = read_state()
    assert s["ProxyEnable"] == 1, "should restore to user's pre-enable value (1)"
    assert s["ProxyServer"] == "myairport.local:1234"
    assert s["ProxyOverride"] == "*example.com;localhost"
    assert not backup_path.exists(), "snapshot file must be consumed after restore"
    print("  disable: original state restored exactly")

    # ─────────────────────────────────────────────────────────────────
    # Test 2: double enable does NOT overwrite the original snapshot
    # ─────────────────────────────────────────────────────────────────
    print("\n[test 2] double enable preserves original snapshot")
    pre = {"ProxyEnable": 0, "ProxyServer": "original.local:9999",
           "ProxyOverride": None}
    write_state(pre)
    if backup_path.exists():
        backup_path.unlink()

    core.set_system_proxy(7890, True)
    snap1 = json.loads(backup_path.read_text(encoding="utf-8"))

    # Second enable (e.g. user toggles port and we re-enable)
    core.set_system_proxy(8888, True)
    snap2 = json.loads(backup_path.read_text(encoding="utf-8"))
    assert snap1 == snap2, "second enable must NOT overwrite snapshot"
    assert snap2["ProxyServer"] == "original.local:9999"
    print("  snapshot preserved across re-enable")

    # And restore still puts back the original
    core.set_system_proxy(0, False)
    s = read_state()
    assert s["ProxyServer"] == "original.local:9999"
    print("  disable still restores the original (not the second port)")

    # ─────────────────────────────────────────────────────────────────
    # Test 3: simulated crash → startup cleanup
    # ─────────────────────────────────────────────────────────────────
    print("\n[test 3] simulated crash + startup cleanup")
    pre = {"ProxyEnable": 1, "ProxyServer": "user_real.proxy:3128",
           "ProxyOverride": None}
    write_state(pre)
    if backup_path.exists():
        backup_path.unlink()

    core.set_system_proxy(7890, True)
    s = read_state()
    assert s["ProxyServer"] == "127.0.0.1:7890"
    # Now simulate the GUI being killed: backup file exists, but the GUI
    # process is gone. Next launch should detect this and restore.
    assert backup_path.exists()

    # Simulate the next GUI launch's startup cleanup
    ok, info = core.set_system_proxy(0, False)
    assert ok and info == "restored"
    s = read_state()
    assert s["ProxyServer"] == "user_real.proxy:3128", \
        f"crash-recovery failed: {s}"
    assert s["ProxyEnable"] == 1
    print("  crash recovery: original user proxy restored")

    # ─────────────────────────────────────────────────────────────────
    # Test 4: panic_recover force-clears even WITHOUT a snapshot
    # ─────────────────────────────────────────────────────────────────
    print("\n[test 4] panic_recover force-clears regardless of snapshot")
    # Garbage state with no snapshot — simulating "another proxy client
    # left dirty registry, ChainProxy never owned anything"
    write_state({"ProxyEnable": 1, "ProxyServer": "ghost.proxy:7892",
                 "ProxyOverride": "garbage"})
    if backup_path.exists():
        backup_path.unlink()
    # ChainProxy never wrote a snapshot — but panic should still nuke it
    msgs = []
    core.panic_recover(lambda m: msgs.append(m))
    s = read_state()
    assert s["ProxyEnable"] == 0, f"ProxyEnable={s['ProxyEnable']!r}"
    # Force-clear deletes the value entirely; reading it back returns None.
    assert s["ProxyServer"] is None, f"ProxyServer={s['ProxyServer']!r}"
    assert s["ProxyOverride"] is None, f"ProxyOverride={s['ProxyOverride']!r}"
    print("  panic_recover cleared the registry even with no snapshot")

    # ─────────────────────────────────────────────────────────────────
    # Test 5: ProxyOverride deletion doesn't crash if it was never set
    # ─────────────────────────────────────────────────────────────────
    print("\n[test 5] disable when ProxyOverride absent originally")
    write_state({"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": None})
    if backup_path.exists():
        backup_path.unlink()
    core.set_system_proxy(7890, True)
    core.set_system_proxy(0, False)
    s = read_state()
    assert s["ProxyEnable"] == 0
    # Original had no ProxyOverride → should be absent again, not lingering
    assert s["ProxyOverride"] is None
    print("  cleanly handled originally-absent ProxyOverride")

except Exception as e:
    _failed = True
    import traceback
    print(f"\n[FAIL] {type(e).__name__}: {e}")
    traceback.print_exc()
finally:
    restore_state()

if _failed:
    print("\n[lifecycle] FAILED")
    sys.exit(1)
print("\n[lifecycle] ALL TESTS PASSED")
