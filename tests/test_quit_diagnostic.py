"""Diagnostic test for the Windows tray-quit hang.

User report: in TUN mode, after right-clicking the tray icon and choosing
"退出 ChainProxy", the GUI window goes away but ChainProxy.exe persists in
Task Manager. We need to find WHAT is keeping the Python interpreter alive.

Strategy:
  1. Build a real MainWindow offscreen (skip main()'s mutex check by going
     direct).
  2. Mock mihomo as "running" with a fake live Popen — so closeEvent's
     "runner.is_running()" branch is exercised exactly like real life.
  3. Snapshot all threading.enumerate() + Qt top-level widgets BEFORE
     close, AFTER close, and AFTER app.exec() returns.
  4. Run the parent harness as a subprocess with a hard kill at 8s, so a
     real hang is observable externally as "process didn't exit".
"""
import os
import sys
import time
import tempfile
import shutil
import threading
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INNER_MARKER = "__QUIT_DIAGNOSTIC_CHILD__"


def _inner_run():
    """Runs in the child process. Builds the GUI, fakes a running mihomo,
    triggers tray-quit, and times each phase."""
    # Use real Windows platform (NOT offscreen) — the bug only repros there.
    # `minimal` uses the native Win32 message pump but doesn't show a window
    # by default; we still get real QSystemTrayIcon behavior.
    platform = os.environ.get("DIAG_PLATFORM", "windows")
    os.environ["QT_QPA_PLATFORM"] = platform
    tmp = Path(tempfile.mkdtemp(prefix="cp_quit_"))
    os.environ["APPDATA"] = str(tmp)
    sys.path.insert(0, str(REPO))

    def log(msg):
        print(f"[child {time.monotonic():.3f}] {msg}", flush=True)

    # Force stdout to utf-8 so we can write to a UTF-8 pipe even when the
    # default Windows console codec is GBK.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    log("BOOT")
    import core  # noqa: F401
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QFrame
    import traceback as _tb

    # Track where each QFrame was constructed so we can identify the
    # parentless top-level ones in the diagnostic dump.
    _orig_qframe_init = QFrame.__init__
    _qframe_origin = {}
    def _qframe_init(self, *args, **kwargs):
        _orig_qframe_init(self, *args, **kwargs)
        stack = _tb.extract_stack()
        # stack[-1] = this _qframe_init body. Walk back to first caller
        # outside this test file.
        for fr in reversed(stack[:-1]):
            if "test_quit_diagnostic" not in fr.filename:
                _qframe_origin[id(self)] = f"{Path(fr.filename).name}:{fr.lineno}"
                break
    QFrame.__init__ = _qframe_init

    import chainproxy_qt as gui

    QApplication.setApplicationName("ChainProxy")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    log("QApplication created")

    w = gui.MainWindow(app)
    w.show()
    app.processEvents()
    log(f"MainWindow shown; tray={'yes' if w.tray else 'no'}")

    # Fake a running mihomo. We don't actually start one — that would need
    # admin rights and clobber the user's network. Instead inject a mock
    # Popen-like object that reports "alive" so closeEvent's stop branch
    # actually runs.
    class _FakeProc:
        pid = 0
        def poll(self): return None  # alive
        def wait(self, timeout=None): return 0
    w.runner.proc = _FakeProc()
    # Also pretend we set the system proxy, so toggle path runs (it'll be
    # a no-op against an unreal port — but exercises the code).
    w._we_set_proxy = False  # don't actually clobber registry

    def dump_state(tag):
        threads = threading.enumerate()
        log(f"[{tag}] threads ({len(threads)}):")
        for t in threads:
            log(f"  - {t.name} daemon={t.daemon} alive={t.is_alive()}")
        # Qt top-level widgets
        try:
            tops = app.topLevelWidgets()
            log(f"[{tag}] qt top-level widgets ({len(tops)}):")
            for tw in tops:
                cls = tw.__class__.__name__
                klass = tw.property("class") or ""
                size = f"{tw.width()}x{tw.height()}"
                qco = tw.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose)
                origin = _qframe_origin.get(id(tw), "?")
                log(f"  - {cls} class='{klass}' visible={tw.isVisible()} "
                    f"size={size} WA_QuitOnClose={qco} origin={origin}")
        except Exception as e:
            log(f"[{tag}] top-widget enum failed: {e}")

    dump_state("BEFORE close")

    quit_seen = [False]
    def on_about_quit():
        log("aboutToQuit FIRED")
        quit_seen[0] = True
    app.aboutToQuit.connect(on_about_quit)

    # Schedule the close to happen INSIDE the event loop, the way real
    # life does it. Then watch whether app.exec() returns.
    from PyQt6.QtCore import QTimer

    def do_close():
        log("[event-loop] simulating tray-quit click")
        w._tray_quit = True
        accepted = w.close()
        log(f"[event-loop] w.close() returned {accepted}")
        dump_state("AFTER w.close() (in event loop)")

    QTimer.singleShot(100, do_close)

    # Watchdog: if app.exec() doesn't return within 4s, dump state and
    # bail out to os._exit so the parent harness sees the hang.
    def watchdog():
        log("[watchdog] app.exec() hasn't returned in 4s — quit chain is BROKEN")
        log(f"[watchdog] aboutToQuit fired? {quit_seen[0]}")
        dump_state("WATCHDOG TIMEOUT")
        log("[watchdog] forcing os._exit(99)")
        os._exit(99)

    QTimer.singleShot(4000, watchdog)

    log("entering app.exec()")
    t0 = time.monotonic()
    rc = app.exec()
    elapsed = time.monotonic() - t0
    log(f"app.exec() RETURNED rc={rc} after {elapsed:.3f}s "
        f"(aboutToQuit fired={quit_seen[0]})")
    dump_state("AFTER app.exec() returned")
    # Dump exit-trace.log content to stdout BEFORE cleaning up the tempdir
    trace_path = tmp / "ChainProxy" / "exit-trace.log"
    log(f"---- exit-trace.log @ {trace_path} ----")
    if trace_path.exists():
        for tline in trace_path.read_text(encoding="utf-8").splitlines():
            log(f"  {tline}")
    else:
        log("  (no exit-trace.log written — _exit_trace never called)")
    log("---- end exit-trace.log ----")
    shutil.rmtree(tmp, ignore_errors=True)
    log("END of inner — about to fall off main")


