"""Tests for the new helpers: seed_geodata + detect_first_hop_processes.

Both must be safe to call in any environment — when bundled assets are
missing or no process is listening on the queried port, they should return
gracefully (no exceptions, empty results).

Run from the repo root: py tests\\test_new_helpers.py
"""
import os
import socket
import sys
import tempfile
from pathlib import Path

# Force offscreen QPA so Qt does not try to talk to a display
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Redirect SUPPORT_DIR to a tempdir so we don't trash the user's real config
_TMP = Path(tempfile.mkdtemp(prefix="chainproxy_helpers_"))
if sys.platform == "win32":
    os.environ["APPDATA"] = str(_TMP)
else:
    # macOS uses ~/Library/Application Support/ChainProxy/ which we can't
    # easily redirect — skip the seed-into-runtime test on macOS by writing
    # to a tempdir directly through the underlying _common helper.
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chainproxy_core as core  # noqa: E402
import core._common as common  # noqa: E402

# 1. seed_geodata with a missing bundle dir: must be no-op, no crash
n = common.seed_geodata(None, _TMP)
assert n == [], f"seed_geodata(None) should return [], got {n!r}"

n = common.seed_geodata(_TMP / "does_not_exist", _TMP)
assert n == [], f"seed_geodata(missing) should return [], got {n!r}"
print("[helpers] seed_geodata no-op when bundle dir missing: OK")

# 2. seed_geodata copies real files when present
src_dir = _TMP / "bundle"
src_dir.mkdir(parents=True, exist_ok=True)
runtime_dir = _TMP / "runtime"
# Create dummy files large enough to pass the >0 byte check
for fn in ("Country.mmdb", "geoip.dat", "geosite.dat"):
    (src_dir / fn).write_bytes(b"FAKE_GEODATA_FOR_TEST" * 100)
seeded = common.seed_geodata(src_dir, runtime_dir)
assert set(seeded) == {"Country.mmdb", "geoip.dat", "geosite.dat"}, \
    f"expected all three seeded, got {seeded!r}"
for fn in ("Country.mmdb", "geoip.dat", "geosite.dat"):
    assert (runtime_dir / fn).exists()
    assert (runtime_dir / fn).stat().st_size > 0
print("[helpers] seed_geodata copies bundled files: OK")

# 3. seed_geodata is idempotent — second call shouldn't reseed anything
seeded2 = common.seed_geodata(src_dir, runtime_dir)
assert seeded2 == [], f"second seed should be no-op, got {seeded2!r}"
print("[helpers] seed_geodata idempotent: OK")

# 4. seed_geodata reseeds zero-byte files (mihomo's "delete and retry" leaves
#    these). This guards against the original failure mode.
(runtime_dir / "Country.mmdb").write_bytes(b"")
seeded3 = common.seed_geodata(src_dir, runtime_dir)
assert "Country.mmdb" in seeded3, \
    f"empty file should be reseeded, got {seeded3!r}"
print("[helpers] seed_geodata reseeds empty files: OK")

# 5. detect_first_hop_processes on a non-loopback host: empty
if hasattr(core, "detect_first_hop_processes"):
    result = core.detect_first_hop_processes("8.8.8.8", 53)
    assert result == [], \
        f"non-loopback host should return [], got {result!r}"
    print("[helpers] detect_first_hop_processes on remote host: OK")

    # 6. detect_first_hop_processes on a port nobody owns: empty (using a
    #    high port unlikely to be in use). We pick an ephemeral free one.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()
    # Tiny race — but on virtually all systems nobody else binds in this
    # microsecond window
    result = core.detect_first_hop_processes("127.0.0.1", free_port)
    assert result == [], \
        f"unowned port should return [], got {result!r}"
    print("[helpers] detect_first_hop_processes on unowned port: OK")

    # 7. detect_first_hop_processes on a port we just opened: must return
    #    at least our own python process (the one running this test)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    held_port = listener.getsockname()[1]
    try:
        names = core.detect_first_hop_processes("127.0.0.1", held_port)
        # name_should_skip filters python.exe / powershell.exe out of the
        # whitelist (they're shells, not airport-client cores). On Windows
        # the py.exe launcher survives because it is not in _NEVER_WHITELIST.
        # On macOS the listener self_name `python` / `python3` IS filtered,
        # so the result may legitimately be empty — but children/parent
        # references can still surface non-shell process names.
        if sys.platform == "win32":
            assert names, \
                f"port {held_port} held by us, expected non-empty list, got {names!r}"
            # Make sure we did NOT leak shell noise
            for n in names:
                assert "python.exe" != n.lower(), f"python.exe leaked: {names!r}"
                assert "powershell" not in n.lower(), \
                    f"powershell.exe leaked: {names!r}"
                assert "conhost" not in n.lower(), f"conhost.exe leaked: {names!r}"
        print(f"[helpers] detect_first_hop_processes on owned port: OK ({names})")
    finally:
        listener.close()
