#!/usr/bin/env python3
"""
ChainProxy core — backend logic shared by any UI.

Contains: config schema, mihomo yaml builder, helper-script management,
process runner, system-proxy control, URL test, panic recover.
"""

import errno
import getpass
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "ChainProxy"
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

PROTOCOLS = ["socks5", "http", "trojan", "ss", "vmess", "hysteria2"]
SS_CIPHERS = ["aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "2022-blake3-aes-256-gcm"]

# Privileged helper for TUN mode. Installed once with admin password,
# afterwards invoked via passwordless sudo (sudoers entry below).
HELPER_PATH = "/usr/local/bin/chainproxy-helper.sh"
SUDOERS_PATH = "/etc/sudoers.d/chainproxy"
HELPER_VERSION = "4"
# Mihomo's default fake-ip / TUN gateway. We clean up any leaked routes
# pointing at this gateway during stop/recover.
FAKE_GATEWAY = "198.18.0.1"
HELPER_SCRIPT = r"""#!/bin/bash
# version: 4
# ChainProxy mihomo helper. Managed by ChainProxy.app — DO NOT EDIT.
# Args: <runtime-dir> <action: start|stop|status|recover> [yaml-path]
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

# kill ANY mihomo run with our config (matches by argv contents); covers the
# case where the PID file is missing or wrong. Excludes verge-mihomo etc.
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
    # 1. Reap any orphan mihomo running with our runtime dir
    kill_orphans
    # 2. Clean leftover TUN state — without this, mihomo's own TUN setup
    #    fails with "add route: 1.0.0.0/8: file exists" and TUN silently
    #    doesn't work even though mihomo keeps running.
    cleanup_routes
    cleanup_utun
    rm -f "$PIDFILE"
    # 3. Start mihomo
    nohup "$MIHOMO" -d "$RUNTIME" -f "$YAML" >> "$LOG" 2>&1 &
    pid=$!
    echo $pid > "$PIDFILE"
    # 4. Give it a beat to fail fast, then verify alive
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
    # Whether or not we had the PID file, also reap any orphans matching our
    # runtime dir — handles the case where prior stops were interrupted.
    kill_orphans
    cleanup_routes
    cleanup_utun
    echo "stopped + routes cleaned"
    ;;
  recover)
    # Used by GUI 急救. Aggressive cleanup of any state we might have left.
    if [ -f "$PIDFILE" ]; then
      pid=$(cat "$PIDFILE")
      kill -KILL "$pid" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    kill_orphans
    pkill -KILL -f "mihomo -d $RUNTIME" 2>/dev/null || true
    cleanup_routes
    cleanup_utun
    echo "recovered"
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
    echo "usage: $0 RUNTIME start|stop|status|recover [YAML]" >&2
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

RULE_TARGETS = ["Chain", "FastLinkOnly", "DIRECT", "REJECT"]

# Loyalsoldier/clash-rules — the de-facto standard mainland China rule set,
# regenerated daily. Mirrors via jsDelivr CDN for fast access from China.
LOYALSOLDIER_BASE = "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release"

DEFAULT_RULE_SETS = [
    {"name": "reject", "behavior": "domain", "target": "REJECT",
     "url": f"{LOYALSOLDIER_BASE}/reject.txt", "enabled": True,
     "desc": "广告 / 隐私 / 恶意域名"},
    {"name": "private", "behavior": "domain", "target": "DIRECT",
     "url": f"{LOYALSOLDIER_BASE}/private.txt", "enabled": True,
     "desc": "内网 / 局域网域名"},
    {"name": "applications", "behavior": "classical", "target": "DIRECT",
     "url": f"{LOYALSOLDIER_BASE}/applications.txt", "enabled": True,
     "desc": "国内常见应用程序"},
    {"name": "direct", "behavior": "domain", "target": "DIRECT",
     "url": f"{LOYALSOLDIER_BASE}/direct.txt", "enabled": True,
     "desc": "国内常用网站（直连）"},
    {"name": "icloud", "behavior": "domain", "target": "DIRECT",
     "url": f"{LOYALSOLDIER_BASE}/icloud.txt", "enabled": False,
     "desc": "iCloud 域名（默认直连）"},
    {"name": "apple", "behavior": "domain", "target": "DIRECT",
     "url": f"{LOYALSOLDIER_BASE}/apple.txt", "enabled": False,
     "desc": "Apple 服务（默认直连）"},
    {"name": "google", "behavior": "domain", "target": "FastLinkOnly",
     "url": f"{LOYALSOLDIER_BASE}/google.txt", "enabled": True,
     "desc": "Google 服务（走第一跳）"},
    {"name": "proxy", "behavior": "domain", "target": "FastLinkOnly",
     "url": f"{LOYALSOLDIER_BASE}/proxy.txt", "enabled": True,
     "desc": "常见需代理的境外域名（走第一跳）"},
    {"name": "gfw", "behavior": "domain", "target": "FastLinkOnly",
     "url": f"{LOYALSOLDIER_BASE}/gfw.txt", "enabled": True,
     "desc": "GFW 黑名单域名（走第一跳）"},
    {"name": "tld-not-cn", "behavior": "domain", "target": "FastLinkOnly",
     "url": f"{LOYALSOLDIER_BASE}/tld-not-cn.txt", "enabled": True,
     "desc": "非 .cn 顶级域名（走第一跳）"},
    {"name": "telegramcidr", "behavior": "ipcidr", "target": "FastLinkOnly",
     "url": f"{LOYALSOLDIER_BASE}/telegramcidr.txt", "enabled": True,
     "desc": "Telegram IP 段（走第一跳）"},
    {"name": "lancidr", "behavior": "ipcidr", "target": "DIRECT",
     "url": f"{LOYALSOLDIER_BASE}/lancidr.txt", "enabled": True,
     "desc": "局域网 IP 段（直连）"},
    {"name": "cncidr", "behavior": "ipcidr", "target": "DIRECT",
     "url": f"{LOYALSOLDIER_BASE}/cncidr.txt", "enabled": True,
     "desc": "中国大陆 IP 段（直连）"},
]

DEFAULT_CONFIG = {
    "local_port": 7890,
    "controller_port": 9999,
    "controller_secret": "chainproxy",
    "set_system_proxy_on_start": False,
    "tun_mode": False,
    # ALL of FastLink's executables that may originate outbound packets.
    # Missing any one of these causes a TUN routing loop:
    #   AtlasCore (the Go engine) dials FastLink's VPN node →
    #   TUN captures it → no PROCESS-NAME match → falls through to MATCH,Chain →
    #   routed back through FastLink → re-captured → context deadline exceeded.
    "first_hop_process_names": [
        "FastLink机场",     # Flutter GUI
        "AtlasCore_arm64",  # actual proxy engine on Apple Silicon
        "AtlasCore_amd64",  # actual proxy engine on Intel
    ],
    "active_first_hop": "",
    "active_second_hop": "",
    "first_hops": [],
    "second_hops": [],
    # Rules
    "rule_sets": list(DEFAULT_RULE_SETS),
    "rule_sets_last_update": "",
    "custom_rules_pre": [],
    "custom_rules_post": [],
    # Final fallback for traffic not matching any rule. Default = FastLinkOnly:
    # send everything through the fast/stable first hop, and only divert
    # specific domains to Chain (second hop) via custom_rules_pre.
    "final_target": "FastLinkOnly",
    # Built-in rule sets ON by default — they handle reject/direct/cncidr etc.
    # Their proxy targets all point at FastLinkOnly (first hop) so the second
    # hop is reserved exclusively for traffic the user routes there via
    # custom rules.
    "rules_enabled": True,
}


# ---------- config persistence ----------

def load_config():
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    RULESET_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception:
        cfg = dict(DEFAULT_CONFIG)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    # Migrate rule_sets: if user has an older list, fold in any new defaults
    # (by name) without disturbing user-altered entries.
    existing_names = {r.get("name") for r in cfg.get("rule_sets", [])}
    for default_rule in DEFAULT_RULE_SETS:
        if default_rule["name"] not in existing_names:
            cfg["rule_sets"].append(dict(default_rule))
    # Migrate first_hop_process_names: union user's list with defaults, so
    # adding new FastLink binaries (e.g., AtlasCore) auto-fixes existing
    # installs without losing any custom entries.
    user_procs = list(cfg.get("first_hop_process_names") or [])
    for default_proc in DEFAULT_CONFIG["first_hop_process_names"]:
        if default_proc not in user_procs:
            user_procs.append(default_proc)
    cfg["first_hop_process_names"] = user_procs
    return cfg


def download_rule_set(rs, timeout=20):
    """Download one rule set. Returns (ok, msg)."""
    name = rs["name"]
    url = rs["url"]
    dest = RULESET_DIR / f"{name}.txt"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ChainProxy/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if not data or len(data) < 16:
            return False, f"{name}: empty response"
        dest.write_bytes(data)
        return True, f"{name}: {len(data):,} bytes"
    except Exception as e:
        return False, f"{name}: {e}"


def update_all_rule_sets(cfg, log_cb):
    """Download all enabled rule sets sequentially. Records timestamp.
    Custom rules are NEVER touched here."""
    RULESET_DIR.mkdir(parents=True, exist_ok=True)
    enabled = [r for r in cfg.get("rule_sets", []) if r.get("enabled")]
    log_cb(f"开始下载 {len(enabled)} 个规则集…")
    ok_count = 0
    for rs in enabled:
        ok, msg = download_rule_set(rs)
        log_cb(("  ✓ " if ok else "  ✗ ") + msg)
        if ok:
            ok_count += 1
    cfg["rule_sets_last_update"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_config(cfg)
    log_cb(f"完成：{ok_count}/{len(enabled)} 成功，时间 {cfg['rule_sets_last_update']}")
    return ok_count, len(enabled)


def rule_set_local_path_exists(name):
    return (RULESET_DIR / f"{name}.txt").exists()


def save_config(cfg):
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


# ---------- mihomo binary ----------

def find_mihomo():
    for p in MIHOMO_BIN_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


# ---------- mihomo yaml generation ----------

def proxy_to_mihomo(p):
    """Convert our internal node dict into a mihomo proxy spec."""
    base = {"name": p["name"], "type": p["type"], "server": p["server"], "port": int(p["port"])}
    t = p["type"]
    if t in ("socks5", "http"):
        if p.get("username"):
            base["username"] = p["username"]
        if p.get("password"):
            base["password"] = p["password"]
        if p.get("tls"):
            base["tls"] = True
        if p.get("skip_cert_verify"):
            base["skip-cert-verify"] = True
        if t == "socks5" and p.get("udp"):
            base["udp"] = True
    elif t == "trojan":
        base["password"] = p.get("password", "")
        base["sni"] = p.get("sni", p["server"])
        if p.get("skip_cert_verify"):
            base["skip-cert-verify"] = True
        if p.get("udp"):
            base["udp"] = True
    elif t == "ss":
        base["cipher"] = p.get("cipher", "aes-256-gcm")
        base["password"] = p.get("password", "")
        if p.get("udp"):
            base["udp"] = True
    elif t == "vmess":
        base["uuid"] = p.get("password", "")
        base["alterId"] = int(p.get("alter_id", 0) or 0)
        base["cipher"] = p.get("cipher", "auto")
        if p.get("tls"):
            base["tls"] = True
        if p.get("udp"):
            base["udp"] = True
    elif t == "hysteria2":
        base["password"] = p.get("password", "")
        base["sni"] = p.get("sni", p["server"])
        if p.get("skip_cert_verify"):
            base["skip-cert-verify"] = True
    return base


def _normalize_target(t):
    """Map our internal target names to the actual proxy/group names mihomo
    will see. FastLinkOnly is a proxy-group (defined below)."""
    return t  # names match


def _is_ipv4_literal(s):
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def build_mihomo_yaml(cfg):
    fh = next((x for x in cfg["first_hops"] if x["name"] == cfg["active_first_hop"]), None)
    sh = next((x for x in cfg["second_hops"] if x["name"] == cfg["active_second_hop"]), None)
    if not fh or not sh:
        raise ValueError("请先选择第一跳和第二跳节点")

    fh_proxy = proxy_to_mihomo(fh)
    sh_proxy = proxy_to_mihomo(sh)
    # mihomo 1.19+ removed 'relay' proxy-group; chain via dialer-proxy on the
    # second hop so its outbound dial goes through the first hop.
    sh_proxy["dialer-proxy"] = fh["name"]

    # === Rules assembly ===
    rules = []
    # 1. Loop-prevention rules for TUN mode (always emitted before anything else)
    if cfg.get("tun_mode"):
        for pname in cfg.get("first_hop_process_names", []) or []:
            if pname:
                rules.append(f"PROCESS-NAME,{pname},DIRECT")
        sh_server = sh["server"]
        if _is_ipv4_literal(sh_server):
            rules.append(f"IP-CIDR,{sh_server}/32,DIRECT,no-resolve")
        else:
            rules.append(f"DOMAIN,{sh_server},DIRECT")

    # 2. User-defined PRE rules (highest user priority)
    for r in cfg.get("custom_rules_pre", []) or []:
        r = (r or "").strip()
        if r and not r.startswith("#"):
            rules.append(r)

    # 3. Built-in rule sets, in their config.json order
    rule_providers = {}
    if cfg.get("rules_enabled"):
        for rs in cfg.get("rule_sets", []) or []:
            if not rs.get("enabled"):
                continue
            name = rs["name"]
            if not rule_set_local_path_exists(name):
                # Skip any rule set we haven't downloaded yet — emitting a
                # RULE-SET line that points at a missing file makes mihomo
                # refuse to start.
                continue
            rule_providers[name] = {
                "type": "file",
                "behavior": rs["behavior"],
                # Loyalsoldier's .txt files are actually YAML (payload: …),
                # despite the extension. Other sources may differ — adjust
                # per-set in config.json if you swap providers.
                "format": rs.get("format", "yaml"),
                "path": f"./ruleset/{name}.txt",
            }
            # IP-CIDR rules need ,no-resolve so mihomo doesn't trigger DNS
            # resolution just to evaluate them — that produces wrong matches
            # when DNS returns 0.0.0.0 / fake-ip from upstream / times out.
            suffix = ",no-resolve" if rs["behavior"] == "ipcidr" else ""
            rules.append(f"RULE-SET,{name},{_normalize_target(rs['target'])}{suffix}")
        # GeoIP fallback for IPs not matched by any IP-CIDR rule set
        rules.append("GEOIP,LAN,DIRECT,no-resolve")
        rules.append("GEOIP,CN,DIRECT")

    # 4. User-defined POST rules
    for r in cfg.get("custom_rules_post", []) or []:
        r = (r or "").strip()
        if r and not r.startswith("#"):
            rules.append(r)

    # 5. Catch-all
    final = cfg.get("final_target", "Chain")
    if final not in RULE_TARGETS or final == "REJECT":
        final = "Chain"
    rules.append(f"MATCH,{final}")

    doc = {
        "mixed-port": int(cfg["local_port"]),
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "external-controller": f"127.0.0.1:{int(cfg['controller_port'])}",
        "secret": cfg["controller_secret"],
        "find-process-mode": "always" if cfg.get("tun_mode") else "off",
        "proxies": [fh_proxy, sh_proxy],
        "proxy-groups": [
            # Full chain: FastLink → second hop → target
            {"name": "Chain", "type": "select", "proxies": [sh["name"]]},
            # First-hop only: skip second hop, exit through FastLink
            {"name": "FastLinkOnly", "type": "select", "proxies": [fh["name"]]},
            # Convenience group
            {"name": "GLOBAL", "type": "select",
             "proxies": ["Chain", "FastLinkOnly", "DIRECT"]},
        ],
        "rules": rules,
    }
    if rule_providers:
        doc["rule-providers"] = rule_providers
    if cfg.get("tun_mode"):
        doc["dns"] = {
            "enable": True,
            "listen": "127.0.0.1:0",
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "nameserver": ["119.29.29.29", "223.5.5.5"],
        }
        doc["tun"] = {
            "enable": True,
            "stack": "system",
            "dns-hijack": ["any:53"],
            "auto-route": True,
            "auto-detect-interface": True,
        }
    return _yaml_dump(doc)


def _yaml_dump(obj, indent=0):
    """Tiny YAML emitter (no PyYAML dep). Handles dict/list/str/int/bool/None."""
    sp = "  " * indent
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                out.append(f"{sp}{k}:")
                out.append(_yaml_dump(v, indent + 1))
            else:
                out.append(f"{sp}{k}: {_yaml_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                first = True
                for k, v in item.items():
                    prefix = f"{sp}- " if first else f"{sp}  "
                    first = False
                    if isinstance(v, (dict, list)):
                        out.append(f"{prefix}{k}:")
                        out.append(_yaml_dump(v, indent + 2))
                    else:
                        out.append(f"{prefix}{k}: {_yaml_scalar(v)}")
            else:
                out.append(f"{sp}- {_yaml_scalar(item)}")
    else:
        out.append(f"{sp}{_yaml_scalar(obj)}")
    return "\n".join(x for x in out if x != "")


def _yaml_scalar(v):
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(c in s for c in [":", "#", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`"]) or s == "" or s.strip() != s:
        s = '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


# ---------- macOS system proxy control ----------

def tcp_reachable(host, port, timeout=2.0):
    """Quick TCP-connect probe. Used to preflight the first hop so we don't
    start mihomo just to have it spew connection-refused for every request."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _default_route_iface():
    """Returns the network interface name (e.g. 'en0') that owns the IPv4
    default route. This is the only interface actually carrying traffic."""
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
    """Map each network service name to its underlying device, e.g.
    {'Wi-Fi': 'en0', 'USB 10/100/1000 LAN': 'en6', ...}."""
    try:
        out = subprocess.check_output(
            ["networksetup", "-listnetworkserviceorder"], text=True
        )
    except Exception:
        return {}
    mapping = {}
    # Pattern: "(2) Wi-Fi\n(Hardware Port: Wi-Fi, Device: en0)"
    pairs = re.findall(
        r"\(\d+\)\s+(.+?)\n\(Hardware Port:.*?Device:\s*([^)]+)\)",
        out,
    )
    for name, dev in pairs:
        mapping[name.strip()] = dev.strip()
    return mapping


def get_active_network_service():
    """Find the macOS network service whose interface owns the default route.
    Falls back to the first IP-bearing service."""
    iface = _default_route_iface()
    smap = _service_to_iface_map()
    if iface:
        for svc, dev in smap.items():
            if dev == iface:
                return svc
    # Fallback: first service with an IP address (excluding disabled).
    try:
        out = subprocess.check_output(
            ["networksetup", "-listnetworkserviceorder"], text=True
        )
    except Exception:
        return None
    # Match only "(N) ServiceName" lines, not "(Hardware Port: ..., Device: enX)"
    # The regex below was broken before — it greedily caught the Hardware Port
    # detail lines and crashed on split, which silently killed any GUI button
    # that touched system proxy.
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
        # Always force-set: many other proxy clients (Karing, FastLink,
        # ClashVerge) like to overwrite system proxy with their own port.
        # Re-asserting our value beats whatever is there.
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


def test_url_through_proxy(url, local_port, log_path, timeout=15):
    """Send a request through the local mihomo, time it, and read the
    matching rule from mihomo's log. Returns a dict with all the diagnostics
    the GUI shows in the test tab."""
    # Normalize: bare host -> https; host:80 -> http
    raw = url.strip()
    if not raw:
        return {"ok": False, "error": "URL 为空"}
    if "://" not in raw:
        if raw.endswith(":80") or raw.endswith(":8080"):
            raw = "http://" + raw
        else:
            raw = "https://" + raw
    # Extract host for log matching
    host = raw.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]

    # Note current log size so we only inspect lines created by this request.
    pos = log_path.stat().st_size if log_path.exists() else 0

    # curl -w prints metrics. Order: code|total|dns|connect|app|remote
    fmt = "%{http_code}|%{time_total}|%{time_namelookup}|%{time_connect}|%{time_appconnect}|%{remote_ip}"
    cmd = [
        "curl", "-x", f"http://127.0.0.1:{local_port}",
        "-sS", "--max-time", str(timeout),
        "-o", "/dev/null", "-w", fmt, raw,
    ]
    started = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    elapsed_ms = int((time.time() - started) * 1000)

    result = {
        "ok": False,
        "url": raw,
        "host": host,
        "elapsed_ms": elapsed_ms,
        "rule": "?",
        "chain": "?",
        "proxy": "?",
        "log_lines": [],
    }

    if r.returncode == 0 and "|" in (r.stdout or ""):
        parts = r.stdout.strip().split("|")
        if len(parts) >= 6:
            code, ttot, tdns, tconn, tapp, remote = parts
            result.update({
                "ok": True,
                "http_code": code,
                "time_total_ms": int(float(ttot) * 1000),
                "time_dns_ms": int(float(tdns) * 1000),
                "time_connect_ms": int(float(tconn) * 1000),
                "time_tls_ms": int(float(tapp) * 1000),
                "remote_ip": remote or "(none)",
            })
    else:
        result["error"] = (r.stderr or r.stdout or "").strip() or f"curl exit={r.returncode}"

    # Read new log lines and find the routing decision.
    if log_path.exists():
        try:
            with open(log_path, "r", errors="ignore") as f:
                f.seek(pos)
                tail = f.read()
            new_lines = tail.splitlines()
            result["log_lines"] = new_lines[-30:]
            # Match line like:
            #   ... msg="[TCP] 127.0.0.1:51134 --> www.google.com:443 match RuleSet(proxy) using Chain[SecondHop]"
            # Note the trailing quote from mihomo's logfmt — exclude it from
            # the captured proxy name with [^\s"].
            pat = re.compile(
                r"\[TCP\][^\n]*-->\s*" + re.escape(host) +
                r":\d+\s+match\s+([^\s\"]+)\s+using\s+([^\s\"]+)"
            )
            for line in new_lines:
                m = pat.search(line)
                if m:
                    rule = m.group(1)
                    proxy_full = m.group(2)
                    # proxy_full is like "Chain[SecondHop]" or "DIRECT" or "FastLinkOnly[FastLink]"
                    if "[" in proxy_full:
                        chain, proxy = proxy_full.split("[", 1)
                        proxy = proxy.rstrip("]")
                    else:
                        chain = proxy_full
                        proxy = proxy_full
                    result["rule"] = rule
                    result["chain"] = chain
                    result["proxy"] = proxy
                    break
        except Exception as e:
            result["log_error"] = str(e)
    return result


