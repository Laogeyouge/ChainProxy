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
    tcp_reachable,
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
        can auto-restart. Same contract as the macOS MihomoRunner._tail."""
        try:
            with open(MIHOMO_LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                while not self._stop_tail.is_set() and self.is_running():
                    line = f.readline()
                    if not line:
                        time.sleep(0.3)
                        continue
                    self.log_cb(line.rstrip())
        except Exception:
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
    "load_config", "save_config", "download_rule_set",
    "update_all_rule_sets", "rule_set_local_path_exists",
    "build_mihomo_yaml", "proxy_to_mihomo", "find_mihomo",
    "tcp_reachable", "test_url_through_proxy",
    "set_system_proxy", "panic_recover", "bounce_primary_interface",
    "atomic_write_text",
    "MihomoRunner",
    "acquire_single_instance_lock", "activate_existing_window",
    "is_elevated", "relaunch_elevated",
    "_read_proxy_state", "_DEFAULT_BYPASS",
]
