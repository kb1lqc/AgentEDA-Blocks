"""Shared finding + allowlist logic for tools/check_env.py and tools/check_reports.py (stdlib only).

A check yields Finding(rule, file, location, message). tools/known_findings.toml lists accepted violations:

    [[finding]]
    file = "<path relative to the repo root>"
    location = "<key or location>"
    rule = "<rule id>"
    reason = "<one line>"
    found = "2026-11-17"

Only an exact (rule, file, location) match is allowed. An entry of a rule this run covers that matches nothing is
STALE and fails the run, so the list can only shrink.
"""

import tomllib
from collections import namedtuple
from pathlib import Path

Finding = namedtuple("Finding", "rule file location message")


def load_allowlist(path):
    path = Path(path)
    if not path.is_file():
        return []
    return tomllib.loads(path.read_text()).get("finding", [])


def apply_allowlist(findings, entries, rules):
    """Return (violations, allowed, stale_entries). `rules` = rule ids this run covers (others' entries are ignored)."""
    keys = {(e.get("rule"), e.get("file"), e.get("location")): e for e in entries if e.get("rule") in rules}
    used, violations, allowed = set(), [], []
    for f in findings:
        k = (f.rule, f.file, f.location)
        if k in keys:
            used.add(k)
            allowed.append(f)
        else:
            violations.append(f)
    stale = [e for k, e in keys.items() if k not in used]
    return violations, allowed, stale


def report(name, findings, entries, rules, checked):
    """Print the result and return the exit code (0 pass, 1 fail). `checked` = count of things examined (0 fails)."""
    violations, allowed, stale = apply_allowlist(findings, entries, rules)
    if checked == 0:
        violations.append(Finding("EMPTY", "", "", "nothing was checked (fail closed)"))
    for v in violations:
        print(f"FAIL [{v.rule}] {v.file} {v.location}: {v.message}")
    for e in stale:
        print(f"FAIL [STALE-ALLOWLIST] {e.get('file')} {e.get('location')} rule {e.get('rule')}: entry matches nothing; delete it")
    for a in allowed:
        print(f"allowlisted [{a.rule}] {a.file} {a.location}")
    bad = len(violations) + len(stale)
    print(f"{name}: {'FAIL' if bad else 'PASS'} ({checked} checked, {len(violations)} violations, "
          f"{len(stale)} stale allowlist entries, {len(allowed)} allowlisted findings)")
    return 1 if bad else 0