def _parent_harness():
    """Spawn the child and time how long it takes to actually exit."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"[parent {time.monotonic():.3f}] spawning child...", flush=True)
    proc = subprocess.Popen(
        [sys.executable, __file__, INNER_MARKER],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    start = time.monotonic()
    HARD_KILL = 8.0

    # Stream child output as we get it
    while True:
        if proc.poll() is not None:
            break
        if time.monotonic() - start > HARD_KILL:
            print(f"[parent] child still alive at {HARD_KILL}s — HARD KILLING")
            proc.kill()
            break
        line = proc.stdout.readline()
        if line:
            print(line.rstrip(), flush=True)
        else:
            time.sleep(0.05)

    # Drain any remaining output
    rest = proc.stdout.read()
    if rest:
        print(rest, end="", flush=True)

    elapsed = time.monotonic() - start
    rc = proc.returncode
    print(f"[parent] child exited rc={rc} after {elapsed:.2f}s", flush=True)
    if elapsed > 6.0 or rc is None:
        print(f"[parent] *** BUG REPRODUCED: child failed to exit cleanly ***")
        print(f"[parent] expected: <2s clean exit. observed: {elapsed:.2f}s")
        sys.exit(1)
    print(f"[parent] OK: clean exit within budget")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == INNER_MARKER:
        _inner_run()
    else:
        _parent_harness()
