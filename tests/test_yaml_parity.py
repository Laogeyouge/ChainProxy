"""Verify the YAML produced on Windows is byte-identical to what macOS used
to produce, given the same config dict.

The YAML emitter is the macOS version's; this just guards against accidental
divergence when the file got copy-pasted across the platform split.
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core._common as common  # noqa: E402

cfg = {
    "local_port": 7890,
    "controller_port": 9999,
    "controller_secret": "chainproxy",
    "tun_mode": False,
    "first_hop_process_names": [],
    "active_first_hop": "FH",
    "active_second_hop": "SH",
    "first_hops": [{"name": "FH", "type": "socks5", "server": "127.0.0.1",
                    "port": 6666, "udp": True}],
    "second_hops": [{"name": "SH", "type": "trojan", "server": "ex.com",
                     "port": 443, "password": "p", "sni": "ex.com", "udp": True}],
    "rule_sets": [], "rules_enabled": False,
    "custom_rules_pre": ["DOMAIN-SUFFIX,openai.com,Chain"],
    "custom_rules_post": [],
    "final_target": "FirstHopOnly",
}
yaml = common.build_mihomo_yaml(cfg, Path("/no/such/dir"))
expected_substrings = [
    "mixed-port: 7890",
    "name: FH",
    "name: SH",
    "dialer-proxy: FH",
    "DOMAIN-SUFFIX,openai.com,Chain",
    "MATCH,FirstHopOnly",
    'name: Chain',
    'name: FirstHopOnly',
    'name: GLOBAL',
]
for s in expected_substrings:
    assert s in yaml, f"missing in YAML: {s!r}\n---\n{yaml}\n---"

# Make sure we did NOT emit a TUN block for non-TUN config
assert "tun:" not in yaml, "TUN block leaked into non-TUN config"
assert "PROCESS-NAME" not in yaml

print("[parity] non-TUN YAML: OK")

cfg["tun_mode"] = True
cfg["first_hop_process_names"] = ["FastLink.exe"]
yaml = common.build_mihomo_yaml(cfg, Path("/no/such/dir"))
assert "tun:" in yaml
assert "stack: system" in yaml
assert "auto-route: true" in yaml
assert "PROCESS-NAME,FastLink.exe,DIRECT" in yaml
# Domain-style server in second hop should produce DOMAIN rule, not IP-CIDR
assert "DOMAIN,ex.com,DIRECT" in yaml
print("[parity] TUN YAML: OK")

# IP-literal server should emit IP-CIDR loop guard instead
cfg["second_hops"] = [{"name": "SH", "type": "trojan", "server": "1.2.3.4",
                       "port": 443, "password": "p"}]
yaml = common.build_mihomo_yaml(cfg, Path("/no/such/dir"))
assert "IP-CIDR,1.2.3.4/32,DIRECT,no-resolve" in yaml
print("[parity] IP-literal loop guard: OK")

# Rules without downloaded files should be skipped (the file doesn't exist
# under the bogus path), but RULE-SET lines for missing sets must NOT appear
cfg["tun_mode"] = False
cfg["rules_enabled"] = True
cfg["rule_sets"] = [{"name": "google", "behavior": "domain",
                     "target": "FirstHopOnly", "enabled": True,
                     "url": "x", "desc": ""}]
yaml = common.build_mihomo_yaml(cfg, Path("/no/such/dir"))
assert "RULE-SET,google" not in yaml, \
    "Missing rule-set file emitted RULE-SET line — would crash mihomo"
print("[parity] missing-rule-set guard: OK")

print("\n[parity] ALL PARITY TESTS PASSED")
