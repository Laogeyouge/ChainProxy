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

# Bundled geodata dir (Country.mmdb / geoip.dat / geosite.dat) — see
# seed_geodata in _common.py for the rationale.
def _find_bundled_geodata_dir():
    here = Path(__file__).resolve()
    candidates = [
        # When installed as ChainProxy.app, _macos.py lives at
        # ChainProxy.app/Contents/Resources/core/_macos.py
        here.parent.parent / "geodata",
        # Source-mode dev runs
        here.parent.parent / "scripts" / "geodata",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


BUNDLED_GEODATA_DIR = _find_bundled_geodata_dir()

# ---------- helper-script for TUN privilege ----------
HELPER_PATH = "/usr/local/bin/chainproxy-helper.sh"
SUDOERS_PATH = "/etc/sudoers.d/chainproxy"
HELPER_VERSION = "8"
HELPER_SCRIPT = r"""#!/bin/bash
# version: 8
# ChainProxy mihomo helper. Managed by ChainProxy.app — DO NOT EDIT.
# Args: <runtime-dir> <action: start|stop|status|recover|flush-dns|bounce-iface> [yaml-path]
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

# Why: another local mihomo-based client (Clash/ClashX/Stash/Mihomo Party)
# defaults to the same fakeip gateway 198.18.0.1 and installs the same
# split-half routes. If we naively `start` while it owns those routes,
# `cleanup_routes` deletes ITS routes (breaking it briefly), then mihomo
# auto-route reinstalls them via OUR utun. The other client's monitor sees
# its routes vanish and reinstalls via ITS utun. Two daemons fight, route
# table flips every few seconds, every dial breaks.
#
# Detection: this runs AFTER cleanup_routes + cleanup_utun. If 198.18.0.1
# is still (or already again) routed to some utun, that route was put back
# by a live foreign daemon — we just deleted ours. We sleep briefly so a
# foreign daemon's monitor has time to re-install before we sample.
preflight_check() {
  sleep 1
  iface=$(/usr/sbin/netstat -rn -f inet 2>/dev/null \
            | awk '$1=="198.18.0.1" && $2=="198.18.0.1" {print $NF; exit}')
  [ -z "$iface" ] && return 0
  case "$iface" in
    utun*)
      echo "ERROR: 198.18.0.1 is already in use by another proxy on $iface." >&2
      echo "Close that proxy client first (Clash/ClashX/Stash/Mihomo Party等)，" >&2
      echo "再启动 ChainProxy。两个 TUN 共用同一 fakeip 网关会互相破坏路由。" >&2
      exit 3
      ;;
  esac
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

# Why: macOS sleep/wake can leave the primary interface in a wedged state where
# every outbound dial returns "connection refused" or i/o timeout — even after
# mihomo restarts with fresh internal state. The TUN sees a default interface
# (en0), but the kernel route through it is broken at L3 until the interface
# is bounced. This is the failure mode where "网络急救 doesn't work" and the
# user reaches for a reboot. Bouncing the primary interface forces ARP/route
# refresh and (for Wi-Fi) re-association, restoring the underlying path that
# mihomo's auto-route depends on. We also re-acquire DHCP because some upstream
# routers expire the lease during sleep.
bounce_iface() {
  iface=$(/sbin/route -n get default 2>/dev/null \
            | awk '/interface:/ {print $2; exit}')
  [ -z "$iface" ] && iface="en0"
  echo "  bouncing $iface" >&2
  /sbin/ifconfig "$iface" down 2>/dev/null || true
  sleep 1
  /sbin/ifconfig "$iface" up 2>/dev/null || true
  /usr/sbin/ipconfig set "$iface" DHCP 2>/dev/null || true
  echo "bounced $iface"
}

kill_orphans() {
  pkill -TERM -f "mihomo -d $RUNTIME" 2>/dev/null || true
  for i in $(seq 1 15); do
    pgrep -f "mihomo -d $RUNTIME" >/dev/null 2>&1 || return 0
    sleep 0.2
  done
  pkill -KILL -f "mihomo -d $RUNTIME" 2>/dev/null || true
}

# Why: nohup mihomo … >> "$LOG" appends forever; the file never gets rotated
# in the helper's spawn path, and after months of use it grows into the
# gigabytes (one bug-report had a 4GB mihomo.log on a developer machine).
# Single-rotation is the simplest fix that doesn't add operational surface:
# rename to .1 once over 10MB, never compress, never multi-generation.
# /usr/bin/stat is BSD on macOS, -f%z = file size in bytes.
rotate_log_if_large() {
  if [ -f "$LOG" ]; then
    sz=$(/usr/bin/stat -f%z "$LOG" 2>/dev/null || echo 0)
    if [ "$sz" -gt 10485760 ]; then
      mv -f "$LOG" "$LOG.1" 2>/dev/null || true
    fi
  fi
}

case "$ACTION" in
  start)
    kill_orphans
    cleanup_routes
    cleanup_utun
    flush_dns
    preflight_check
    rotate_log_if_large
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
    # Why flush_dns first: macOS's resolver caches our fakeip answers
    # (claude.ai → 198.18.0.x). If we kill mihomo first, then flush, there's
    # a 200ms-1s window where apps re-resolve, hit cache, get a fakeip whose
    # listener is gone → "connection refused" surfaces to the user. Flushing
    # BEFORE kill ensures the next resolve goes to a real upstream resolver
    # (e.g. 119.29.29.29) which returns the real IP. Second flush after kill
    # purges any entry that snuck in during the kill window.
    flush_dns
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
    flush_dns
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
  bounce-iface)
    bounce_iface
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
    echo "usage: $0 RUNTIME start|stop|status|recover|flush-dns|bounce-iface [YAML]" >&2
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


def atomic_write_text(path, content, encoding="utf-8"):
    """Pass-through to common helper. Exposed so the GUI can write
    mihomo.yaml atomically (the same way save_config writes config.json)."""
    _common.atomic_write_text(path, content, encoding=encoding)


def download_rule_set(rs, timeout=20):
    return _common.download_rule_set(rs, RULESET_DIR, timeout=timeout)


def update_all_rule_sets(cfg, log_cb):
    return _common.update_all_rule_sets(
        cfg, RULESET_DIR, CONFIG_PATH, SUPPORT_DIR, log_cb)


def rule_set_local_path_exists(name):
    return _common.rule_set_local_path_exists(name, RULESET_DIR)


def build_mihomo_yaml(cfg):
    return _build_mihomo_yaml_raw(cfg, RULESET_DIR)


def seed_geodata(log_cb=None):
    return _common.seed_geodata(BUNDLED_GEODATA_DIR, RUNTIME_DIR, log_cb=log_cb)


# ---------- process tree detection (TUN loop-prevention helper) ----------
#
# Mirrors the Windows helper. macOS mihomo also supports PROCESS-NAME rules,
# so when the user's first hop is on 127.0.0.1 we auto-fill
# first_hop_process_names from the PID listening on that port.

def _listening_pid_lsof(port):
    """Return the PID LISTENing on TCP `port`, prefer loopback bindings."""
    try:
        r = subprocess.run(
            ["lsof", "-nP", "-iTCP:" + str(int(port)), "-sTCP:LISTEN", "-F", "pn"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    # lsof -F output: lines start with field-tag chars. 'p' = PID, 'n' = name.
    # Records group by `p<pid>` followed by zero or more 'f'/'n' lines until
    # the next 'p'. We pick the loopback row's PID, fall back to first.
    pid = None
    loopback_pid = None
    for line in (r.stdout or "").splitlines():
        if not line:
            continue
        tag, val = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(val)
            except ValueError:
                pid = None
        elif tag == "n" and pid is not None:
            if "127.0.0.1:" in val or "[::1]:" in val:
                loopback_pid = pid
    return loopback_pid or pid


def _proc_info_mac(pid):
    """Return (process name, parent PID) for `pid`, or (None, None)."""
    try:
        r = subprocess.run(
            ["ps", "-o", "comm=,ppid=", "-p", str(int(pid))],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return (None, None)
    line = (r.stdout or "").strip()
    if not line:
        return (None, None)
    # ps prints: "/path/to/exe 1234"  — last token is ppid, rest is comm.
    parts = line.rsplit(None, 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return (None, None)
    comm = os.path.basename(parts[0])
    return (comm or None, int(parts[1]))


def _children_names_mac(pid):
    """Return basename of every process whose PPID == pid."""
    try:
        r = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            capture_output=True, text=True, timeout=4,
        )
    except Exception:
        return []
    names = []
    for line in (r.stdout or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            ppid = int(parts[1])
        except ValueError:
            continue
        if ppid != int(pid):
            continue
        names.append(os.path.basename(parts[2]))
    return names


def detect_first_hop_processes(host, port):
    """See _windows.detect_first_hop_processes — same contract."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        return []
    pid = _listening_pid_lsof(port)
    if not pid:
        return []
    names = []
    seen = set()

    def push(name):
        if not name:
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        names.append(name)

    self_name, ppid = _proc_info_mac(pid)
    push(self_name)
    if ppid and ppid > 1:  # 1 = launchd
        parent_name, _ = _proc_info_mac(ppid)
        # Skip launcher shells / launchd children — not the airport client.
        if parent_name and parent_name.lower() not in (
                "launchd", "bash", "zsh", "sh", "login", "terminal"):
            push(parent_name)
            for n in _children_names_mac(ppid):
                push(n)
    for n in _children_names_mac(pid):
        push(n)
    return names


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


def refresh_system_proxy():
    """Cross-platform hook (paired with the Windows implementation). On
    macOS, networksetup writes per-service config that's read live by
    apps; there's no WinINET-style cache to flush. No-op."""
    pass


def bounce_primary_interface(log_cb):
    """Bounce the macOS primary network interface. The helper auto-detects
    en0/en1/etc. from the default route and runs ifconfig down/up + DHCP
    renew. Required after sleep/wake when L3 is wedged — restarting mihomo
    alone can't fix that, the kernel needs to refresh the route entries."""
    if not helper_installed():
        raise RuntimeError("sudo helper 未安装，无法重置网卡")
    r = subprocess.run(
        ["sudo", "-n", HELPER_PATH, str(RUNTIME_DIR), "bounce-iface"],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"bounce-iface failed (exit {r.returncode}): "
            f"{(r.stderr or r.stdout or '').strip()}"
        )
    out = (r.stdout or r.stderr or "").strip()
    if out:
        log_cb(f"  {out}")


# ---------- mihomo process manager ----------

class MihomoRunner:
    def __init__(self, log_cb):
        self.proc = None
        self.sudo_pid = None
        self.log_cb = log_cb
        self._tail_thread = None
        self._tail_stop = threading.Event()
        # GUI registers a callback here. Fired (off-thread) when the tail
        # loop notices mihomo died without us calling stop() — i.e. crash,
        # OOM, panic. The GUI uses this to auto-restart silently.
        self.on_unexpected_exit = None

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
        # Fence off any tail thread from a previous incarnation. Without
        # this, fast stop()→start() cycles overlap two _tail loops on the
        # same log; the GUI receives duplicate lines and the watchdog's
        # is_running()-driven exit detection sees the OLD thread's poll
        # confusing the OLD process state with the NEW one.
        self._stop_tail_thread()
        # Rotate large logs (size-based, single .1 generation). Why not
        # truncate every restart: previously tracking down "why did mihomo
        # die last session" required catching it live; now one restart of
        # history survives.
        self._rotate_log_if_large()
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
        self._tail_stop.clear()
        self._tail_thread = threading.Thread(target=self._tail, daemon=True)
        self._tail_thread.start()

    @staticmethod
    def _rotate_log_if_large():
        try:
            if MIHOMO_LOG.exists() and MIHOMO_LOG.stat().st_size > 10 * 1024 * 1024:
                rotated = MIHOMO_LOG.with_suffix(".log.1")
                try:
                    rotated.unlink()
                except (OSError, FileNotFoundError):
                    pass
                MIHOMO_LOG.rename(rotated)
        except OSError:
            pass

    def _stop_tail_thread(self):
        if self._tail_thread and self._tail_thread.is_alive():
            self._tail_stop.set()
            self._tail_thread.join(timeout=0.5)
        self._tail_thread = None
        self._tail_stop.clear()

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
            # Exit 3 = preflight detected another proxy owning 198.18.0.1.
            # Surface the helper's message verbatim — it already explains why.
            if result.returncode == 3:
                raise RuntimeError(err or "另一个代理客户端正占用 TUN 网关 198.18.0.1")
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
        # Tail thread observes is_running()=False on its next poll and exits;
        # tell it to stop NOW so we don't wait up to 0.3s for that poll.
        self._tail_stop.set()

    def _tail(self):
        """Stream mihomo's log to log_cb until either the process dies or
        the GUI explicitly asks us to stop. If the process dies WITHOUT
        the GUI asking (`_tail_stop` not set), fire on_unexpected_exit so
        the GUI can auto-restart."""
        try:
            with open(MIHOMO_LOG, "r") as f:
                f.seek(0, 2)
                while not self._tail_stop.is_set():
                    if not self.is_running():
                        break
                    line = f.readline()
                    if not line:
                        time.sleep(0.3)
                        continue
                    self.log_cb(line.rstrip())
        except Exception:
            pass
        # Distinguish "we asked it to stop" from "it died on us". Only
        # the latter triggers auto-restart.
        if not self._tail_stop.is_set() and self.on_unexpected_exit:
            try:
                self.on_unexpected_exit()
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
    "BUNDLED_GEODATA_DIR",
    # config / rule-set IO
    "load_config", "save_config", "download_rule_set",
    "update_all_rule_sets", "rule_set_local_path_exists",
    # mihomo + yaml
    "build_mihomo_yaml", "proxy_to_mihomo", "find_mihomo",
    "seed_geodata", "detect_first_hop_processes",
    # network probes / tests
    "tcp_reachable", "test_url_through_proxy",
    # platform: system proxy / panic / runner / single instance
    "set_system_proxy", "panic_recover", "bounce_primary_interface",
    "refresh_system_proxy",
    "atomic_write_text",
    "MihomoRunner",
    "acquire_single_instance_lock", "activate_existing_window",
    "get_active_network_service",
]
