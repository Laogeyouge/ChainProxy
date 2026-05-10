"""Windows-specific backend.

Mirrors the macOS API exactly. Differences from macOS:
  - System proxy: WinINet registry under HKCU + InternetSetOption refresh
    (no per-service split — Windows proxy is global)
  - TUN: every start triggers a UAC prompt (no equivalent of sudoers NOPASSWD)
  - Process management: CREATE_NEW_PROCESS_GROUP + taskkill /T /F for tree kill
  - Single instance: named Mutex via win32event
  - Window activation: win32gui.FindWindow + SetForegroundWindow
  - Paths: %APPDATA%\\ChainProxy

The first-hop port stays at 6666 in the example config — same as macOS.
"""

import ctypes
import ctypes.wintypes
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import _common
from ._common import (
    APP_NAME, PROTOCOLS, SS_CIPHERS, FAKE_GATEWAY, RULE_TARGETS,
    LOYALSOLDIER_BASE, DEFAULT_RULE_SETS, DEFAULT_CONFIG,
    proxy_to_mihomo, build_mihomo_yaml as _build_mihomo_yaml_raw,
    tcp_reachable, name_should_skip,
)


# ---------- paths ----------
# %APPDATA% is the right place for per-user app data on Windows; it roams with
# the user profile on domain accounts. LOCALAPPDATA would be machine-local.
_APPDATA = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
SUPPORT_DIR = Path(_APPDATA) / APP_NAME
CONFIG_PATH = SUPPORT_DIR / "config.json"
RUNTIME_DIR = SUPPORT_DIR / "runtime"
MIHOMO_YAML = RUNTIME_DIR / "config.yaml"
MIHOMO_LOG = RUNTIME_DIR / "mihomo.log"
RULESET_DIR = RUNTIME_DIR / "ruleset"

# Bundled-mihomo location (PyInstaller). PyInstaller 6 onedir puts our
# bundled binaries under <exedir>\_internal\, while older versions / a
# manual copy may sit alongside the exe at <exedir>\. Check both, and also
# sys._MEIPASS for one-file builds.
_BUNDLED_CANDIDATES = []
if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).parent
    _BUNDLED_CANDIDATES = [
        _exe_dir / "mihomo.exe",
        _exe_dir / "_internal" / "mihomo.exe",
    ]
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        _BUNDLED_CANDIDATES.append(Path(_meipass) / "mihomo.exe")

MIHOMO_BIN_CANDIDATES = [str(p) for p in _BUNDLED_CANDIDATES] + [
    str(Path(_APPDATA) / APP_NAME / "mihomo.exe"),
    r"C:\Program Files\mihomo\mihomo.exe",
    r"C:\ProgramData\chocolatey\bin\mihomo.exe",
    str(Path.home() / "scoop/apps/mihomo/current/mihomo.exe"),
    shutil.which("mihomo") or "",
    shutil.which("mihomo.exe") or "",
]

# Bundled geodata (Country.mmdb / geoip.dat / geosite.dat) — see seed_geodata.
# PyInstaller --onedir puts datas under <exedir>\_internal\geodata, while
# --onefile expands to sys._MEIPASS\geodata. Source-mode dev runs look at
# scripts\geodata next to the repo.
def _find_bundled_geodata_dir():
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates.append(exe_dir / "geodata")
        candidates.append(exe_dir / "_internal" / "geodata")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "geodata")
    # Source-mode: repo root (scripts/geodata)
    here = Path(__file__).resolve()
    candidates.append(here.parent.parent / "scripts" / "geodata")
    for c in candidates:
        if c.is_dir():
            return c
    return None


BUNDLED_GEODATA_DIR = _find_bundled_geodata_dir()


# ---------- config & rule-set wrappers (inject paths) ----------

def load_config():
    return _common.load_config(CONFIG_PATH, SUPPORT_DIR, RUNTIME_DIR, RULESET_DIR)


def save_config(cfg):
    _common.save_config(cfg, CONFIG_PATH, SUPPORT_DIR)


def atomic_write_text(path, content, encoding="utf-8"):
    """Pass-through to common helper. Same surface as macOS — used by the
    GUI for mihomo.yaml."""
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
# When the user's first hop is 127.0.0.1:N we need to mark the airport
# client's processes as DIRECT in mihomo so its outbound dial isn't recaptured
# by our TUN. This used to require the user to know what an "代理引擎" is and
# manually edit JSON. Now we look up which PID owns the listening socket on
# port N, walk one level up (parent — the GUI launcher) and one level down
# (children — the proxy engine spawned by the GUI), and propose those .exe
# names.

