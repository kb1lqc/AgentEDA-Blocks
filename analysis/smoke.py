"""Smoke test: the pinned library loads and the INA229 data is reachable.

Run from the repo root:   python3 -m analysis.smoke
"""

from analysis import _zl  # noqa: F401
from lib import load

ina = load("TexasInstruments/INA229AIDGS")
vs = ina.param("vs_rec")
print(f"INA229AIDGS loaded from {_zl.ZL.name}: VS recommended {vs.min} to {vs.max} {vs.unit}")
