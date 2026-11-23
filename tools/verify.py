#!/usr/bin/env python3
"""The whole after-change chain for circuit-blocks as one command. Exit 0 = PASS, 1 = any FAIL.

Usage (repo root):
  python3 tools/verify.py [BENCH ...] [--log-dir D] [--root R]
  python3 tools/verify.py --schematic BENCH [--zl DIR] [--log-dir D] [--root R]
BENCH is a name (INA229HighSideMonitor) or a path to benches/<Block>/bench.zen; none given = every benches/*/bench.zen
(zero benches = FAIL).

Default chain, each step judged by exit code:
  tools/check_env.py -> `pcb build -Dwarnings <bench>` per bench (one retry when the first build fails with no
  Error/Warning line, logged "retried") -> tools/check_reports.py -> tools/bind.py (topology preconditions, design
  values, design outputs, spec account; docs/spec/blocks.md "Binding") -> `zsh tools/schematic/check_sync.sh <benches>`.
--schematic BENCH: the deferred schematic gates, all on TEMP COPIES (rsync, never the repo):
  `pcb apply schematic --no-open` twice (the second must print "schematic unchanged"); kicad-cli netlist of the
  committed sheets vs of the applied copy with zener-library tools/schematic/netcmp.py (identical, and not zero nets);
  ERC on the root sheet with kicad-cli: wire_dangling, unconnected_wire_endpoint, pin_not_connected and
  hier_label_mismatch are fatal (docs/spec/pcb-toolchain.md "ERC"), every other class is counted; overlap.py on every
  sheet. kicad-cli: $KICAD_CLI or the macOS default. zener-library: --zl, $ZENER_LIBRARY or ../zener-library.
Output: one PASS line with counts, or the first 5 failures as `file:line message` (`step: line` when no location).
Full logs, one file per step incl. `pcb --version`: --log-dir (default: a new $TMPDIR/cb-logs-* per run); the path is printed last.
__pycache__ directories are removed afterwards.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_FAILURES = 5
LOC_RE = re.compile(r"^(\S+?\.\w+):(\d+)(?::\d+)?:?\s+(.*\S)")
BAD_RE = re.compile(r"\berror\b|\bwarning\b|\bwarn\b|\bfail|✗|Traceback|DRIFT|MISMATCH|DIFF|differs|conflict", re.I)
DIAG_RE = re.compile(r"\b(error|warning)\b", re.I)
FATAL_ERC = ("wire_dangling", "unconnected_wire_endpoint", "pin_not_connected", "hier_label_mismatch")
KICAD_DEFAULT = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
APPLY_RE = re.compile(r"schematic (unchanged|updated|created)")


def run_cmd(cmd, cwd, env=None):
    """Run a command; return (rc, combined output). A command that cannot start is rc 127, never a pass."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    except OSError as e:
        return 127, f"cannot start {cmd[0]}: {e}"
    return p.returncode, p.stdout + p.stderr


def failure_lines(step, out, limit=MAX_FAILURES):
    found = []
    for line in out.splitlines():
        line = line.strip()
        if not line or "fontconfig" in line.lower() or not BAD_RE.search(line):
            continue
        m = LOC_RE.match(line)
        found.append(f"{m.group(1)}:{m.group(2)} {m.group(3)}" if m else f"{step}: {line}")
        if len(found) == limit:
            break
    return found or [f"{step}: failed with no diagnostic line (see log)"]