def _ps_run(cmd, timeout=6):
    """Run a PowerShell command; return stdout text or '' on failure."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return ""
    return r.stdout or ""


def _listening_pid_powershell(port):
    """Return the OwningProcess of whatever is LISTENing on TCP `port`,
    preferring the loopback binding."""
    out = _ps_run(
        f"Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
        f"-ErrorAction SilentlyContinue | "
        f"Select-Object -Property LocalAddress,OwningProcess | "
        f"Format-Table -HideTableHeaders | Out-String"
    )
    candidates = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        addr, pid_s = parts[0], parts[-1]
        if not pid_s.isdigit():
            continue
        candidates.append((addr, int(pid_s)))
    if not candidates:
        return None
    # Prefer 127.0.0.1 / ::1 over 0.0.0.0 / ::; users with multi-binding apps
    # often see one row per address.
    for addr, pid in candidates:
        if addr in ("127.0.0.1", "::1"):
            return pid
    return candidates[0][1]


def _listening_pid_netstat(port):
    """Fallback when Get-NetTCPConnection isn't available (very old Windows
    or locked-down environments). netstat is shipped with every Windows."""
    try:
        r = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=6,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return None
    suffix_a = f":{int(port)}"
    candidates = []
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto, local, foreign, state, pid_s = parts[:5]
        if proto.upper() != "TCP" or "LISTEN" not in state.upper():
            continue
        if not local.endswith(suffix_a):
            continue
        if not pid_s.isdigit():
            continue
        candidates.append((local, int(pid_s)))
    if not candidates:
        return None
    # Prefer loopback bindings — same reasoning as above.
    for local, pid in candidates:
        if local.startswith("127.0.0.1:") or local.startswith("[::1]:"):
            return pid
    return candidates[0][1]


def _proc_info(pid):
    """Return (Name, ParentProcessId) for a PID, or (None, None)."""
    out = _ps_run(
        f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' "
        f"-ErrorAction SilentlyContinue | "
        f"Select-Object -ExpandProperty Name -ErrorAction SilentlyContinue"
    )
    name = (out or "").strip().splitlines()[0] if out else ""
    out2 = _ps_run(
        f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' "
        f"-ErrorAction SilentlyContinue).ParentProcessId"
    )
    ppid_s = (out2 or "").strip()
    ppid = int(ppid_s) if ppid_s.isdigit() else None
    return (name or None), ppid


def _children_names(pid):
    """Return list of child .exe names whose ParentProcessId == pid."""
    out = _ps_run(
        f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={int(pid)}' "
        f"-ErrorAction SilentlyContinue | "
        f"Select-Object -ExpandProperty Name -ErrorAction SilentlyContinue"
    )
    names = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return names


def _ps_snapshot_win():
    """Return list[(pid, ppid, name)] for every process. Empty on error."""
    out = _ps_run(
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId, $_.ParentProcessId, $_.Name }"
    )
    procs = []
    for line in (out or "").splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        name = parts[2]
        if name:
            procs.append((pid, ppid, name))
    return procs


def list_airport_client_families():
    """See _macos.list_airport_client_families_mac — same contract.

    Windows-side groups by brand pattern (FastLink / Clash Verge / Karing /
    ...) too, since Windows airport clients also ship products where the
    GUI .exe and proxy-core .exe aren't parent-child (the proxy core is
    sometimes spawned by a Windows service running under SYSTEM).
    """
    procs = _ps_snapshot_win()
    if not procs:
        return []

    name_of = {pid: name for pid, _, name in procs}
    children = {}
    for pid, ppid, _ in procs:
        children.setdefault(ppid, []).append(pid)

    by_brand = {}
    for pid, ppid, name in procs:
        brand = _common.airport_brand_for_name(name)
        if brand:
            by_brand.setdefault(brand, []).append((pid, ppid, name))
    if not by_brand:
        return []

    families = []
    for brand, members in by_brand.items():
        all_pids = set()
        for pid, ppid, _ in members:
            all_pids.add(pid)
            parent_name = name_of.get(ppid)
            if (ppid and ppid > 4 and parent_name
                    and not _common.name_should_skip(parent_name)):
                all_pids.add(ppid)
                for cpid in children.get(ppid, []):
                    all_pids.add(cpid)
            for cpid in children.get(pid, []):
                all_pids.add(cpid)

        names = []
        seen = set()
        for pid in all_pids:
            name = name_of.get(pid)
            if not name or _common.name_should_skip(name):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
        if names:
            families.append({"label": brand, "names": names})

    families.sort(key=lambda f: f["label"].lower())
    return families


def detect_first_hop_processes(host, port):
    """Given a (host, port) of a local first hop, return a deduplicated list
    of .exe names that should be added to first_hop_process_names so TUN
    bypasses them (parent + self + children of the listening process).

    Returns []  when nothing is listening on that port, or detection failed.
    """
    if host not in ("127.0.0.1", "localhost", "::1"):
        return []
    pid = _listening_pid_powershell(port) or _listening_pid_netstat(port)
    if pid:
        names = []
        seen = set()

        def push(name):
            if not name or _common.name_should_skip(name):
                return
            # Normalize case for dedup but preserve user-facing case.
            key = name.lower()
            if key in seen:
                return
            seen.add(key)
            names.append(name)

        self_name, ppid = _proc_info(pid)
        push(self_name)
        if ppid and ppid > 4:  # 0/4 = System idle / System
            parent_name, _ = _proc_info(ppid)
            if parent_name and not _common.name_should_skip(parent_name):
                push(parent_name)
                # When parent is the GUI, also pick up its other children
                # (sibling proxy engines that aren't the listening process).
                for n in _children_names(ppid):
                    push(n)
        # And our own children, in case THIS process is the GUI launcher.
        for n in _children_names(pid):
            push(n)
        return names

    # netstat/Get-NetTCPConnection saw nobody. Same blind-spot as macOS:
    # listeners under SYSTEM (mihomo started via a privileged helper) or
    # other security contexts can be invisible to a non-elevated query. A
    # SOCKS5 handshake via 127.0.0.1 is unrestricted, so use it to confirm
    # the proxy exists, then enumerate plausible airport-client processes
    # by name.
    if not _common.socks5_handshake_succeeds(host, port):
        return []
    families = list_airport_client_families()
    if not families:
        return []
    if len(families) == 1:
        return families[0]["names"]
    # Multiple airport clients running. Caller (GUI) should disambiguate via
    # list_airport_client_families() so the user picks the right one rather
    # than getting an over-broad whitelist (the v1.1.7 bug we hit).
    return []


def test_url_through_proxy(url, local_port, log_path, timeout=15,
                           controller_port=None, controller_secret=None):
    return _common.test_url_through_proxy(
        url, local_port, log_path, "NUL", timeout=timeout,
        controller_port=controller_port,
        controller_secret=controller_secret)


# ---------- mihomo binary ----------

def find_mihomo():
    for p in MIHOMO_BIN_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


# ---------- elevation helpers ----------

def is_elevated():
    """Returns True if the current process is running as Administrator."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated(extra_args=None):
    """Re-spawn the current executable with the 'runas' verb (UAC prompt),
    propagating the original argv. Returns True on success (caller should
    immediately exit), False if the user cancelled UAC or the relaunch
    couldn't be initiated.

    Caller convention: only invoke when running from a frozen build
    (sys.frozen) or after `python script.py` — for source runs we relaunch
    the python interpreter with the script path."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = " ".join(_quote_arg(a) for a in (extra_args or sys.argv[1:]))
    else:
        # Running from source: relaunch python.exe with our script
        exe = sys.executable
        script = os.path.abspath(sys.argv[0])
        args = list(extra_args or sys.argv[1:])
        params = " ".join(_quote_arg(a) for a in [script] + args)

    SW_SHOWNORMAL = 1
    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", exe, params, None, SW_SHOWNORMAL,
    )
    # Return values >32 are HINSTANCE (success); <=32 are error codes.
    # 1223 = ERROR_CANCELLED.
    return ret > 32


def _quote_arg(s):
    """Quote an argument for ShellExecute's space-separated params string."""
    s = str(s)
    if not s or any(c.isspace() for c in s) or '"' in s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