def panic_recover(log_cb):
    """Best-effort cleanup: clear system proxy on the active service, run
    the helper's `recover` action to drop TUN routes, and stop our mihomo."""
    log_cb("=== 网络急救 ===")
    # 1. Clear system proxy (no sudo needed for current user's services)
    ok, info = set_system_proxy(0, enable=False)
    log_cb(f"  系统代理已清 ({info if ok else 'error: '+info})")
    # 2. Run helper recover (uses passwordless sudo if installed)
    if helper_installed():
        try:
            r = subprocess.run(
                ["sudo", "-n", HELPER_PATH, str(RUNTIME_DIR), "recover"],
                capture_output=True, text=True, timeout=30,
            )
            log_cb(f"  helper recover: {(r.stdout or r.stderr).strip()}")
        except Exception as e:
            log_cb(f"  helper recover 失败: {e}")
    else:
        log_cb("  未安装 sudo helper，跳过 TUN 路由清理")
    log_cb("==================")


# ---------- mihomo process manager ----------

class MihomoRunner:
    def __init__(self, log_cb):
        self.proc = None       # Popen handle (non-sudo path)
        self.sudo_pid = None   # PID (sudo path; we don't own the process)
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
                # EPERM = process exists but we (non-root) can't signal it
                # because it's owned by root. That's our normal TUN-mode case
                # — treat it as alive. ESRCH = actually dead.
                if e.errno == errno.EPERM:
                    return True
                self.sudo_pid = None
                return False
        return False

    def attach_existing(self, log_cb=None):
        """Reattach to a mihomo process from a previous GUI session.

        Two cases:
          1. PID file present + process alive -> claim it
          2. No PID file, but a mihomo running with our runtime dir
             (orphan from an interrupted stop) -> claim it too
        Either way subsequent Stop will use the helper which can sudo-kill it.
        """
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
                    # EPERM = root-owned, alive; that's expected for TUN.
            except (ValueError, OSError):
                try:
                    pid_file.unlink()
                except OSError:
                    pass
                pid = None
        if pid is None:
            # Look for an orphan mihomo running with our config dir
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
        # First-time setup: install the privileged helper + sudoers entry
        # (one admin prompt, then no more password prompts ever).
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
            # If sudoers was lost or never installed, try installing again.
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


# ---------- GUI ----------


def acquire_single_instance_lock():
    """Prevent two GUI instances from running at once. Returns the lock fd
    on success, or None if another instance already holds it (in which case
    the caller should exit). Uses an exclusive flock on a file in SUPPORT_DIR.

    Without this, double-clicking the .app while one is running would create
    a second python process whose Tk window may render behind the first or
    appear blank — explaining the "关了重开不能正常显示" symptom.
    """
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
    """Best-effort: tell macOS to bring the existing ChainProxy window to
    front. Used when single-instance lock is taken."""
    try:
        subprocess.Popen([
            "osascript", "-e",
            'tell application "ChainProxy" to activate',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


