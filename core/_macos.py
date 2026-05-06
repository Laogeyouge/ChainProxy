"""macOS-specific backend: paths, system proxy via networksetup, sudoers
helper for TUN, MihomoRunner that uses setsid + signals + sudo, single
instance lock via fcntl, window activation via osascript."""

import errno
import getpass
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import _common
from ._common import (  # re-exported as part of platform API
    APP_NAME, PROTOCOLS, SS_CIPHERS, FAKE_GATEWAY, RULE_TARGETS,
    LOYALSOLDIER_BASE, DEFAULT_RULE_SETS, DEFAULT_CONFIG,
    proxy_to_mihomo, build_mihomo_yaml as _build_mihomo_yaml_raw,
    tcp_reachable,
)

# ---------- paths ----------
SUPPORT_DIR = Path.home() / "Library/Application Support" / APP_NAME
CONFIG_PATH = SUPPORT_DIR / "config.json"
RUNTIME_DIR = SUPPORT_DIR / "runtime"
MIHOMO_YAML = RUNTIME_DIR / "config.yaml"
MIHOMO_LOG = RUNTIME_DIR / "mihomo.log"
RULESET_DIR = RUNTIME_DIR / "ruleset"
MIHOMO_BIN_CANDIDATES = [
    "/opt/homebrew/bin/mihomo",
    "/usr/local/bin/mihomo",
    shutil.which("mihomo") or "",
]

# ---------- helper-script for TUN privilege ----------
HELPER_PATH = "/usr/local/bin/chainproxy-helper.sh"
SUDOERS_PATH = "/etc/sudoers.d/chainproxy"
HELPER_VERSION = "5"
HELPER_SCRIPT = r"""#!/bin/bash
# version: 5
# ChainProxy mihomo helper. Managed by ChainProxy.app — DO NOT EDIT.
# Args: <runtime-dir> <action: start|stop|status|recover|flush-dns> [yaml-path]
set -e
RUNTIME="$1"
ACTION="$2"
YAML="$3"
PIDFILE="$RUNTIME/mihomo.pid"
LOG="$RUNTIME/mihomo.log"
MIHOMO=/opt/homebrew/bin/mihomo
[ -x "$MIHOMO" ] || MIHOMO=/usr/local/bin/mihomo
GW=198.18.0.1

cleanup_routes() {
  for net in 1.0.0.0/8 2.0.0.0/7 4.0.0.0/6 8.0.0.0/5 16.0.0.0/4 \
             32.0.0.0/3 64.0.0.0/2 128.0.0.0/1; do
    /sbin/route -n delete -net "$net" "$GW" 2>/dev/null || true
  done
}

cleanup_utun() {
  for ifname in $(ifconfig -l 2>/dev/null); do
    case "$ifname" in
      utun*)
        if ifconfig "$ifname" 2>/dev/null | grep -q "$GW"; then
          /sbin/ifconfig "$ifname" down 2>/dev/null || true
        fi
        ;;
    esac
  done
}

# Why: macOS resolver caches our fakeip answers (claude.ai → 198.18.0.x).
# That cache outlives mihomo. Whenever mihomo's fakeip↔domain table changes
# or mihomo exits, the cached fakeips become stale — apps connect to a
# number nobody recognises and routing breaks (most visibly on Claude /
# short-lived TLS, while Netflix-style long-lived QUIC keeps working).
# Flushing on every lifecycle event guarantees the system never holds a
# fakeip that mihomo can't resolve. dscacheutil works for any user;
# killall -HUP mDNSResponder requires root, which is why this lives in
# the helper.
flush_dns() {
  /usr/bin/dscacheutil -flushcache 2>/dev/null || true
  /usr/bin/killall -HUP mDNSResponder 2>/dev/null || true
}

kill_orphans() {
  pkill -TERM -f "mihomo -d $RUNTIME" 2>/dev/null || true
  for i in $(seq 1 15); do
    pgrep -f "mihomo -d $RUNTIME" >/dev/null 2>&1 || return 0
    sleep 0.2
  done
  pkill -KILL -f "mihomo -d $RUNTIME" 2>/dev/null || true
}

case "$ACTION" in
  start)
    kill_orphans
    cleanup_routes
    cleanup_utun
    flush_dns
    rm -f "$PIDFILE"
    nohup "$MIHOMO" -d "$RUNTIME" -f "$YAML" >> "$LOG" 2>&1 &
    pid=$!
    echo $pid > "$PIDFILE"
    sleep 0.5
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PIDFILE"
      echo "ERROR: mihomo died immediately, see $LOG" >&2
      tail -20 "$LOG" >&2 || true
      exit 1
    fi
    echo "started pid=$pid"
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      pid=$(cat "$PIDFILE")
      kill -TERM "$pid" 2>/dev/null || true
      for i in $(seq 1 30); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.2
      done
      kill -KILL "$pid" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    kill_orphans
    cleanup_routes
    cleanup_utun
    flush_dns
    echo "stopped + routes cleaned + dns flushed"
    ;;
  recover)
    if [ -f "$PIDFILE" ]; then
      pid=$(cat "$PIDFILE")
      kill -KILL "$pid" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    kill_orphans
    pkill -KILL -f "mihomo -d $RUNTIME" 2>/dev/null || true
    cleanup_routes
    cleanup_utun
    flush_dns
    echo "recovered"
    ;;
  flush-dns)
    flush_dns
    echo "dns flushed"
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "running pid=$(cat "$PIDFILE")"
    elif pgrep -f "mihomo -d $RUNTIME" >/dev/null 2>&1; then
      pgrep -f "mihomo -d $RUNTIME" | head -1 | awk '{print "orphan pid="$1}'
    else
      echo "stopped"
    fi
    ;;
  *)
    echo "usage: $0 RUNTIME start|stop|status|recover|flush-dns [YAML]" >&2
    exit 2
    ;;
esac
"""


