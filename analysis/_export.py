"""Write the library rows an analysis read, for the library's `tools/review.py --block <Block>`.

    from analysis._export import write_library_inputs
    write_library_inputs(FIG_DIR)  # last, after every part.param() read

Output `design-analysis/library-inputs.json` (generated, committed, compared by tools/check_reports.py): sorted list of
{"value": <component dir name>, "key": <param key>, "input": "<label>:<key>", "dir": "components/<Mfr>/<Value>"} from
lib.used_inputs(). The label is mapped to its directory by name (a variant Value to its family; "[selection]" dropped);
"dir" is left out when no single directory matches.
"""

import json
import tomllib
from pathlib import Path

from analysis._zl import ZL


def component_dir(zl, label):
    """components/<Mfr>/<Value> of an input label, or None (absent or ambiguous)."""
    base = label.split("[")[0]
    comps = Path(zl) / "components"
    found = sorted(p.parent for p in comps.glob(f"*/{base}/component.toml"))
    if not found:
        found = [f.parent for f in sorted(comps.glob("*/*/variants.toml"))
                 if base in tomllib.loads(f.read_text()).get("variant", {})]
    return found[0] if len(found) == 1 else None


def library_inputs(inputs, zl=ZL):
    rows = []
    for inp in inputs:
        label, _, key = inp.rpartition(":")
        d = component_dir(zl, label)
        row = {"value": d.name if d else label.split("[")[0], "key": key, "input": inp}
        if d:
            row["dir"] = d.relative_to(Path(zl)).as_posix()
        rows.append(row)
    return sorted(rows, key=lambda r: (r["value"], r["key"], r["input"]))


def review_counts():
    """(rows read, rows with a current human stamp) for the rows read so far, judged from the library's review ledger
    only (no local PDF check), so the count is the same on every machine. Uses zener-library lib.component internals
    (_ROWS, _review(local_pdf=False)); fails loudly if a release changes them."""
    import lib.component as c
    from lib import used_inputs
    rows = c._ROWS
    need = [k for k, e in rows.items() if c._review(e["dir"], e["review_key"], e["stamp"], local_pdf=False)[1]]
    n = len(used_inputs())
    if set(rows) != set(used_inputs()):
        raise RuntimeError("zener-library review record (_ROWS) does not cover every row read; update review_counts")
    return n, n - len(need)


def write_library_inputs(fig_dir):
    from lib import used_inputs
    path = Path(fig_dir) / "library-inputs.json"
    path.write_text(json.dumps(library_inputs(used_inputs()), indent=1) + "\n")
    return path
