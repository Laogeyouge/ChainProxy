"""Platform-independent core: schema, mihomo YAML generation, rule-set
download, URL test, helpers. Functions that need filesystem paths take them
as arguments so the same code runs on macOS and Windows.
"""

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "ChainProxy"

PROTOCOLS = ["socks5", "http", "trojan", "ss", "vmess", "hysteria2"]
SS_CIPHERS = ["aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "2022-blake3-aes-256-gcm"]

# Mihomo's default fake-ip / TUN gateway. We clean up any leaked routes
# pointing at this gateway during stop/recover.
FAKE_GATEWAY = "198.18.0.1"

RULE_TARGETS = ["Chain", "FirstHopOnly", "DIRECT", "REJECT"]

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
    {"name": "google", "behavior": "domain", "target": "FirstHopOnly",
     "url": f"{LOYALSOLDIER_BASE}/google.txt", "enabled": True,
     "desc": "Google 服务（走第一跳）"},
    {"name": "proxy", "behavior": "domain", "target": "FirstHopOnly",
     "url": f"{LOYALSOLDIER_BASE}/proxy.txt", "enabled": True,
     "desc": "常见需代理的境外域名（走第一跳）"},
    {"name": "gfw", "behavior": "domain", "target": "FirstHopOnly",
     "url": f"{LOYALSOLDIER_BASE}/gfw.txt", "enabled": True,
     "desc": "GFW 黑名单域名（走第一跳）"},
    {"name": "tld-not-cn", "behavior": "domain", "target": "FirstHopOnly",
     "url": f"{LOYALSOLDIER_BASE}/tld-not-cn.txt", "enabled": True,
     "desc": "非 .cn 顶级域名（走第一跳）"},
    {"name": "telegramcidr", "behavior": "ipcidr", "target": "FirstHopOnly",
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
    # In TUN mode we add PROCESS-NAME,...,DIRECT rules so the first-hop
    # client's outbound packets aren't recaptured by our own TUN — that loop
    # would route the airport client's VPN dial back through itself, dying
    # with "context deadline exceeded".
    # On Windows fill in EVERY .exe (e.g. "FastLink.exe", "AtlasCore.exe").
    "first_hop_process_names": [],
    "active_first_hop": "",
    "active_second_hop": "",
    "first_hops": [],
    "second_hops": [],
    "rule_sets": list(DEFAULT_RULE_SETS),
    "rule_sets_last_update": "",
    "custom_rules_pre": [],
    "custom_rules_post": [],
    "final_target": "FirstHopOnly",
    "rules_enabled": True,
}


# ---------- config persistence ----------

def load_config(config_path: Path, support_dir: Path,
                runtime_dir: Path, ruleset_dir: Path):
    support_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    ruleset_dir.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        save_config(DEFAULT_CONFIG, config_path, support_dir)
        return dict(DEFAULT_CONFIG)
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        cfg = dict(DEFAULT_CONFIG)
    migrated = False
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
            migrated = True
    existing_names = {r.get("name") for r in cfg.get("rule_sets", [])}
    for default_rule in DEFAULT_RULE_SETS:
        if default_rule["name"] not in existing_names:
            cfg["rule_sets"].append(dict(default_rule))
            migrated = True
    # Migrate legacy "FastLinkOnly" rule target → "FirstHopOnly"
    if cfg.get("final_target") == "FastLinkOnly":
        cfg["final_target"] = "FirstHopOnly"
        migrated = True
    for rs in cfg.get("rule_sets", []) or []:
        if rs.get("target") == "FastLinkOnly":
            rs["target"] = "FirstHopOnly"
            migrated = True
    for key in ("custom_rules_pre", "custom_rules_post"):
        new = []
        for ln in (cfg.get(key) or []):
            if isinstance(ln, str) and ",FastLinkOnly" in ln:
                new.append(ln.replace(",FastLinkOnly", ",FirstHopOnly"))
                migrated = True
            else:
                new.append(ln)
        cfg[key] = new
    cfg["first_hop_process_names"] = list(
        cfg.get("first_hop_process_names") or [])
    if migrated:
        save_config(cfg, config_path, support_dir)
    return cfg


_save_lock = threading.Lock()


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8"):
    """Write file content atomically: write to a sibling .tmp, fsync, rename.
    On all supported platforms (POSIX, Windows since Python 3.3) os.replace
    is atomic on the same filesystem. Why we need this: previously a crash
    or kill mid-write would leave a half-truncated file, and on next start
    mihomo (or load_config) would error out. The bug had been silent until
    one morning the user found ChainProxy refusing to start with a YAML
    parse error mid-line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding=encoding) as f:
        f.write(content)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            # Some filesystems / network mounts return EINVAL on fsync;
            # the rename is still atomic enough for our use case.
            pass
    os.replace(tmp, path)


def save_config(cfg, config_path: Path, support_dir: Path):
    support_dir.mkdir(parents=True, exist_ok=True)
    # Lock to serialize concurrent writers (rule-set updater thread vs Qt
    # main thread). Without this, the JSON output of one writer can
    # overwrite the other's state mid-flight even with atomic rename,
    # because the in-memory `cfg` snapshot was lost.
    with _save_lock:
        # Defensive backup: keep a rolling copy of the LAST successfully-
        # saved file at config.json.bak. If a future save ever wipes the
        # second-hop list (or anything else valuable) the user has a
        # mechanical fallback they can restore by hand. We only refresh
        # the .bak if the about-to-be-written content actually has
        # populated `first_hops`/`second_hops` — that way a transient
        # save with empty lists (mid-construction) can never overwrite
        # a known-good backup with a known-bad one.
        new_text = json.dumps(cfg, indent=2, ensure_ascii=False)
        try:
            if (cfg.get("first_hops") and cfg.get("second_hops")
                    and config_path.exists()):
                bak = config_path.with_name(config_path.name + ".bak")
                # Only copy if the existing file ALSO had populated hops
                # (otherwise we'd shadow a good backup with the empty
                # state we're about to overwrite).
                try:
                    prev = json.loads(
                        config_path.read_text(encoding="utf-8"))
                    if prev.get("first_hops") and prev.get("second_hops"):
                        bak.write_text(
                            config_path.read_text(encoding="utf-8"),
                            encoding="utf-8")
                except (OSError, ValueError):
                    pass
        except OSError:
            pass
        atomic_write_text(config_path, new_text)


def download_rule_set(rs, ruleset_dir: Path, timeout=20):
    """Download one rule set. Returns (ok, msg)."""
    name = rs["name"]
    url = rs["url"]
    dest = ruleset_dir / f"{name}.txt"
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


def update_all_rule_sets(cfg, ruleset_dir: Path, config_path: Path,
                         support_dir: Path, log_cb):
    """Download all enabled rule sets sequentially. Records timestamp."""
    ruleset_dir.mkdir(parents=True, exist_ok=True)
    enabled = [r for r in cfg.get("rule_sets", []) if r.get("enabled")]
    log_cb(f"开始下载 {len(enabled)} 个规则集…")
    ok_count = 0
    for rs in enabled:
        ok, msg = download_rule_set(rs, ruleset_dir)
        log_cb(("  ✓ " if ok else "  ✗ ") + msg)
        if ok:
            ok_count += 1
    cfg["rule_sets_last_update"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_config(cfg, config_path, support_dir)
    log_cb(f"完成：{ok_count}/{len(enabled)} 成功，时间 {cfg['rule_sets_last_update']}")
    return ok_count, len(enabled)


def rule_set_local_path_exists(name, ruleset_dir: Path):
    return (ruleset_dir / f"{name}.txt").exists()


# ---------- geo-data (MMDB / dat) seeding ----------
#
# mihomo needs a GeoIP MMDB to evaluate `GEOIP,CN`/`GEOIP,LAN` rules. When it
# can't find one, it tries to download from a default GitHub URL. On a fresh
# Windows install where the user has no proxy yet (the very situation
# ChainProxy is meant to bootstrap), GitHub is unreachable, the download
# fails, mihomo deletes the empty file and retries forever — controller never
# binds, ChainProxy reports "mihomo 启动后立即崩溃".
#
# Fix: ship a snapshot of the three Loyalsoldier files inside the installer/
# .app and copy them into runtime/ on every startup if the runtime copy is
# missing or empty. This eliminates the cold-start dependency on GitHub.
GEODATA_FILES = ("Country.mmdb", "geoip.dat", "geosite.dat")


def seed_geodata(bundled_dir: Path, runtime_dir: Path, log_cb=None):
    """Copy the bundled mmdb/dat snapshot into runtime_dir if not already
    present. Safe to call on every startup — it's a no-op when the runtime
    copy exists and is non-empty.

    Returns the list of filenames that were freshly copied (or [] if nothing
    needed seeding).
    """
    seeded = []
    try:
        bundled_dir = Path(bundled_dir) if bundled_dir else None
    except Exception:
        bundled_dir = None
    if not bundled_dir or not bundled_dir.is_dir():
        return seeded
    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for name in GEODATA_FILES:
        src = bundled_dir / name
        dst = runtime_dir / name
        if not src.exists():
            continue
        # Don't clobber a file that's already healthy. mihomo updates these
        # in place when /configs/geo is poked, and we shouldn't wind that
        # forward-progress backwards just because the user reinstalled.
        if dst.exists() and dst.stat().st_size > 0:
            continue
        try:
            data = src.read_bytes()
            if not data:
                continue
            tmp = dst.with_name(f".{dst.name}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
            seeded.append(name)
        except OSError as e:
            if log_cb:
                log_cb(f"seed_geodata: {name} 复制失败：{e}")
    if seeded and log_cb:
        log_cb(f"已铺设 GeoIP 数据：{', '.join(seeded)}")
    return seeded


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
    """Map internal target names to actual proxy/group names mihomo sees."""
    return t  # names match


def _is_ipv4_literal(s):
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def build_mihomo_yaml(cfg, ruleset_dir: Path):
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
    # 1. Loop-prevention rules for TUN mode
    if cfg.get("tun_mode"):
        for pname in cfg.get("first_hop_process_names", []) or []:
            if pname:
                rules.append(f"PROCESS-NAME,{pname},DIRECT")
        sh_server = sh["server"]
        if _is_ipv4_literal(sh_server):
            rules.append(f"IP-CIDR,{sh_server}/32,DIRECT,no-resolve")
        else:
            rules.append(f"DOMAIN,{sh_server},DIRECT")

    # 2. User-defined PRE rules
    for r in cfg.get("custom_rules_pre", []) or []:
        r = (r or "").strip()
        if r and not r.startswith("#"):
            rules.append(r)

    # 3. Built-in rule sets
    rule_providers = {}
    if cfg.get("rules_enabled"):
        for rs in cfg.get("rule_sets", []) or []:
            if not rs.get("enabled"):
                continue
            name = rs["name"]
            if not rule_set_local_path_exists(name, ruleset_dir):
                continue
            rule_providers[name] = {
                "type": "file",
                "behavior": rs["behavior"],
                "format": rs.get("format", "yaml"),
                "path": f"./ruleset/{name}.txt",
            }
            suffix = ",no-resolve" if rs["behavior"] == "ipcidr" else ""
            rules.append(f"RULE-SET,{name},{_normalize_target(rs['target'])}{suffix}")
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
            {"name": "Chain", "type": "select", "proxies": [sh["name"]]},
            {"name": "FirstHopOnly", "type": "select", "proxies": [fh["name"]]},
            {"name": "GLOBAL", "type": "select",
             "proxies": ["Chain", "FirstHopOnly", "DIRECT"]},
        ],
        "rules": rules,
    }
    if rule_providers:
        doc["rule-providers"] = rule_providers
    if cfg.get("tun_mode"):
        # store-fake-ip persists the fakeip↔domain table to mihomo's cache.db
        # so the same domain gets the same fakeip across mihomo restarts.
        # Why: macOS system DNS resolver caches our fakeip answers. Without
        # persistence, every mihomo restart (rule edit auto-restart, node
        # change, manual restart) reshuffles the pool, leaving the system
        # cache pointing at fakeips the new mihomo doesn't recognize. Symptom:
        # "claude.ai works, then suddenly stops; netflix keeps working because
        # it's on a long-lived QUIC connection." Fixed by making the mapping
        # stable across restarts.
        doc["profile"] = {"store-fake-ip": True}
        # default-nameserver bootstraps any hostname-form upstream out-of-band
        # of fakeip — defensive even though our `nameserver` entries are IPs
        # today, because user-edited configs may add hostnames later.
        # fake-ip-filter excludes domains that must resolve to real IPs:
        # mDNS/.local, reverse-DNS, captive-portal probes (msftconnecttest /
        # captive.apple.com), and a few QQ login quirks. Without these, fakeip
        # answers break local-network discovery and macOS captive detection.
        doc["dns"] = {
            "enable": True,
            "listen": "127.0.0.1:0",
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "default-nameserver": ["223.5.5.5", "119.29.29.29"],
            "nameserver": ["119.29.29.29", "223.5.5.5"],
            "fake-ip-filter": [
                "*.lan",
                "*.local",
                "*.in-addr.arpa",
                "*.ip6.arpa",
                "+.msftconnecttest.com",
                "+.msftncsi.com",
                "captive.apple.com",
                "localhost.ptlogin2.qq.com",
            ],
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


# ---------- network helpers ----------

def tcp_reachable(host, port, timeout=2.0):
    """Quick TCP-connect probe."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def socks5_handshake_succeeds(host, port, timeout=0.4):
    """Probe whether `host:port` speaks SOCKS5 NoAuth.

    Why we need this beyond `tcp_reachable`: airport clients like FastLink /
    Mihomo Party / Clash Verge spawn their proxy core under root. macOS
    `lsof` without sudo can only see sockets owned by the calling user, so
    we cannot identify the listener PID directly — but a TCP connect to
    127.0.0.1 is unrestricted regardless of the listener's UID. A successful
    SOCKS5 handshake therefore proves "a real proxy is here" even when we
    cannot see the PID, and we can fall back to a process-name scan to
    populate the TUN whitelist.

    Sends `\\x05\\x01\\x00` (one auth method offered: NoAuth) and expects
    `\\x05\\x00`. Anything else (HTTP-only listener, kernel reset, timeout)
    counts as failure. Mixed-port mihomo / Clash / sing-box / xray all
    respond `\\x05\\x00`, which covers ~every airport client in practice.
    """
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError:
        return False
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        data = s.recv(2)
        return data == b"\x05\x00"
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


# Process-name patterns we trust to identify "this is an airport-client or
# its proxy core". Used as a fallback when lsof can't see the listener (root
# UID) but a SOCKS5 handshake succeeds.
#
# Patterns are grouped by BRAND so the GUI can present families to the user.
# Many airport clients ship a GUI process AND a separately-launched proxy
# core (Karing's GUI vs its root-mode system-extension service; FastLink's
# GUI vs AtlasCore; Clash Verge's GUI vs verge-mihomo) — these may not be
# linked by parent-PID (system-extension services have PPID=1) but they're
# clearly the same product. Brand-grouping makes the chooser dialog show
# "Karing" / "FastLink" / "Clash Verge" instead of pseudo-families based on
# accidental process-tree shape.
#
# Order matters: more specific brand patterns appear first so a name like
# `verge-mihomo` matches "Clash Verge" rather than the generic "mihomo"
# at the bottom of the list.
#
# Match is case-insensitive on the basename of `comm`. Patterns err on the
# inclusive side: a TUN whitelist that contains a non-proxy process is
# harmless (just routes its traffic DIRECT), but missing the actual proxy
# core deadlocks the chain. Add brands as users report them.
AIRPORT_BRANDS = [
    # (label, [regex patterns])
    ("FastLink",      [r"FastLink", r"AtlasCore"]),
    ("Clash Verge",   [r"clash[-_ ]?verge", r"verge[-_]mihomo"]),
    ("Karing",        [r"^Karing$", r"karingService", r"karing"]),
    ("Mihomo Party",  [r"mihomo[-_ ]?party"]),
    ("ClashX",        [r"^ClashX", r"^clashx"]),
    ("Clash.Meta",    [r"clash[-_ ]?meta"]),
    ("FlClash",       [r"FlClash", r"^flclash"]),
    ("V2RayU",        [r"v2rayu"]),
    ("V2RayN",        [r"v2rayn"]),
    ("V2RayNG",       [r"v2rayng"]),
    ("NekoBox",       [r"nekobox", r"NekoRay"]),
    ("Surge",         [r"^Surge", r"^sgw$"]),
    ("Stash",         [r"^Stash$"]),
    ("Quantumult X",  [r"quantumult"]),
    ("Pluto",         [r"^Pluto"]),
    ("Shadowrocket",  [r"shadowrocket"]),
    # Generic Chinese-named airport clients (catch-all by suffix)
    ("airport (机场)", [r"机场"]),
    # Bare proxy cores running standalone (no branded GUI parent matched).
    # Listed last so a branded match wins over a generic one.
    ("mihomo",        [r"^mihomo$"]),
    ("sing-box",      [r"sing[-_]?box"]),
    ("v2ray",         [r"^v2ray$"]),
    ("xray",          [r"^xray$"]),
    ("hysteria",      [r"hysteria"]),
    ("trojan",        [r"^trojan"]),
    ("shadowsocks",   [r"^shadowsocks"]),
]
# Pre-compile for hot-path matching.
_BRAND_RE = [(label, [re.compile(p, re.IGNORECASE) for p in pats])
             for label, pats in AIRPORT_BRANDS]

# Process names we never want in the whitelist even if a parent matched.
# Adding a shell to PROCESS-NAME would route every command-line invocation
# DIRECT, which is not what the user wants.
_NEVER_WHITELIST = {
    "launchd", "init", "kernel_task", "bash", "zsh", "sh", "fish",
    "login", "terminal", "iterm2", "iterm", "tmux", "screen",
    "python", "python3", "ruby", "node",
    # Windows: listed without .exe; name_should_skip strips the suffix
    # before lookup so this set stays cross-platform.
    "explorer", "cmd", "powershell", "pwsh", "conhost",
    "wininit", "csrss", "services", "svchost", "lsass", "winlogon",
}


def airport_brand_for_name(name):
    """Return the brand label (e.g. 'FastLink', 'Karing', 'Clash Verge')
    matching this process name, or None if no pattern matches."""
    if not name:
        return None
    # Windows reports process names with a .exe suffix (Win32_Process.Name),
    # but several patterns below are anchored with $ so the bare-core names
    # (mihomo, v2ray, xray, sgw, Stash) match the macOS form. Strip the suffix
    # so the same patterns work on both platforms.
    if name.lower().endswith(".exe"):
        name = name[:-4]
    for label, regexes in _BRAND_RE:
        for r in regexes:
            if r.search(name):
                return label
    return None


def name_looks_like_airport_client(name):
    """Return True if `name` (a process basename) matches any of the
    well-known airport-client / proxy-core brands."""
    return airport_brand_for_name(name) is not None

def name_should_skip(name):
    if not name:
        return True
    n = name.lower()
    if n.endswith(".exe"):
        n = n[:-4]
    return n in _NEVER_WHITELIST


# ---- macOS bundle-path auto-detect helpers (additive, used by _macos.py) ----
#
# macOS 1.1.9 introduces a second detection path that does NOT use AIRPORT_BRANDS.
# It groups running processes by their .app bundle and uses these generic
# "looks like a proxy" filters to keep only proxy-bearing bundles. Windows
# continues to use the brand-list path above; nothing here changes that.

# Generic proxy-engine names. NOT a brand list. These are the well-known
# proxy CORES — the binaries that actually do the SOCKS5/HTTP proxy work.
PROXY_CORE_HINTS = [
    "mihomo", "clash", "sing-box", "singbox", "xray", "v2ray", "v2fly",
    "hysteria", "trojan", "shadowsocks", "naive", "brook", "juicity",
    "tuic", "ssr-",
]
# Suffix heuristic for branded re-skins of known cores (AtlasCore, CatCore,
# NekoCore, etc.) without enumerating every vendor's name.
_PROXY_CORE_SUFFIXES = ("core",)

# Path / bundle-id keywords that strongly hint a proxy/VPN/airport client.
# Used by the .app-bundle fallback for Karing-style apps that ship a single
# self-contained binary with no proxy-core child process.
PROXY_BUNDLE_HINTS = [
    "proxy", "vpn", "clash", "mihomo", "sing-box", "singbox", "v2ray",
    "xray", "shadowsocks", "trojan", "hysteria", "naive", "karing",
    "machine", "机场", "airport",
]

_ARCH_SUFFIXES = ("_arm64", "_amd64", "_x86_64", "_aarch64", "_x64", "_x86",
                  "-arm64", "-amd64", "-x86_64", "-aarch64", "-x64", "-x86")


def name_looks_like_proxy_core(name):
    """Return True if `name` is recognizable as a well-known proxy engine
    (mihomo / clash / sing-box / xray / v2ray / hysteria / trojan / ...) or
    a branded re-skin ending in 'core' (AtlasCore, CatCore, NekoCore).

    Does NOT identify airport-client *brands* — the brand label is derived
    from the .app bundle path on disk by the macOS backend, not from the
    process name. .exe and architecture suffixes are stripped so the same
    rule works regardless of platform conventions."""
    if not name:
        return False
    n = os.path.basename(name).lower()
    if n.endswith(".exe"):
        n = n[: -len(".exe")]
    for suf in _ARCH_SUFFIXES:
        if n.endswith(suf):
            n = n[: -len(suf)]
            break
    if n in _NEVER_WHITELIST:
        return False
    for hint in PROXY_CORE_HINTS:
        if hint in n:
            return True
    for suf in _PROXY_CORE_SUFFIXES:
        if n.endswith(suf) and len(n) > len(suf):
            return True
    return False


def path_hints_proxy_bundle(path):
    """Return True if `path` (a .app bundle root or bundle ID) contains a
    keyword that strongly hints this is a proxy/VPN/airport client. Used to
    rescue brand-only apps where the binary alone gives no signal but the
    path does (Karing, FastLink机场, etc.)."""
    if not path:
        return False
    p = path.lower()
    return any(h in p for h in PROXY_BUNDLE_HINTS)


def test_url_through_proxy(url, local_port, log_path, curl_devnull,
                           timeout=15, controller_port=None,
                           controller_secret=None):
    """Send a request through the local mihomo, time it, and read the
    matching rule from mihomo's log. `curl_devnull` is the platform-specific
    null sink ('/dev/null' on Unix, 'NUL' on Windows).

    Tries two log sources, in order:
      1. tail of `log_path` (mihomo.log file). Works when mihomo's stdout
         is captured to a file — i.e. non-elevated launches.
      2. mihomo controller's /logs streaming endpoint, if controller_port
         is provided. Works regardless of how mihomo was launched, so it's
         the reliable path on Windows where TUN-mode mihomo is elevated
         and its stdout isn't redirected.
    """
    import threading
    raw = url.strip()
    if not raw:
        return {"ok": False, "error": "URL 为空"}
    if "://" not in raw:
        if raw.endswith(":80") or raw.endswith(":8080"):
            raw = "http://" + raw
        else:
            raw = "https://" + raw
    host = raw.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]

    pos = log_path.stat().st_size if log_path.exists() else 0

    # Start a background thread that streams the controller's /logs endpoint
    # while curl is running. Collected lines are merged with file-based log
    # lines below. Fail-soft: any error here just leaves controller_lines empty.
    controller_lines = []
    stop_evt = threading.Event()

    def _stream_controller_logs():
        if not controller_port:
            return
        try:
            url2 = f"http://127.0.0.1:{int(controller_port)}/logs?level=info"
            req = urllib.request.Request(url2)
            if controller_secret:
                req.add_header("Authorization", f"Bearer {controller_secret}")
            with urllib.request.urlopen(req, timeout=timeout + 2) as r:
                while not stop_evt.is_set():
                    line = r.readline()
                    if not line:
                        break
                    try:
                        obj = json.loads(line.decode("utf-8", errors="replace"))
                        payload = obj.get("payload") or ""
                        if payload:
                            controller_lines.append(payload)
                    except Exception:
                        pass
        except Exception:
            pass

    log_thread = threading.Thread(target=_stream_controller_logs, daemon=True)
    log_thread.start()

    fmt = "%{http_code}|%{time_total}|%{time_namelookup}|%{time_connect}|%{time_appconnect}|%{remote_ip}"
    cmd = [
        "curl", "-x", f"http://127.0.0.1:{local_port}",
        "-sS", "--max-time", str(timeout),
        "-o", curl_devnull, "-w", fmt, raw,
    ]
    started = time.time()
    # On Windows we want curl invocation to be silent (no console window flash
    # if we ever switch to creationflags). subprocess.run is fine here because
    # PyInstaller --noconsole means curl inherits no console anyway.
    creationflags = 0
    try:
        import subprocess as _sp  # local import to avoid cluttering top
        if hasattr(_sp, "CREATE_NO_WINDOW"):
            creationflags = _sp.CREATE_NO_WINDOW
    except Exception:
        pass
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 5,
            creationflags=creationflags,
        )
    except TypeError:
        # creationflags not supported on this platform (Unix)
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

    # Stop the controller-log streamer; it had `timeout+5` seconds total so
    # by now it's almost certainly already produced anything relevant. Give
    # it a grace period to flush, then read.
    stop_evt.set()
    log_thread.join(timeout=0.5)

    file_lines = []
    if log_path.exists():
        try:
            with open(log_path, "r", errors="ignore", encoding="utf-8") as f:
                f.seek(pos)
                tail = f.read()
            file_lines = tail.splitlines()
        except Exception as e:
            result["log_error"] = str(e)

    merged = file_lines + controller_lines
    result["log_lines"] = merged[-30:]

    pat = re.compile(
        r"\[TCP\][^\n]*-->\s*" + re.escape(host) +
        r":\d+\s+match\s+([^\s\"]+)\s+using\s+([^\s\"]+)"
    )
    for line in merged:
        m = pat.search(line)
        if m:
            rule = m.group(1)
            proxy_full = m.group(2)
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
    return result