def helper_installed():
    if not (os.path.exists(HELPER_PATH) and os.path.exists(SUDOERS_PATH)):
        return False
    try:
        head = Path(HELPER_PATH).read_text(errors="ignore").splitlines()[:5]
        return any(f"version: {HELPER_VERSION}" in line for line in head)
    except Exception:
        return False


def install_helper():
    """One-time admin prompt: install root-owned helper + sudoers entry."""
    user = getpass.getuser()
    tmp_helper = Path("/tmp/chainproxy-helper.sh")
    tmp_helper.write_text(HELPER_SCRIPT)
    sudoers_line = f"{user} ALL=(root) NOPASSWD: {HELPER_PATH}"
    cmd = (
        f"mkdir -p /usr/local/bin && "
        f"cp '{tmp_helper}' '{HELPER_PATH}' && "
        f"chown root:wheel '{HELPER_PATH}' && chmod 755 '{HELPER_PATH}' && "
        f"echo '{sudoers_line}' > '{SUDOERS_PATH}' && "
        f"chown root:wheel '{SUDOERS_PATH}' && chmod 440 '{SUDOERS_PATH}' && "
        f"rm -f '{tmp_helper}'"
    )
    escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
    apple = f'do shell script "{escaped}" with administrator privileges'
    result = subprocess.run(
        ["osascript", "-e", apple], capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "").strip() or "授权被取消"
        )


# ---------- config & rule-set wrappers (inject paths) ----------

def load_config():
    return _common.load_config(CONFIG_PATH, SUPPORT_DIR, RUNTIME_DIR, RULESET_DIR)


def save_config(cfg):
    _common.save_config(cfg, CONFIG_PATH, SUPPORT_DIR)


def download_rule_set(rs, timeout=20):
    return _common.download_rule_set(rs, RULESET_DIR, timeout=timeout)


def update_all_rule_sets(cfg, log_cb):
    return _common.update_all_rule_sets(
        cfg, RULESET_DIR, CONFIG_PATH, SUPPORT_DIR, log_cb)


def rule_set_local_path_exists(name):
    return _common.rule_set_local_path_exists(name, RULESET_DIR)


def build_mihomo_yaml(cfg):
    return _build_mihomo_yaml_raw(cfg, RULESET_DIR)


def test_url_through_proxy(url, local_port, log_path, timeout=15,
                           controller_port=None, controller_secret=None):
    return _common.test_url_through_proxy(
        url, local_port, log_path, "/dev/null", timeout=timeout,
        controller_port=controller_port,
        controller_secret=controller_secret)


# ---------- mihomo binary ----------

def find_mihomo():
    for p in MIHOMO_BIN_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


# ---------- macOS system proxy ----------

def _default_route_iface():
    try:
        out = subprocess.check_output(["route", "-n", "get", "default"], text=True)
    except Exception:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("interface:"):
            return line.split(":", 1)[1].strip()
    return None


def _service_to_iface_map():
    try:
        out = subprocess.check_output(
            ["networksetup", "-listnetworkserviceorder"], text=True
        )
    except Exception:
        return {}
    mapping = {}
    pairs = re.findall(
        r"\(\d+\)\s+(.+?)\n\(Hardware Port:.*?Device:\s*([^)]+)\)",
        out,
    )
    for name, dev in pairs:
        mapping[name.strip()] = dev.strip()
    return mapping


