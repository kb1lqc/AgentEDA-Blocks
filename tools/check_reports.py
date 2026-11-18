#!/usr/bin/env python3
"""Fail-closed check of the generated bench reports. Exit 0 = pass, 1 = any violation.

Usage (repo root): python3 tools/check_reports.py [--root DIR] [--zl DIR]

For every benches/<Block>/ (with bench.zen) the committed report is regenerated into a TEMP COPY of the repo (rsync,
never into the repo) by `python3 blocks/<Block>/analysis.py` with its defaults, then compared byte for byte:
  benches/<Block>/docs/design-analysis.md
  benches/<Block>/docs/design-analysis/values.json
The PDF (benches/<Block>/docs/design-analysis.pdf) is not compared: it is not byte-reproducible (known finding;
check_sync.sh verifies its embedded hash of the Markdown instead).

Rules (ids for tools/known_findings.toml):
  REP-NO-BENCHES  zero benches found
  REP-GENERATE    blocks/<Block>/analysis.py missing or exits nonzero in the copy
  REP-STALE       a committed file is missing or differs from the regenerated one
  REP-VALUES      values.json missing, not valid JSON, or empty
Not checked (abstained): required report sections. docs/spec/analysis-report.md lists nine content topics without fixed
heading text, and the two committed reports number and word their headings differently; any heading test would be a guess.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _findings import Finding, load_allowlist, report  # noqa: E402
from check_env import default_zl  # noqa: E402

RULES = {"REP-NO-BENCHES", "REP-GENERATE", "REP-STALE", "REP-VALUES"}
COMPARED = ("design-analysis.md", "design-analysis/values.json")


def benches(root):
    return sorted(p.parent.name for p in (root / "benches").glob("*/bench.zen"))


def copy_repo(root, dest):
    cmd = ["rsync", "-a", "--exclude", ".git", "--exclude", ".pcb", "--exclude", "*.lck", "--exclude", "__pycache__",
           f"{root}/", f"{dest}/"]
    if subprocess.run(cmd, capture_output=True).returncode:
        raise SystemExit("rsync of the repo to a temp dir failed")


def check_values(path, rel):
    if not path.is_file():
        return [Finding("REP-VALUES", rel, "", "values.json missing")]
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        return [Finding("REP-VALUES", rel, "", f"not valid JSON ({e})")]
    if not data:
        return [Finding("REP-VALUES", rel, "", "values.json is empty")]
    return []


RESULTS_DATE = re.compile(rb"^- Results date: \d{4}-\d{2}-\d{2}\.$", re.M)


def _masked(path):
    """File bytes with the report's own results-date line blanked: the only content allowed to differ between runs."""
    return RESULTS_DATE.sub(b"- Results date: <masked>.", path.read_bytes())


def run(root, zl, python=sys.executable):
    root = Path(root)
    names = benches(root)
    if not names:
        return [Finding("REP-NO-BENCHES", "benches/*/bench.zen", "", "no benches found")], 0
    findings = []
    with tempfile.TemporaryDirectory(prefix="check_reports_") as tmp:
        copy_repo(root, tmp)
        env = dict(os.environ, ZENER_LIBRARY=str(zl), PYTHONDONTWRITEBYTECODE="1")
        for name in names:
            docs = f"benches/{name}/docs/"
            script = Path(tmp) / "blocks" / name / "analysis.py"
            findings += check_values(root / docs / "design-analysis" / "values.json", docs + "design-analysis/values.json")
            if not script.is_file():
                findings.append(Finding("REP-GENERATE", f"blocks/{name}/analysis.py", "", "analysis.py missing"))
                continue
            for rel in COMPARED:  # remove any committed copy so a script that writes nothing cannot pass
                (Path(tmp) / docs / rel).unlink(missing_ok=True)
            r = subprocess.run([python, str(script)], cwd=tmp, env=env, capture_output=True, text=True)
            if r.returncode:
                findings.append(Finding("REP-GENERATE", f"blocks/{name}/analysis.py", "",
                                        f"exit {r.returncode}: {(r.stderr or r.stdout).strip().splitlines()[-1:]}"))
                continue
            for rel in COMPARED:
                old, new = root / docs / rel, Path(tmp) / docs / rel
                if not old.is_file():
                    findings.append(Finding("REP-STALE", docs + rel, "", "committed file missing"))
                elif not new.is_file():
                    findings.append(Finding("REP-STALE", docs + rel, "", "analysis.py did not regenerate it"))
                elif _masked(old) != _masked(new):
                    findings.append(Finding("REP-STALE", docs + rel, "",
                                            f"differs from a fresh run; run python3 blocks/{name}/analysis.py and review"))
    return findings, len(names)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--zl")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    zl = Path(a.zl).resolve() if a.zl else default_zl(root)
    findings, checked = run(root, zl)
    return report("check_reports", findings, load_allowlist(root / "tools" / "known_findings.toml"), RULES, checked)


if __name__ == "__main__":
    sys.exit(main())
