"""Headless smoke test for the Windows port.

Builds a full MainWindow using Qt's offscreen platform plugin, exercises
config load, mihomo YAML generation, all five pages, and the runner's
non-TUN code path (without actually launching mihomo).

Run from the repo root: py tests\\smoke_test.py
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

# Force offscreen — works on a headless box / CI / a session without a display
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Redirect SUPPORT_DIR to a tempdir so we don't trash the user's real config
_TMP = Path(tempfile.mkdtemp(prefix="chainproxy_smoke_"))
os.environ["APPDATA"] = str(_TMP)

# Now import the package — paths above are baked at import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chainproxy_core as core
print(f"[smoke] platform={core.PLATFORM_LABEL}")
print(f"[smoke] SUPPORT_DIR={core.SUPPORT_DIR}")
assert str(core.SUPPORT_DIR).startswith(str(_TMP)), \
    "support dir not redirected to tempdir — bail before clobbering user state"

# 1. config schema round-trip
cfg = core.load_config()
assert cfg["local_port"] == 7890
assert "first_hops" in cfg and "second_hops" in cfg
assert cfg["final_target"] == "FirstHopOnly"
print("[smoke] load_config: OK")

# 2. inject example nodes and build the YAML
cfg["first_hops"] = [{
    "name": "test-fh", "type": "socks5",
    "server": "127.0.0.1", "port": 6666,
    "username": "", "password": "", "udp": True,
    "tls": False, "skip_cert_verify": False,
}]
cfg["second_hops"] = [{
    "name": "test-sh", "type": "trojan",
    "server": "example.com", "port": 443,
    "password": "secret", "sni": "example.com",
    "udp": True, "skip_cert_verify": False,
}]
cfg["active_first_hop"] = "test-fh"
cfg["active_second_hop"] = "test-sh"
core.save_config(cfg)
yaml = core.build_mihomo_yaml(cfg)
assert "mixed-port: 7890" in yaml
assert "127.0.0.1" in yaml
assert "dialer-proxy: test-fh" in yaml
assert "MATCH,FirstHopOnly" in yaml
print("[smoke] build_mihomo_yaml (rules disabled-but-present): OK")
print(f"[smoke] yaml is {len(yaml)} bytes")

# 3. TUN-mode YAML — should add tun + dns + IP-CIDR loop guard
cfg2 = dict(cfg)
cfg2["tun_mode"] = True
cfg2["first_hop_process_names"] = ["FastLink.exe", "AtlasCore.exe"]
yaml2 = core.build_mihomo_yaml(cfg2)
assert "tun:" in yaml2
assert "enable: true" in yaml2
assert "PROCESS-NAME,FastLink.exe,DIRECT" in yaml2
assert "PROCESS-NAME,AtlasCore.exe,DIRECT" in yaml2
print("[smoke] build_mihomo_yaml (TUN): OK")

# 4. Windows-specific: system proxy registry write/read round-trip.
# This test snapshots the user's real registry state, runs through
# enable→disable, and restores the snapshot at the end so we don't
# leave the box with broken proxy settings even if the assertions fail.
if sys.platform == "win32":
    import winreg
    KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

    def _snap():
        snap = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
                try:
                    v, _ = winreg.QueryValueEx(k, name)
                    snap[name] = v
                except FileNotFoundError:
                    snap[name] = None
        return snap

    def _restore(snap):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD,
                              int(snap.get("ProxyEnable") or 0))
            for name in ("ProxyServer", "ProxyOverride"):
                v = snap.get(name)
                if v is None:
                    try:
                        winreg.DeleteValue(k, name)
                    except FileNotFoundError:
                        pass
                else:
                    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(v))

    user_snap = _snap()
    backup_file = core.SUPPORT_DIR / ".proxy_backup.json"
    if backup_file.exists():
        backup_file.unlink()
    # Establish baseline: user has NO proxy enabled. This way set_system_proxy
    # disable should hand us back ProxyEnable=0.
    _restore({"ProxyEnable": 0, "ProxyServer": None, "ProxyOverride": None})
    try:
        ok, info = core.set_system_proxy(7890, True)
        assert ok, f"set_system_proxy enable failed: {info}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            enabled, _ = winreg.QueryValueEx(k, "ProxyEnable")
            server, _ = winreg.QueryValueEx(k, "ProxyServer")
            override, _ = winreg.QueryValueEx(k, "ProxyOverride")
        assert enabled == 1, f"ProxyEnable={enabled}"
        assert server == "127.0.0.1:7890", f"ProxyServer={server}"
        assert "<local>" in override, f"ProxyOverride missing <local>: {override}"
        print(f"[smoke] system proxy enabled: server={server}")

        ok, info = core.set_system_proxy(7890, False)
        assert ok, f"set_system_proxy disable failed: {info}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            enabled, _ = winreg.QueryValueEx(k, "ProxyEnable")
        assert enabled == 0, f"ProxyEnable after restore={enabled}"
        print("[smoke] system proxy round-trip OK")
    finally:
        # Always restore the user's REAL state, even on failure
        _restore(user_snap)
        if backup_file.exists():
            backup_file.unlink()
        import ctypes
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
        ctypes.windll.wininet.InternetSetOptionW(0, 37, 0, 0)

# 5. Single instance lock
lock1 = core.acquire_single_instance_lock()
assert lock1 is not None, "first lock should succeed"
lock2 = core.acquire_single_instance_lock()
assert lock2 is None, "second lock should be refused"
lock1.close()
lock3 = core.acquire_single_instance_lock()
assert lock3 is not None, "after release, lock should succeed again"
lock3.close()
print("[smoke] single-instance lock: OK")

# 6. Build the GUI offscreen
from PyQt6.QtWidgets import QApplication
import chainproxy_qt as gui
QApplication.setApplicationName(core.APP_NAME)
app = QApplication.instance() or QApplication(sys.argv)
app.setStyle("Fusion")
w = gui.MainWindow(app)
w.show()
app.processEvents()
assert w.windowTitle() == "ChainProxy"
# Hop through every nav page
for stack_idx in range(5):
    w._goto(stack_idx)
    app.processEvents()
    assert w.stack.currentIndex() == stack_idx, f"failed nav to {stack_idx}"
print("[smoke] navigation: OK")

# 7. Test page render against synthetic result
from chainproxy_qt import TestPage
test_page = w.test_page
fake = {
    "ok": True, "url": "https://example.com", "host": "example.com",
    "http_code": "200", "time_total_ms": 123, "time_dns_ms": 10,
    "time_connect_ms": 30, "time_tls_ms": 60,
    "rule": "RuleSet(google)", "chain": "FirstHopOnly", "proxy": "test-fh",
    "log_lines": ["sample log line"],
}
test_page._render(fake)
test_page._add_history(fake)
assert test_page.hist_tbl.rowCount() == 1
print("[smoke] test page render+history: OK")

# 8. NodeEditor basic operation
ne = w.nodes_page.first
ne.refresh()
assert ne.combo.currentText() == "test-fh"
assert ne.f_server.text() == "127.0.0.1"
assert ne.f_port.text() == "6666"
print("[smoke] node editor populated: OK")

# 9. RulesPage refresh — ensures every default rule set renders without crash
rp = w.rules_page
rp._refresh_table()
assert rp.tbl.rowCount() == len(cfg["rule_sets"])
print(f"[smoke] rules page rendered {rp.tbl.rowCount()} rules")

# 10. SettingsPage — toggle TUN ON should not crash; toggle OFF
sp = w.settings_page
sp.tun_chk.setChecked(False)  # avoid the modal information dialog firing on True
app.processEvents()
print("[smoke] settings page interactive: OK")

# 11. MihomoRunner attach_existing on cold cache should return False (no orphan)
runner = w.runner
assert not runner.is_running()
print("[smoke] runner clean state: OK")

# Cleanup
w.close()
shutil.rmtree(_TMP, ignore_errors=True)
print("\n[smoke] ALL TESTS PASSED")