def get_active_network_service():
    iface = _default_route_iface()
    smap = _service_to_iface_map()
    if iface:
        for svc, dev in smap.items():
            if dev == iface:
                return svc
    try:
        out = subprocess.check_output(
            ["networksetup", "-listnetworkserviceorder"], text=True
        )
    except Exception:
        return None
    services = re.findall(r"^\((?:\*?\d+)\)\s+(.+?)$", out, re.MULTILINE)
    services = [s.strip() for s in services if s.strip()]
    for svc in services:
        try:
            info = subprocess.check_output(["networksetup", "-getinfo", svc], text=True)
            if "IP address: " in info and "IP address: none" not in info:
                return svc
        except Exception:
            continue
    return services[0] if services else None


def set_system_proxy(port, enable):
    svc = get_active_network_service()
    if not svc:
        return False, "no active network service"
    cmds = []
    if enable:
        cmds += [
            ["networksetup", "-setwebproxy", svc, "127.0.0.1", str(port)],
            ["networksetup", "-setsecurewebproxy", svc, "127.0.0.1", str(port)],
            ["networksetup", "-setsocksfirewallproxy", svc, "127.0.0.1", str(port)],
        ]
    else:
        cmds += [
            ["networksetup", "-setwebproxystate", svc, "off"],
            ["networksetup", "-setsecurewebproxystate", svc, "off"],
            ["networksetup", "-setsocksfirewallproxystate", svc, "off"],
        ]
    errs = []
    for c in cmds:
        try:
            subprocess.run(c, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            errs.append(e.stderr.strip())
    if errs:
        return False, "; ".join(errs)
    return True, svc


def _flush_dns_cache_unprivileged(log_cb):
    """Flush the user-visible portion of macOS DNS cache without sudo.
    `dscacheutil -flushcache` works for any user; the deeper mDNSResponder
    HUP needs root and lives in the helper. Worth running anyway because
    even a partial flush often clears the stale claude.ai → 198.18.0.x
    entries that confuse other proxy clients."""
    try:
        subprocess.run(
            ["/usr/bin/dscacheutil", "-flushcache"],
            capture_output=True, text=True, timeout=5,
        )
        log_cb("  DNS 缓存已 flush（dscacheutil）")
    except Exception as e:
        log_cb(f"  dscacheutil flush 失败: {e}")


def panic_recover(log_cb):
    """Best-effort cleanup."""
    log_cb("=== 网络急救 ===")
    ok, info = set_system_proxy(0, enable=False)
    log_cb(f"  系统代理已清 ({info if ok else 'error: '+info})")
    if helper_installed():
        try:
            r = subprocess.run(
                ["sudo", "-n", HELPER_PATH, str(RUNTIME_DIR), "recover"],
                capture_output=True, text=True, timeout=30,
            )
            log_cb(f"  helper recover: {(r.stdout or r.stderr).strip()}")
        except Exception as e:
            log_cb(f"  helper recover 失败: {e}")
            _flush_dns_cache_unprivileged(log_cb)
    else:
        log_cb("  未安装 sudo helper，跳过 TUN 路由清理")
        _flush_dns_cache_unprivileged(log_cb)
    log_cb("==================")


# ---------- mihomo process manager ----------

class MihomoRunner:
    def __init__(self, log_cb):
        self.proc = None
        self.sudo_pid = None
        self.log_cb = log_cb
        self._tail_thread = None

    def is_running(self):
        if self.proc is not None:
            return self.proc.poll() is None
        if self.sudo_pid is not None:
            try:
                os.kill(self.sudo_pid, 0)
                return True
            except OSError as e:
                if e.errno == errno.EPERM:
                    return True
                self.sudo_pid = None
                return False
        return False

    def attach_existing(self, log_cb=None):
        pid_file = RUNTIME_DIR / "mihomo.pid"
        pid = None
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                try:
                    os.kill(pid, 0)
                except OSError as e:
                    if e.errno != errno.EPERM:
                        raise
            except (ValueError, OSError):
                try:
                    pid_file.unlink()
                except OSError:
                    pass
                pid = None
        if pid is None:
            try:
                out = subprocess.check_output(
                    ["pgrep", "-f", f"mihomo -d {RUNTIME_DIR}"],
                    text=True,
                )
                lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
                if lines:
                    pid = int(lines[0])
                    if log_cb:
                        log_cb(f"⚠ 发现孤儿 mihomo 进程 pid={pid}，已接管管理权")
            except (subprocess.CalledProcessError, ValueError):
                pid = None
        if pid is None:
            return False
        self.sudo_pid = pid
        self._tail_thread = threading.Thread(target=self._tail, daemon=True)
        self._tail_thread.start()
        if log_cb:
            log_cb(f"已接管 mihomo (pid={pid})")
        return True

    def start(self, mihomo_bin, use_sudo=False):
        if self.is_running():
            return
        MIHOMO_LOG.write_text("")
        if use_sudo:
            self._start_sudo(mihomo_bin)
        else:
            f = open(MIHOMO_LOG, "a")
            self.proc = subprocess.Popen(
                [mihomo_bin, "-d", str(RUNTIME_DIR), "-f", str(MIHOMO_YAML)],
                stdout=f, stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            self.log_cb(f"mihomo started (pid={self.proc.pid})")
        self._tail_thread = threading.Thread(target=self._tail, daemon=True)
        self._tail_thread.start()

    def _start_sudo(self, mihomo_bin):
        if not helper_installed():
            self.log_cb("首次开启 TUN：安装 sudoers 助手（只需输一次密码）…")
            install_helper()
            self.log_cb("已安装 /usr/local/bin/chainproxy-helper.sh + 免密 sudo 规则")

        result = subprocess.run(
            ["sudo", "-n", HELPER_PATH, str(RUNTIME_DIR), "start", str(MIHOMO_YAML)],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            if "password is required" in err.lower() or "a terminal" in err.lower():
                self.log_cb("免密规则缺失，重新安装…")
                install_helper()
                result = subprocess.run(
                    ["sudo", "-n", HELPER_PATH, str(RUNTIME_DIR), "start", str(MIHOMO_YAML)],
                    capture_output=True, text=True, timeout=20,
                )
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or "").strip() or "helper start failed")
            else:
                raise RuntimeError(err or "helper start failed")
        m = re.search(r"pid=(\d+)", result.stdout)
        if not m:
            raise RuntimeError(f"helper 输出异常：{result.stdout.strip()!r}")
        self.sudo_pid = int(m.group(1))
        self.log_cb(f"mihomo started as root (pid={self.sudo_pid})")

    def stop(self):
        if self.proc is not None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                for _ in range(20):
                    if self.proc.poll() is not None:
                        break
                    time.sleep(0.1)
                if self.proc.poll() is None:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception as e:
                self.log_cb(f"stop error: {e}")
            self.proc = None
            self.log_cb("mihomo stopped")
        elif self.sudo_pid is not None:
            try:
                subprocess.run(
                    ["sudo", "-n", HELPER_PATH, str(RUNTIME_DIR), "stop"],
                    capture_output=True, text=True, timeout=15,
                )
            except Exception as e:
                self.log_cb(f"sudo stop error: {e}")
            self.sudo_pid = None
            self.log_cb("mihomo stopped (root)")

    def _tail(self):
        try:
            with open(MIHOMO_LOG, "r") as f:
                f.seek(0, 2)
                while self.is_running():
                    line = f.readline()
                    if not line:
                        time.sleep(0.3)
                        continue
                    self.log_cb(line.rstrip())
        except Exception:
            pass


# ---------- single-instance + window activation ----------

def acquire_single_instance_lock():
    """fcntl flock on a file in SUPPORT_DIR. Returns the open fd on success
    (caller must keep it alive), or None if another instance holds it."""
    import fcntl
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = SUPPORT_DIR / ".gui.lock"
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except OSError:
        fd.close()
        return None


def activate_existing_window():
    try:
        subprocess.Popen([
            "osascript", "-e",
            'tell application "ChainProxy" to activate',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# Platform identifier consumed by the GUI for cosmetic differences
PLATFORM_LABEL = "macOS"


__all__ = [
    # constants
    "APP_NAME", "PROTOCOLS", "SS_CIPHERS", "FAKE_GATEWAY",
    "RULE_TARGETS", "LOYALSOLDIER_BASE", "DEFAULT_RULE_SETS",
    "DEFAULT_CONFIG", "PLATFORM_LABEL",
    # paths
    "SUPPORT_DIR", "CONFIG_PATH", "RUNTIME_DIR", "MIHOMO_YAML",
    "MIHOMO_LOG", "RULESET_DIR", "MIHOMO_BIN_CANDIDATES",
    # config / rule-set IO
    "load_config", "save_config", "download_rule_set",
    "update_all_rule_sets", "rule_set_local_path_exists",
    # mihomo + yaml
    "build_mihomo_yaml", "proxy_to_mihomo", "find_mihomo",
    # network probes / tests
    "tcp_reachable", "test_url_through_proxy",
    # platform: system proxy / panic / runner / single instance
    "set_system_proxy", "panic_recover", "MihomoRunner",
    "acquire_single_instance_lock", "activate_existing_window",
    "get_active_network_service",
]