else:
    print("[helpers] detect_first_hop_processes: not exposed on this platform (skipped)")

# 8. core exposes the new symbols
assert hasattr(core, "seed_geodata"), "core.seed_geodata missing"
assert hasattr(core, "BUNDLED_GEODATA_DIR"), "core.BUNDLED_GEODATA_DIR missing"
print("[helpers] core surface includes new symbols: OK")

# 9. socks5_handshake_succeeds returns True for a fake SOCKS5 server,
#    False for a plain TCP listener / non-SOCKS5 service. Crucial because
#    the fallback path (root-owned listener detection) hinges on this probe.
import threading  # noqa: E402

def _fake_socks5_server(host="127.0.0.1"):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind((host, 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, _ = srv.accept()
            conn.settimeout(1.0)
            try:
                hello = conn.recv(3)
                if hello == b"\x05\x01\x00":
                    conn.sendall(b"\x05\x00")
                else:
                    conn.sendall(b"\x05\xff")
            finally:
                conn.close()
        except OSError:
            pass
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return port

socks5_port = _fake_socks5_server()
assert common.socks5_handshake_succeeds("127.0.0.1", socks5_port, timeout=1.0), \
    "SOCKS5 server should respond to handshake"
print("[helpers] socks5_handshake_succeeds on real SOCKS5: OK")

plain = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
plain.bind(("127.0.0.1", 0))
plain.listen(1)
plain_port = plain.getsockname()[1]
try:
    assert not common.socks5_handshake_succeeds("127.0.0.1", plain_port, timeout=0.5), \
        "plain TCP listener should fail SOCKS5 probe (no server response)"
    print("[helpers] socks5_handshake_succeeds rejects plain TCP: OK")
finally:
    plain.close()

# 10. Process-name patterns identify common airport clients but skip shells.
assert common.name_looks_like_airport_client("FastLink机场")
assert common.name_looks_like_airport_client("AtlasCore_arm64")
assert common.name_looks_like_airport_client("clash-verge")
assert common.name_looks_like_airport_client("verge-mihomo")
assert common.name_looks_like_airport_client("mihomo")
assert common.name_looks_like_airport_client("ClashX Pro")
assert common.name_looks_like_airport_client("Mihomo Party")
assert common.name_looks_like_airport_client("v2rayN.exe")
# Windows reports Win32_Process names with .exe — bare-core patterns are
# anchored with $ so the matcher must strip the suffix.
assert common.name_looks_like_airport_client("mihomo.exe")
assert common.name_looks_like_airport_client("v2ray.exe")
assert common.name_looks_like_airport_client("xray.exe")
assert common.name_looks_like_airport_client("sgw.exe")
assert common.name_looks_like_airport_client("Stash.exe")
assert not common.name_looks_like_airport_client("python3")
assert not common.name_looks_like_airport_client("WeChat")
assert not common.name_looks_like_airport_client("")
assert common.name_should_skip("bash")
assert common.name_should_skip("python3")
assert common.name_should_skip("EXPLORER.EXE")
# Windows console / shell noise must be filtered (.exe suffix stripped on lookup)
assert common.name_should_skip("conhost.exe")
assert common.name_should_skip("python.exe")
assert common.name_should_skip("svchost.exe")
assert common.name_should_skip("powershell.exe")
assert not common.name_should_skip("FastLink机场")
print("[helpers] airport-client name patterns: OK")

# Cleanup
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)
print("\n[helpers] ALL TESTS PASSED")