# ---------- Windows system proxy (registry + WinINet refresh) ----------

# Internet Settings registry path under HKCU. Writes to HKCU don't need admin.
_PROXY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_INTERNET_OPTION_SETTINGS_CHANGED = 39
_INTERNET_OPTION_REFRESH = 37
# Default ProxyOverride we write while we own the proxy. Bypasses localhost
# and RFC1918 ranges so the GUI's own connections (controller :9999, the
# first hop at 127.0.0.1:6666) don't loop through ourselves.
_DEFAULT_BYPASS = (
    "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;"
    "172.20.*;172.21.*;172.22.*;172.23.*;172.24.*;172.25.*;"
    "172.26.*;172.27.*;172.28.*;172.29.*;172.30.*;172.31.*;192.168.*;<local>"
)


def _refresh_wininet():
    """Tell apps using WinINet (Edge, Chrome, IE, .NET, etc.) that the proxy
    settings just changed. Without this they cache stale values until restart."""
    try:
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, _INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, _INTERNET_OPTION_REFRESH, 0, 0)
    except OSError:
        pass


def refresh_system_proxy():
    """Cross-platform hook the GUI calls after a brief sleep/wake to make
    sure the OS's per-app proxy state is consistent with the registry.

    On Windows, WinINET caches proxy values per process; after wake, some
    apps (especially long-running browser tabs) hold the cache from before
    sleep, causing requests to bypass mihomo until the app is restarted.
    Reapplying SETTINGS_CHANGED + REFRESH kicks WinINET to re-read the
    registry, which already points at our local port. Idempotent and
    cheap (~1 ms) — safe to call from the watchdog tier-1 path.

    On macOS this is a no-op (the system proxy is per-service via
    networksetup, with no per-process cache that needs flushing)."""
    _refresh_wininet()


