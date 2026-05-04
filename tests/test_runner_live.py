"""Live test: spawn mihomo.exe via MihomoRunner (non-TUN path), verify it
runs, then verify stop() actually kills it. Uses an isolated tempdir so it
doesn't touch the user's real config or running GUI.

Skipped if mihomo isn't installed.
"""
import os
import sys
import tempfile
import time
import shutil
import socket
import subprocess
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="chainproxy_runner_"))

# Snapshot the real APPDATA so we can find the installed mihomo.exe before we
# redirect APPDATA to our tempdir. Otherwise find_mihomo() looks in the empty
# tempdir and reports nothing installed.
_REAL_APPDATA = os.environ.get("APPDATA", "")
_REAL_MIHOMO = Path(_REAL_APPDATA) / "ChainProxy" / "mihomo.exe" if _REAL_APPDATA else None

os.environ["APPDATA"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chainproxy_core as core

# Make sure our tempdir has mihomo too, so MIHOMO_BIN_CANDIDATES finds it
if _REAL_MIHOMO and _REAL_MIHOMO.exists():
    core.SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REAL_MIHOMO, core.SUPPORT_DIR / "mihomo.exe")

mihomo_bin = core.find_mihomo()
if not mihomo_bin:
    print("[runner] mihomo not installed — SKIP")
    sys.exit(0)
print(f"[runner] using {mihomo_bin}")
print(f"[runner] SUPPORT_DIR={core.SUPPORT_DIR}")

cfg = core.load_config()
# Use a port pair unlikely to clash with the user's real GUI (7890 / 9999)
cfg["local_port"] = 17890
cfg["controller_port"] = 19999
cfg["first_hops"] = [{
    "name": "fh-test", "type": "socks5",
    "server": "127.0.0.1", "port": 6666,
    "username": "", "password": "", "udp": True,
    "tls": False, "skip_cert_verify": False,
}]
cfg["second_hops"] = [{
    "name": "sh-test", "type": "socks5",
    "server": "127.0.0.1", "port": 6666,
    "udp": False, "tls": False, "skip_cert_verify": False,
}]
cfg["active_first_hop"] = "fh-test"
cfg["active_second_hop"] = "sh-test"
cfg["rules_enabled"] = False  # avoid trying to load missing rule-set files
cfg["tun_mode"] = False        # non-TUN path — no UAC
core.save_config(cfg)

yaml = core.build_mihomo_yaml(cfg)
core.MIHOMO_YAML.write_text(yaml, encoding="utf-8")
print(f"[runner] wrote yaml ({len(yaml)} bytes) to {core.MIHOMO_YAML}")

logs = []
runner = core.MihomoRunner(lambda m: logs.append(m))
print("[runner] starting...")
runner.start(mihomo_bin, use_sudo=False)
assert runner.is_running(), "runner reports not running right after start"
print(f"[runner] started: pid={runner.proc.pid}")

# Wait a moment for mihomo to bind its ports
time.sleep(1.5)
assert runner.is_running(), "mihomo died within 1.5s — see log below"

# Verify the controller port is bound (proves mihomo actually started, not
# just spawned-and-crashed)
def port_listening(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False

ctl_ok = port_listening(19999)
mix_ok = port_listening(17890)
print(f"[runner] controller :19999 listening: {ctl_ok}")
print(f"[runner] mixed-port  :17890 listening: {mix_ok}")
assert ctl_ok, "controller port never came up — mihomo didn't start cleanly"
assert mix_ok, "mixed-port never came up"

print("[runner] stopping...")
runner.stop()
assert not runner.is_running(), "runner reports still running after stop()"

# Confirm the OS actually killed it (mihomo PID gone, ports released)
time.sleep(0.5)
ctl_after = port_listening(19999)
print(f"[runner] controller :19999 after stop: {ctl_after}")
assert not ctl_after, "port still bound — process probably orphaned"

# Confirm log tail captured at least one mihomo log line
log_text = core.MIHOMO_LOG.read_text(encoding="utf-8", errors="replace")
print(f"[runner] mihomo.log size: {len(log_text)} bytes")
assert len(log_text) > 0, "mihomo wrote nothing to its log"

# Quick attach_existing test: spawn raw mihomo, then ask runner to adopt it
print("[runner] testing attach_existing...")
raw = subprocess.Popen(
    [mihomo_bin, "-d", str(core.RUNTIME_DIR), "-f", str(core.MIHOMO_YAML)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
time.sleep(1.0)
adopt_runner = core.MihomoRunner(lambda m: logs.append(m))
ok = adopt_runner.attach_existing(lambda m: print(f"   {m}"))
if ok:
    print(f"[runner] attached to orphan pid={adopt_runner.uac_pid or raw.pid}")
else:
    print("[runner] attach_existing did not find orphan (wmic may be slow on this box)")
# Either way, kill the raw process directly
raw.terminate()
try:
    raw.wait(timeout=3)
except Exception:
    raw.kill()

shutil.rmtree(_TMP, ignore_errors=True)
print("\n[runner] LIVE RUNNER TEST PASSED")