def classify_erc(report, fatal=FATAL_ERC):
    """Parse a KiCad ERC .rpt. Returns (counts by class, [fatal message lines]); (None, ...) when it is not a report."""
    if "ERC report" not in report:
        return None, ["ERC report missing or not an ERC report"]
    counts, bad = {}, []
    for line in report.splitlines():
        m = re.match(r"^\[(\w+)\]:\s*(.*)", line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            if m.group(1) in fatal:
                bad.append(f"{m.group(1)}: {m.group(2)}")
    return counts, bad


def bench_zens(root, names):
    """Resolve bench names or paths to repo-relative bench.zen paths; [] when none exist."""
    root = Path(root)
    if not names:
        return sorted(str(p.relative_to(root)) for p in root.glob("benches/*/bench.zen"))
    out = []
    for n in names:
        p = Path(n)
        rel = str(p) if p.name == "bench.zen" else f"benches/{p.name}/bench.zen"
        out.append(rel)
    return out


def zl_dir(root, zl=None):
    return Path(zl or os.environ.get("ZENER_LIBRARY") or Path(root).parent / "zener-library").resolve()


class Chain:
    def __init__(self, root, log_dir, runner):
        self.root, self.runner = Path(root), runner
        # a fresh directory per run, so two runs at once never share logs (circuit-blocks #8)
        self.log_dir = Path(log_dir) if log_dir else Path(tempfile.mkdtemp(prefix="cb-logs-"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.fails, self.counts, self.notes, self.n = [], {}, [], 0

    def log(self, name, text):
        self.n += 1
        (self.log_dir / f"{self.n:02d}-{name}.log").write_text(text)

    def step(self, name, cmd, cwd=None, env=None):
        rc, out = self.runner(cmd, cwd or self.root) if env is None else self.runner(cmd, cwd or self.root, env)
        self.log(name, f"$ {' '.join(map(str, cmd))}\nexit {rc}\n{out}")
        if rc:
            self.fails.extend(failure_lines(name, out))
        return rc, out

    def fail(self, msg):
        self.fails.append(msg)

    def pcb_version(self):
        rc, out = self.runner(["pcb", "--version"], self.root)
        self.log("pcb-version", f"exit {rc}\n{out}")
        if rc:
            self.fail(f"pcb --version: cannot run pcb (exit {rc})")

    def finish(self):
        lines = self.fails[:MAX_FAILURES]
        if self.fails:
            lines = [f"FAIL: {len(self.fails)} failure line(s), first {len(lines)}"] + lines
        else:
            lines = ["PASS: " + ", ".join(f"{k} {v}" for k, v in self.counts.items())
                     + (f"; {'; '.join(self.notes)}" if self.notes else "")]
        lines.append(f"logs: {self.log_dir}")
        return (1 if self.fails else 0), lines


def verify(root, names=(), log_dir=None, runner=run_cmd):
    c = Chain(root, log_dir, runner)
    zens = bench_zens(root, names)
    if not zens:
        c.fail("benches: no benches/*/bench.zen found; nothing was verified")
        return c.finish()
    for z in zens:
        if not (Path(root) / z).is_file():
            c.fail(f"{z}:1 bench.zen not found")
    if c.fails:
        return c.finish()
    c.pcb_version()
    if c.fails:
        return c.finish()
    if c.step("check_env", [sys.executable, "tools/check_env.py"])[0]:
        return c.finish()
    retried = 0
    built = 0
    for z in zens:
        name = "build-" + Path(z).parent.name
        rc, out = runner(["pcb", "build", "-Dwarnings", z], c.root)
        if rc and not DIAG_RE.search(out):
            c.log(name + "-first", f"exit {rc}\n{out}")
            retried += 1
            rc, out = runner(["pcb", "build", "-Dwarnings", z], c.root)
        c.log(name, f"$ pcb build -Dwarnings {z}\nexit {rc}\n{out}")
        if rc:
            c.fails.extend(failure_lines(name, out))
        else:
            built += 1
    c.counts["benches"] = f"{built}/{len(zens)} built"
    if retried:
        c.notes.append(f"retried {retried} build(s)")
        c.counts["retried"] = retried
    if c.fails:
        return c.finish()
    if c.step("check_reports", [sys.executable, "tools/check_reports.py"])[0]:
        return c.finish()
    rc, out = c.step("bind", [sys.executable, "tools/bind.py"])
    if rc:
        return c.finish()
    m = re.search(r"bind: PASS \((\d+) checked", out)
    c.counts["bound"] = m.group(1) if m else "?"
    unbound = re.findall(r"^UNBOUND (\S+):", out, re.M)
    if unbound:
        c.notes.append("UNBOUND " + ", ".join(unbound))
    rc, out = c.step("check_sync", ["zsh", "tools/schematic/check_sync.sh", *zens])
    if not rc and "IN SYNC" not in out:
        c.fail("check_sync: exit 0 but no 'IN SYNC' line printed")
    elif not rc:
        c.counts["sync"] = "IN SYNC"
    return c.finish()


def verify_schematic(root, bench, zl=None, log_dir=None, runner=run_cmd, kicad=None):
    c = Chain(root, log_dir, runner)
    root = Path(root)
    zen = bench_zens(root, [bench])[0]
    kicad = kicad or os.environ.get("KICAD_CLI") or KICAD_DEFAULT
    zl = zl_dir(root, zl)
    netcmp, overlap = zl / "tools/schematic/netcmp.py", zl / "tools/schematic/overlap.py"
    for need, what in ((root / zen, "bench.zen"), (netcmp, "netcmp.py"), (overlap, "overlap.py")):
        if not need.is_file():
            c.fail(f"{need}:1 {what} not found")
    if shutil.which(kicad) is None and not Path(kicad).is_file():
        c.fail(f"kicad-cli: not found at {kicad} (set KICAD_CLI)")
    if c.fails:
        return c.finish()
    c.pcb_version()
    if c.fails:
        return c.finish()
    tmp = Path(tempfile.mkdtemp(prefix="cb-verify-"))
    try:
        work = tmp / "work"
        work.mkdir()
        if c.step("rsync", ["rsync", "-a", "--exclude", ".git", "--exclude", "*.lck", f"{root}/", f"{work}/"])[0]:
            return c.finish()
        eda = work / Path(zen).parent / "eda"
        roots = sorted(eda.glob("*_bench.kicad_sch"))
        if len(roots) != 1:
            c.fail(f"{eda}:1 expected exactly one *_bench.kicad_sch, found {len(roots)}")
            return c.finish()
        rootsheet = roots[0]
        sheets = sorted(eda.glob("*.kicad_sch"))
        # committed netlist (before any apply)
        before = tmp / "before.net"
        c.step("netlist-committed", [kicad, "sch", "export", "netlist", "--output", str(before), str(rootsheet)], work)
        # apply twice
        outs = []
        for i in (1, 2):
            rc, out = c.step(f"apply-{i}", ["pcb", "apply", "schematic", "--no-open", zen], work)
            m = APPLY_RE.findall(out)
            outs.append(m[-1] if m else None)
        if not c.fails:
            if outs[1] is None:
                c.fail("apply-2: no 'schematic unchanged/updated/created' line printed")
            elif outs[1] != "unchanged":
                c.fail(f"apply-2: second apply reported '{outs[1]}', not 'unchanged' (apply is not idempotent)")
            if outs[0] not in (None, "unchanged"):
                c.notes.append(f"first apply {outs[0]}")
        c.counts["apply"] = "/".join(str(o) for o in outs)
        # netlist diff
        after = tmp / "after.net"
        c.step("netlist-applied", [kicad, "sch", "export", "netlist", "--output", str(after), str(rootsheet)], work)
        if not c.fails:
            for p in (before, after):
                if not p.is_file():
                    c.fail(f"{p.name}:1 netlist was not written")
        if not c.fails:
            rc, out = c.step("netcmp", [sys.executable, str(netcmp), str(before), str(after)], work)
            m = re.search(r"^(\d+) nets baseline", out, re.M)
            if not m or int(m.group(1)) == 0:
                c.fail("netcmp: zero nets in the baseline netlist")
            else:
                c.counts["nets"] = m.group(1)
        # ERC
        rpt = tmp / "erc.rpt"
        c.step("erc", [kicad, "sch", "erc", "-o", str(rpt), str(rootsheet)], work)
        if not c.fails:
            text = rpt.read_text() if rpt.is_file() else ""
            counts, bad = classify_erc(text)
            if counts is None:
                c.fail("erc: " + bad[0])
            else:
                c.fails.extend(f"erc: FATAL {b}" for b in bad)
                other = {k: v for k, v in sorted(counts.items()) if k not in FATAL_ERC}
                c.counts["erc"] = f"{len(bad)} fatal, {sum(other.values())} other"
                if other:
                    c.notes.append("erc other: " + ", ".join(f"{k} {v}" for k, v in other.items()))
        # overlap
        rc, out = c.step("overlap", [sys.executable, str(overlap), *map(str, sheets)], work)
        c.counts["sheets"] = len(sheets)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c.finish()


def clean_pycache(root):
    for base, dirs, _ in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", ".pcb"}]
        for d in [d for d in dirs if d == "__pycache__"]:
            shutil.rmtree(Path(base) / d, ignore_errors=True)
            dirs.remove(d)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("benches", nargs="*")
    ap.add_argument("--schematic", metavar="BENCH")
    ap.add_argument("--zl")
    ap.add_argument("--log-dir")
    ap.add_argument("--root", default=Path(__file__).resolve().parent.parent)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        if a.schematic:
            rc, lines = verify_schematic(root, a.schematic, a.zl, a.log_dir)
        else:
            rc, lines = verify(root, a.benches, a.log_dir)
    finally:
        clean_pycache(root)
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main())