def _proxy_backup_path():
    """File we stash the user's pre-ChainProxy proxy values into. Living
    under SUPPORT_DIR means it survives across GUI restarts but isn't shared
    between user accounts."""
    return SUPPORT_DIR / ".proxy_backup.json"


def _read_proxy_state():
    import winreg
    state = {"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PROXY_KEY) as k:
            for name, default in (("ProxyEnable", 0),
                                  ("ProxyServer", ""),
                                  ("ProxyOverride", "")):
                try:
                    v, _ = winreg.QueryValueEx(k, name)
                    state[name] = v
                except FileNotFoundError:
                    state[name] = default
    except OSError:
        pass
    return state


def set_system_proxy(port, enable):
    """Toggle the per-user system proxy.

    Behaviour:
      - enable=True: snapshot the user's current ProxyEnable / ProxyServer /
        ProxyOverride to disk (only if no snapshot exists yet — a second
        enable does NOT overwrite the original snapshot), then write
        127.0.0.1:<port> with our default bypass.
      - enable=False: restore the snapshot if we have one (first call after
        an enable), otherwise force-clear ProxyEnable + ProxyServer +
        ProxyOverride. Either way, after a successful disable the snapshot
        is consumed so we never restore stale values on a future cycle.

    This means: after `set_system_proxy(0, False)` the user is guaranteed
    to be in a clean state — either back to their original config, or with
    everything blanked. Never left pointing at a dead 127.0.0.1:<port>.
    """
    import winreg, json
    backup_path = _proxy_backup_path()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PROXY_KEY, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
            if enable:
                # 1. Snapshot the current state. We take a fresh snapshot if
                # one doesn't exist, OR if the existing snapshot is stale
                # (a third party — Clash/V2Ray/manual edit — has taken over
                # the registry since our previous crash, so the snapshot no
                # longer represents the user's real "before-ChainProxy" state).
                #
                # Detection: our _DEFAULT_BYPASS string is distinctive enough
                # that no other proxy client would write the exact same value.
                # If the current ProxyOverride matches it, we're still in
                # control — preserve the existing snapshot. Otherwise the
                # registry has been modified since we last enabled, and the
                # snapshot is stale.
                want_str = f"127.0.0.1:{int(port)}"
                cur = _read_proxy_state()
                cur_is_ours = (
                    str(cur.get("ProxyOverride", "")) == _DEFAULT_BYPASS
                    and str(cur.get("ProxyServer", "")).startswith("127.0.0.1:")
                )
                if not backup_path.exists() or not cur_is_ours:
                    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
                    backup_path.write_text(
                        json.dumps(cur, ensure_ascii=False),
                        encoding="utf-8",
                    )
                # 2. Write our values
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, want_str)
                winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ,
                                  _DEFAULT_BYPASS)
            else:
                # 1. Try to restore the snapshot
                restored = False
                if backup_path.exists():
                    try:
                        snap = json.loads(backup_path.read_text(encoding="utf-8"))
                        winreg.SetValueEx(k, "ProxyEnable", 0,
                                          winreg.REG_DWORD,
                                          int(snap.get("ProxyEnable", 0)))
                        # If the original ProxyServer was empty, DELETE the
                        # value rather than writing "". Same logic as the
                        # force-clear path: a literal empty string confuses
                        # third-party clients that only check ProxyEnable
                        # before rewriting ProxyServer.
                        srv = str(snap.get("ProxyServer", ""))
                        if srv:
                            winreg.SetValueEx(k, "ProxyServer", 0,
                                              winreg.REG_SZ, srv)
                        else:
                            try:
                                winreg.DeleteValue(k, "ProxyServer")
                            except FileNotFoundError:
                                pass
                        # ProxyOverride may not have existed originally
                        ov = snap.get("ProxyOverride", "")
                        if ov:
                            winreg.SetValueEx(k, "ProxyOverride", 0,
                                              winreg.REG_SZ, ov)
                        else:
                            try:
                                winreg.DeleteValue(k, "ProxyOverride")
                            except FileNotFoundError:
                                pass
                        restored = True
                    except Exception:
                        # Snapshot corrupt — fall through to force-clear
                        restored = False
                    finally:
                        try:
                            backup_path.unlink()
                        except OSError:
                            pass
                # 2. No snapshot OR snapshot was corrupt: force-clear.
                # Delete ProxyServer + ProxyOverride entirely rather than
                # blanking them — leaving "ProxyServer=''" can confuse
                # third-party proxy clients that read the value but only
                # rewrite ProxyEnable.
                if not restored:
                    winreg.SetValueEx(k, "ProxyEnable", 0,
                                      winreg.REG_DWORD, 0)
                    for name in ("ProxyServer", "ProxyOverride"):
                        try:
                            winreg.DeleteValue(k, name)
                        except FileNotFoundError:
                            pass
        _refresh_wininet()
        if enable:
            return True, "HKCU registry"
        return True, "restored" if restored else "cleared"
    except OSError as e:
        return False, str(e)


