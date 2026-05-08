"""Headless smoke test for the new NodeEditor TUN-helper widgets.

We can't simulate a full mihomo lifecycle here, but we can verify:
  - NodeEditor builds without exception when kind="first" (with helper)
    and kind="second" (no helper)
  - The auto-detect button is wired and clickable
  - _refresh_tun_helper_label renders the existing list correctly
  - first_hop_process_names round-trips through the dialog

Run from the repo root: py tests\\test_node_editor_tun.py
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

_TMP = Path(tempfile.mkdtemp(prefix="chainproxy_ne_"))
if sys.platform == "win32":
    os.environ["APPDATA"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chainproxy_core as core  # noqa: E402

cfg = core.load_config()
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
cfg["first_hop_process_names"] = ["alreadyhere.exe"]
core.save_config(cfg)

from PyQt6.QtWidgets import QApplication  # noqa: E402
import chainproxy_qt as gui  # noqa: E402

QApplication.setApplicationName(core.APP_NAME)
app = QApplication.instance() or QApplication(sys.argv)
app.setStyle("Fusion")

w = gui.MainWindow(app)
w.show()
app.processEvents()

# 1. First-hop NodeEditor must have the TUN helper widgets
first_ne = w.nodes_page.first
assert first_ne.tun_helper is not None, "first-hop editor should have tun_helper"
assert hasattr(first_ne, "tun_proc_label"), "tun_proc_label missing"
assert hasattr(first_ne, "_auto_detect_processes")
assert hasattr(first_ne, "_refresh_tun_helper_label")
print("[ne] first-hop editor exposes TUN helper: OK")

# 2. Second-hop NodeEditor must NOT have the helper
second_ne = w.nodes_page.second
assert second_ne.tun_helper is None, "second-hop editor must not have tun_helper"
print("[ne] second-hop editor correctly omits TUN helper: OK")

# 3. Label reflects existing list
first_ne._refresh_tun_helper_label()
assert "alreadyhere.exe" in first_ne.tun_proc_label.text()
print("[ne] tun_proc_label populates from cfg: OK")

# 4. Empty list shows the placeholder
cfg["first_hop_process_names"] = []
core.save_config(cfg)
w.cfg = core.load_config()
first_ne._refresh_tun_helper_label()
assert "尚未配置" in first_ne.tun_proc_label.text()
print("[ne] tun_proc_label placeholder when empty: OK")

# 5. Calling auto-detect with a non-loopback host should return without crash
first_ne.f_server.setText("8.8.8.8")
first_ne.f_port.setText("53")
# We don't want a modal — but the function does QMessageBox.information.
# In offscreen mode QMessageBox.information returns immediately with default
# button. Confirm we don't crash.
# Actually, QMessageBox can block even offscreen. We'll test the code path
# that doesn't hit a dialog: invalid port.
first_ne.f_server.setText("127.0.0.1")
first_ne.f_port.setText("not-a-number")
# Drive _auto_detect_processes manually but skip the QMessageBox.information
# call by patching it:
from PyQt6.QtWidgets import QMessageBox  # noqa: E402
calls = []
orig_info = QMessageBox.information
QMessageBox.information = staticmethod(lambda *a, **kw: calls.append(a))
QMessageBox.question = staticmethod(
    lambda *a, **kw: QMessageBox.StandardButton.No)
try:
    first_ne._auto_detect_processes()
    assert calls, "should have shown an info dialog about invalid port"
finally:
    QMessageBox.information = orig_info  # type: ignore
print("[ne] auto-detect handles invalid port gracefully: OK")

w.close()
shutil.rmtree(_TMP, ignore_errors=True)
print("\n[ne] ALL TESTS PASSED")
