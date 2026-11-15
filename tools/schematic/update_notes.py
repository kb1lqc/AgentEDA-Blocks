"""Update a bench's schematic engineering notes from its analysis values (deterministic, no model).

Usage (repo root):  python3 tools/schematic/update_notes.py benches/<Block>
Reads <bench>/docs/design-analysis/values.json and the rules in <bench>/notes_rules.py (`rules(v, f, NUM)` returns
{relative .kicad_sch path: [(regex, replacement), ...]}). Each pattern must match exactly once, or the script stops
without writing anything. Run after the block analysis, then gate: `pcb apply schematic --no-open` twice, netlist
diff, `pcb build -Dwarnings`, ERC, tools/schematic/check_sync.sh.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit(__doc__)
bench = Path(sys.argv[1]).resolve()
v = json.load(open(bench / "docs/design-analysis/values.json"))
spec = importlib.util.spec_from_file_location("notes_rules", bench / "notes_rules.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def f(x, n):
    return f"{x:.{n}f}"


out = {}
for rel, rules in mod.rules(v, f, r"[0-9.]+").items():
    s = (bench / rel).read_text()
    for pat, rep in rules:
        hits = re.findall(pat, s)
        if len(hits) != 1:
            sys.exit(f"{rel}: pattern {pat!r} matched {len(hits)} times; nothing written")
        if hits[0] != rep:
            print(f"{rel}: {hits[0]!r} -> {rep!r}")
        s = re.sub(pat, lambda _m: rep, s)
    out[rel] = s
for rel, s in out.items():
    (bench / rel).write_text(s)
print("notes up to date")