def panic_recover(log_cb):
    """Best-effort cleanup: forcibly clear the system proxy registry
    (regardless of whether we have a snapshot), kill any mihomo we know
    about, and ask Windows to pick up the proxy change. Used when the
    user's network is in a half-broken state and we don't want to be
    'polite' about restoring previous values — just nuke everything that
    could route traffic to a dead port."""
    import winreg
    log_cb("=== 网络急救 ===")

    # 1. Force-clear the registry, no matter what's in there. This is the
    # only thing standing between the user and a working network. Delete
    # the value names entirely so the registry is in a known-clean state
    # — third-party clients will rewrite them when they start.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PROXY_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            for name in ("ProxyServer", "ProxyOverride", "AutoConfigURL"):
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
        _refresh_wininet()
        log_cb("  系统代理已强制清空")
    except OSError as e:
        log_cb(f"  清注册表失败: {e}")

    # 2. Drop our backup so a future enable doesn't try to restore stale
    # values from a previous incident.
    try:
        bp = _proxy_backup_path()
        if bp.exists():
            bp.unlink()
            log_cb("  代理备份已清除")
    except OSError:
        pass

    # 3. Kill any mihomo.exe. /T also kills children, /F forces.
    #    If a TUN-mode mihomo is elevated, normal taskkill returns
    #    "access denied" — fall back to ShellExecute('runas', ...) which
    #    triggers a UAC prompt that's elevated enough to kill it.
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/T", "/IM", "mihomo.exe"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (r.stdout or r.stderr or "").strip()
        access_denied = ("access is denied" in out.lower()
                         or "拒绝访问" in out
                         or "无法终止" in out)
        if r.returncode != 0 and access_denied:
            log_cb("  普通 taskkill 被拒（mihomo 已提权），尝试 UAC 提权后重杀…")
            try:
                # Synchronous: ShellExecuteExW with NOCLOSEPROCESS so we can
                # wait for taskkill to finish before reporting back.
                ret = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", "taskkill.exe",
                    "/F /T /IM mihomo.exe", None, 0,  # SW_HIDE
                )
                # ShellExecuteW returns >32 on success (instance handle).
                if ret > 32:
                    log_cb("  UAC 提权 taskkill 已发起")
                    # Give it a moment to actually kill
                    time.sleep(1.0)
                else:
                    log_cb(f"  UAC 授权失败或被取消 (ret={ret})")
            except Exception as e:
                log_cb(f"  UAC taskkill 失败: {e}")
        else:
            log_cb(f"  taskkill mihomo.exe: {out or 'no process'}")
    except Exception as e:
        log_cb(f"  taskkill 失败: {e}")

    # When mihomo exits cleanly its TUN adapter (WinTun) is removed
    # automatically. If it crashed, the WinTun driver state survives until
    # the next mihomo run does its own cleanup. Nothing useful we can do
    # from user-mode here without elevation.
    log_cb("==================")


def kill_orphan_mihomo(log_cb):
    """Best-effort cleanup of any leftover mihomo.exe at app exit. Used by
    _final_cleanup as a safety net after the runner's own stop() path. Does
    NOT pop UAC — exit-time prompts are jarring and easy to miss. If the
    orphan is elevated and we aren't, we just log and give up; the user
    can run 网络急救 from a fresh GUI session to take care of it."""
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/T", "/IM", "mihomo.exe"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (r.stdout or r.stderr or "").strip()
        # taskkill exits 128 when no matching process exists — the common
        # happy case (runner.stop already killed it). Don't log noise for
        # that, but do log access-denied so the bug is visible in app.log.
        if r.returncode == 0:
            log_cb(f"orphan sweep: {out}")
        elif "access is denied" in out.lower() or "拒绝访问" in out:
            log_cb("orphan sweep: 残留 mihomo 已提权，未杀（避免在退出时弹 UAC）")
    except Exception as e:
        log_cb(f"orphan sweep failed: {e}")


def bounce_primary_interface(log_cb):
    """Windows stub. macOS-specific recovery; Windows handles sleep/wake at
    the OS level differently (NDIS reset on resume) and we have no equivalent
    user-mode recovery story. Surface as no-op so cross-platform callers can
    call uniformly."""
    log_cb("  (Windows: 跳过网卡重置)")


# ---------- mihomo process manager ----------

