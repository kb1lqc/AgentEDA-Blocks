"""Put a zener-library checkout on sys.path so analyses can `from lib import ...`.

Usage at the top of an analysis script:

    from analysis import _zl  # noqa: F401  (side effect: sys.path)
    from lib import Param, load, table, wca

The checkout lives OUTSIDE this repo (default: ../zener-library, override with $ZENER_LIBRARY) because pcb rejects
any nested pcb.toml that has a [workspace] section, even in hidden or git-ignored folders.

ZL_VERSION must match the Zener dependency versions in pcb.toml, so the Python numbers and the Zener wrappers come
from the same component.toml. Bump both together.
"""

import os
import subprocess
import sys
from pathlib import Path

ZL_VERSION = "v0.4.0"
ZL = Path(os.environ.get("ZENER_LIBRARY", Path(__file__).resolve().parent.parent.parent / "zener-library")).resolve()

if not (ZL / "lib" / "__init__.py").exists():
    raise SystemExit(
        f"zener-library not found at {ZL}. Clone it:\n"
        f"  git clone --branch {ZL_VERSION} https://github.com/kb1lqc/zener-library.git {ZL}\n"
        "or set ZENER_LIBRARY to an existing checkout."
    )

_tags = subprocess.run(["git", "-C", str(ZL), "tag", "--points-at", "HEAD"], capture_output=True, text=True).stdout
_dirty = subprocess.run(["git", "-C", str(ZL), "status", "--porcelain"], capture_output=True, text=True).stdout
if ZL_VERSION not in _tags.split() or _dirty.strip():
    print(
        f"WARNING: {ZL} is not a clean checkout of {ZL_VERSION}; results may not match the Zener dependencies.",
        file=sys.stderr,
    )

if str(ZL) not in sys.path:
    sys.path.insert(0, str(ZL))
