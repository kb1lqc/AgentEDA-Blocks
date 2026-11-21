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

ZL_VERSION = "v0.7.0"
ZL = Path(os.environ.get("ZENER_LIBRARY", Path(__file__).resolve().parent.parent.parent / "zener-library")).resolve()

if not (ZL / "lib" / "__init__.py").exists():
    raise SystemExit(
        f"zener-library not found at {ZL}. Clone it:\n"
        f"  git clone --branch {ZL_VERSION} https://github.com/kb1lqc/zener-library.git {ZL}\n"
        "or set ZENER_LIBRARY to an existing checkout."
    )

def porcelain_clean(porcelain):
    """True when `git status --porcelain` output shows no tracked change and no untracked file outside .claude/
    (the same rule as tools/check_env.py: local Claude skills in the checkout do not change results)."""
    return all(line.startswith("?? .claude/") for line in porcelain.splitlines() if line.strip())


def zl_git(*args):
    """git in the library checkout; GIT_* dropped so a hook's GIT_DIR / GIT_INDEX_FILE cannot redirect it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(ZL), *args], capture_output=True, text=True, env=env).stdout.strip()


def zl_released():
    """True when the checkout is clean (porcelain_clean) and HEAD carries the ZL_VERSION tag."""
    return ZL_VERSION in zl_git("tag", "--points-at", "HEAD").split() and porcelain_clean(zl_git("status", "--porcelain"))


if not zl_released():
    print(
        f"WARNING: {ZL} is not a clean checkout of {ZL_VERSION}; results may not match the Zener dependencies.",
        file=sys.stderr,
    )

if str(ZL) not in sys.path:
    sys.path.insert(0, str(ZL))