class MihomoRunner:
    """Same surface as the macOS one. Differences:
      - non-TUN: spawn directly, kill via taskkill /T /F
      - TUN: ShellExecuteEx with 'runas' verb to trigger UAC; track PID
        through GetProcessId on the returned process handle
    """
    def __init__(self, log_cb):
        self.proc = None        # Popen (non-TUN)
        self.uac_pid = None     # PID started via UAC elevation
        self._uac_handle = None # SHELLEXECUTEINFO hProcess (kept open)
        self.log_cb = log_cb
        self._tail_thread = None
        self._stop_tail = threading.Event()
        # GUI registers a callback here. Fired (off-thread) when the tail
        # loop notices mihomo died without us calling stop() — i.e. crash.
        # GUI uses this to auto-restart silently. Same surface as macOS
        # MihomoRunner.
        self.on_unexpected_exit = None

    # ---- liveness ----
    def _pid_alive(self, pid):
        if not pid:
            return False
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel = ctypes.windll.kernel32
        h = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = ctypes.wintypes.DWORD()
            ok = kernel.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            kernel.CloseHandle(h)

    def is_running(self):
        if self.proc is not None:
            return self.proc.poll() is None
        if self.uac_pid is not None:
            if self._pid_alive(self.uac_pid):
                return True
            self.uac_pid = None
            return False
        return False

    # ---- attach orphan ----
    def attach_existing(self, log_cb=None):
        """Find any mihomo.exe whose command line references our runtime dir
        and adopt it. Used when the GUI restarts and a previous instance
        left mihomo running."""
        try:
            r = subprocess.run(
                ["wmic", "process", "where",
                 "name='mihomo.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
                capture_output=True, text=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            return False
        runtime_str = str(RUNTIME_DIR)
        adopted = None
        for line in (r.stdout or "").splitlines():
            if not line.strip() or "ProcessId" in line and "CommandLine" in line:
                continue
            # CSV columns vary; we just look for our runtime dir + a numeric
            # PID at the end.
            if runtime_str.lower() in line.lower():
                parts = line.rstrip().split(",")
                # last non-empty cell is PID
                for cell in reversed(parts):
                    cell = cell.strip()
                    if cell.isdigit():
                        adopted = int(cell)
                        break
                if adopted:
                    break
        if not adopted:
            return False
        self.uac_pid = adopted
        self._stop_tail.clear()
        self._tail_thread = threading.Thread(target=self._tail, daemon=True)
        self._tail_thread.start()
        if log_cb:
            log_cb(f"已接管 mihomo (pid={adopted})")
        return True

    # ---- start ----
    def start(self, mihomo_bin, use_sudo=False):
        if self.is_running():
            return
        # Fence off any tail thread from a previous incarnation. Without
        # this, fast stop()→start() cycles overlap two _tail loops on the
        # same log; the GUI receives duplicate lines and the unexpected-exit
        # callback can fire from the previous tail confusingly.
        if self._tail_thread and self._tail_thread.is_alive():
            self._stop_tail.set()
            self._tail_thread.join(timeout=0.5)
            self._tail_thread = None
        # Rotate log if over 10MB. Previously this path truncated on every
        # start, which controlled size but destroyed history — every restart
        # wiped any clue about why mihomo died last session. Now we keep
        # one rotation (.log → .log.1) so recent history survives at least
        # one restart, but the log can never grow unbounded.
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
        # Avoid an extra UAC prompt: if the GUI itself is already running
        # elevated (the recommended setup for TUN mode — see main()'s
        # one-shot relaunch logic), spawn mihomo as a normal child. The
        # child inherits our elevated token; no second UAC prompt.
        if use_sudo and not is_elevated():
            self._start_elevated(mihomo_bin)
        else:
            self._start_direct(mihomo_bin)
        self._stop_tail.clear()
        self._tail_thread = threading.Thread(target=self._tail, daemon=True)
        self._tail_thread.start()

    def _start_direct(self, mihomo_bin):
        # CREATE_NO_WINDOW: avoid a flashing console window when GUI launches
        # mihomo.exe. CREATE_NEW_PROCESS_GROUP: lets us send CTRL_BREAK to it
        # later without affecting the GUI itself.
        f = open(MIHOMO_LOG, "a", encoding="utf-8", errors="replace")
        creationflags = (subprocess.CREATE_NO_WINDOW |
                         subprocess.CREATE_NEW_PROCESS_GROUP)
        self.proc = subprocess.Popen(
            [mihomo_bin, "-d", str(RUNTIME_DIR), "-f", str(MIHOMO_YAML)],
            stdout=f, stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        self.log_cb(f"mihomo started (pid={self.proc.pid})")

    def _start_elevated(self, mihomo_bin):
        """Trigger UAC and launch mihomo.exe as Administrator. Required for
        TUN mode because WinTun driver loading + route table edits both need
        elevation. We capture the resulting PID via SHELLEXECUTEINFO."""
        # SHELLEXECUTEINFOW struct
        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SEE_MASK_NOASYNC        = 0x00000100
        SW_HIDE = 0

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize",       ctypes.wintypes.DWORD),
                ("fMask",        ctypes.c_ulong),
                ("hwnd",         ctypes.wintypes.HWND),
                ("lpVerb",       ctypes.wintypes.LPCWSTR),
                ("lpFile",       ctypes.wintypes.LPCWSTR),
                ("lpParameters", ctypes.wintypes.LPCWSTR),
                ("lpDirectory",  ctypes.wintypes.LPCWSTR),
                ("nShow",        ctypes.c_int),
                ("hInstApp",     ctypes.wintypes.HINSTANCE),
                ("lpIDList",     ctypes.c_void_p),
                ("lpClass",      ctypes.wintypes.LPCWSTR),
                ("hkeyClass",    ctypes.wintypes.HKEY),
                ("dwHotKey",     ctypes.wintypes.DWORD),
                ("hIconOrMonitor", ctypes.wintypes.HANDLE),
                ("hProcess",     ctypes.wintypes.HANDLE),
            ]

        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
        sei.lpVerb = "runas"
        sei.lpFile = mihomo_bin
        # Quote paths in case they contain spaces (e.g. in the AppData path)
        sei.lpParameters = f'-d "{RUNTIME_DIR}" -f "{MIHOMO_YAML}"'
        sei.lpDirectory = str(RUNTIME_DIR)
        sei.nShow = SW_HIDE

        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
        shell32.ShellExecuteExW.restype = ctypes.wintypes.BOOL
        if not shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.GetLastError()
            # 1223 = ERROR_CANCELLED (user clicked No on UAC)
            if err == 1223:
                raise RuntimeError("UAC 授权被取消")
            raise RuntimeError(f"UAC 提权失败 (ShellExecuteExW err={err})")

        kernel32 = ctypes.windll.kernel32
        kernel32.GetProcessId.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.GetProcessId.restype = ctypes.wintypes.DWORD
        pid = kernel32.GetProcessId(sei.hProcess)
        if pid == 0:
            kernel32.CloseHandle(sei.hProcess)
            raise RuntimeError("无法取得已提权 mihomo 的 PID")
        self.uac_pid = int(pid)
        self._uac_handle = sei.hProcess
        self.log_cb(f"mihomo started elevated (pid={self.uac_pid})")

    # ---- stop ----
    def stop(self):
        if self.proc is not None:
            self._stop_tail.set()
            try:
                # Kill the whole tree by PID — taskkill /T handles any child
                # mihomo spawned (mostly nothing, but defensive).
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as e:
                self.log_cb(f"stop error: {e}")
            try:
                self.proc.wait(timeout=3)
            except Exception:
                pass
            self.proc = None
            self.log_cb("mihomo stopped")
        elif self.uac_pid is not None:
            self._stop_tail.set()
            stopped = False
            if is_elevated():
                # GUI is already elevated → plain taskkill is enough. No UAC.
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.uac_pid)],
                        capture_output=True, text=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    stopped = True
                except Exception as e:
                    self.log_cb(f"taskkill 失败: {e}")
            if not stopped:
                # Non-elevated GUI talking to an elevated mihomo: must go
                # through UAC. ShellExecute('runas', 'taskkill', ...) is the
                # only working path. Pops a prompt but unavoidable.
                try:
                    self._elevated_taskkill(self.uac_pid)
                    stopped = True
                except Exception as e:
                    self.log_cb(f"elevated taskkill 失败: {e}")
            # Wait briefly for it to die
            for _ in range(20):
                if not self._pid_alive(self.uac_pid):
                    break
                time.sleep(0.1)
            # Only forget the PID if mihomo actually died. Previously this
            # cleared unconditionally, so a cancelled UAC prompt left mihomo
            # alive but invisible to the runner — _final_cleanup couldn't
            # see it either, and the user found mihomo still in Task Manager.
            if self._pid_alive(self.uac_pid):
                self.log_cb(f"mihomo (pid={self.uac_pid}) 仍在运行 — UAC 取消或 taskkill 失败")
            else:
                self.uac_pid = None
                if self._uac_handle:
                    try:
                        ctypes.windll.kernel32.CloseHandle(self._uac_handle)
                    except Exception:
                        pass
                    self._uac_handle = None
                self.log_cb("mihomo stopped (elevated)")

    def _stop_via_controller(self):
        """Ask mihomo to exit cleanly via its external-controller REST API.
        No elevation required because we just send an HTTP request."""
        try:
            cfg = load_config()
            ctl_port = int(cfg.get("controller_port", 9999))
            secret = cfg.get("controller_secret", "")
            import urllib.request, urllib.error
            req = urllib.request.Request(
                f"http://127.0.0.1:{ctl_port}/restart",
                method="POST",
            )
            if secret:
                req.add_header("Authorization", f"Bearer {secret}")
            # mihomo doesn't have a clean /shutdown endpoint, but PUT to
            # /configs with mode change followed by a restart is awkward.
            # Simpler: try a built-in endpoint if available, else give up.
            try:
                urllib.request.urlopen(req, timeout=2)
            except urllib.error.HTTPError as e:
                # Some versions return 404 for /restart — fall through.
                if e.code != 200:
                    return False
            except Exception:
                return False
            # /restart only restarts mihomo; it doesn't shut down. So this
            # path can't actually stop a running TUN-mode mihomo without
            # elevation. Return False to force the elevated-taskkill path.
            return False
        except Exception:
            return False

    def _elevated_taskkill(self, pid):
        """Run taskkill /F /T /PID <pid> elevated. Triggers a UAC prompt."""
        SW_HIDE = 0
        params = f'/F /T /PID {int(pid)}'
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "taskkill.exe", params, None, SW_HIDE)
        if ret <= 32:
            raise RuntimeError(f"ShellExecute taskkill returned {ret}")

    # ---- log tail ----
    def _tail(self):
        """Stream mihomo's log to log_cb until the process dies or the GUI
        explicitly asks us to stop. If the process dies WITHOUT the GUI
        asking (`_stop_tail` not set), fire on_unexpected_exit so the GUI
        can auto-restart. Same contract as the macOS MihomoRunner._tail.

        Re-stat the path on every idle iteration: log rotation
        (`_rotate_log_if_large` runs on every start) renames mihomo.log →
        mihomo.log.1; without re-stat the tail's fd points at the
        now-quiescent renamed file forever and the user sees the log
        panel freeze. Detects rotation via st_ino mismatch on the path
        and truncation via size going backwards.
        """
        f = None
        try:
            f = open(MIHOMO_LOG, "r", encoding="utf-8", errors="replace")
            f.seek(0, 2)
            cur_inode = os.fstat(f.fileno()).st_ino
            cur_size = f.tell()
            while not self._stop_tail.is_set() and self.is_running():
                try:
                    st = os.stat(MIHOMO_LOG)
                    if st.st_ino != cur_inode or st.st_size < cur_size:
                        try:
                            f.close()
                        except OSError:
                            pass
                        f = open(MIHOMO_LOG, "r",
                                 encoding="utf-8", errors="replace")
                        cur_inode = os.fstat(f.fileno()).st_ino
                        cur_size = 0
                except FileNotFoundError:
                    time.sleep(0.3)
                    continue
                line = f.readline()
                if not line:
                    time.sleep(0.3)
                    continue
                cur_size = f.tell()
                self.log_cb(line.rstrip())
        except Exception:
            pass
        finally:
            if f is not None:
                try:
                    f.close()
                except OSError:
                    pass
        if not self._stop_tail.is_set() and self.on_unexpected_exit:
            try:
                self.on_unexpected_exit()
            except Exception:
                pass


