"""ChainProxy core — backwards-compat shim.

The real implementation lives in `core/` and is dispatched by platform
(see core/__init__.py). This module exists only so that existing code (the
GUI, the .app bundle's Resources folder, third-party scripts) can keep using
`import chainproxy_core as core` exactly as before.
"""

from core import *  # noqa: F401, F403
