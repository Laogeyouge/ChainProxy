"""ChainProxy core — platform-dispatching facade.

Imports the platform-specific backend at module load time. Anything the GUI
(or chainproxy_core.py shim) needs is re-exported here.
"""
import sys

if sys.platform == "darwin":
    from ._macos import *  # noqa: F401, F403
    from ._macos import (  # explicit re-export for names not in __all__
        HELPER_PATH, HELPER_VERSION, helper_installed, install_helper,
    )
elif sys.platform == "win32":
    from ._windows import *  # noqa: F401, F403
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")