# ---------- single-instance lock + window activation ----------

_MUTEX_NAME = "Global\\ChainProxyGUISingleInstance"
_HMUTEX = [None]  # module-level so the handle outlives the function


def acquire_single_instance_lock():
    """Create a named mutex; if it already exists, another instance is alive.
    The handle stays open for the process lifetime; the OS releases it when
    the process exits.

    Returns a sentinel (the handle int wrapped in a small class with .close())
    on success, or None if another instance holds it.
    """
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    h = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    last_err = ctypes.GetLastError()
    if not h:
        return None
    if last_err == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(h)
        return None
    _HMUTEX[0] = h

    class _Lock:
        def close(self):
            if _HMUTEX[0]:
                try:
                    ctypes.windll.kernel32.CloseHandle(_HMUTEX[0])
                except Exception:
                    pass
                _HMUTEX[0] = None
    return _Lock()


def activate_existing_window():
    """Find the running ChainProxy window and bring it to the foreground."""
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR]
        user32.FindWindowW.restype = ctypes.wintypes.HWND
        hwnd = user32.FindWindowW(None, "ChainProxy")
        if hwnd:
            SW_RESTORE = 9
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


PLATFORM_LABEL = "Windows"


__all__ = [
    "APP_NAME", "PROTOCOLS", "SS_CIPHERS", "FAKE_GATEWAY",
    "RULE_TARGETS", "LOYALSOLDIER_BASE", "DEFAULT_RULE_SETS",
    "DEFAULT_CONFIG", "PLATFORM_LABEL",
    "SUPPORT_DIR", "CONFIG_PATH", "RUNTIME_DIR", "MIHOMO_YAML",
    "MIHOMO_LOG", "RULESET_DIR", "MIHOMO_BIN_CANDIDATES",
    "BUNDLED_GEODATA_DIR",
    "load_config", "save_config", "download_rule_set",
    "update_all_rule_sets", "rule_set_local_path_exists",
    "build_mihomo_yaml", "proxy_to_mihomo", "find_mihomo",
    "seed_geodata", "detect_first_hop_processes",
    "list_airport_client_families", "name_should_skip",
    "tcp_reachable", "test_url_through_proxy",
    "set_system_proxy", "panic_recover", "kill_orphan_mihomo",
    "bounce_primary_interface",
    "refresh_system_proxy",
    "atomic_write_text",
    "MihomoRunner",
    "acquire_single_instance_lock", "activate_existing_window",
    "is_elevated", "relaunch_elevated",
    "_read_proxy_state", "_DEFAULT_BYPASS",
]
