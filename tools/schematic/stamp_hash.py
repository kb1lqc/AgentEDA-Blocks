#!/usr/bin/env python3
"""Stamp each KiCad sheet's title block with the short hash of the commit that last changed that sheet's content.

    python3 tools/schematic/stamp_hash.py [sheets...]          write: set each sheet's stamp (default benches/*/eda/*.kicad_sch)
    python3 tools/schematic/stamp_hash.py --check [sheets...]  verify: exit 1 if a committed, unchanged sheet carries the wrong stamp

The stamp is the `comment 1` line of the title block, e.g. `github.com/kb1lqc/circuit-blocks @ 66b8398: benches/...`.
Only the hash token changes. Content is compared with the stamp masked, so a stamp-only change never counts as a
content change. own_hash() walks the sheet's history newest first and returns the newest commit whose sheet content
differs from its parent's (a commit that added the sheet counts). Working-tree changes are ignored by own_hash.

After committing sheet changes, the title-block stamps of the committed sheets are stale until stamped:
    python3 tools/schematic/stamp_hash.py
    tools/schematic/export_pdf.sh          (the bench PDFs print the title block, so they change too)
    git commit -m "Stamp sheet hashes"    (stamp and PDFs only)
While a sheet has uncommitted content changes, --check reports it as pending and does not fail.
Stdlib and the git CLI only. Used by benches/*/arrange_sheet.py so a generator re-run never reverts a stamp.
"""
import glob
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# The stamp: the hash token of title-block comment 1 (the title block is `(comment 1 "<url> @ <hash>: <subdir>")`).
STAMP_RE = re.compile(r'(\(comment 1 "[^"\n]*? @ )[0-9a-f]{7,40}(?=:)')
PLACEHOLDER = "0" * 7


def _git(*args, check=True):
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout if r.returncode == 0 else None


def _rel(path):
    return str(Path(path).resolve().relative_to(REPO))


def _show(rev, rel):
    """File content at `rev`, or None if it does not exist there."""
    return _git("show", "%s:%s" % (rev, rel), check=False)


def normalize(text):
    """The sheet text with the stamp hash masked, so only content changes compare."""
    return STAMP_RE.sub(lambda m: m.group(1) + PLACEHOLDER, text)


def read_stamp(text):
    m = STAMP_RE.search(text)
    return m.group(0).rsplit(" @ ", 1)[1] if m else None


def own_hash(path):
    """Short hash of the newest commit that changed this sheet's content (history only, not the working tree)."""
    rel = _rel(path)
    out = _git("log", "--follow", "--format=%x1e%H", "--name-only", "--", rel)
    entries = []  # (sha, path name at that commit), newest first
    for chunk in out.split("\x1e")[1:]:
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        entries.append((lines[0], lines[-1] if len(lines) > 1 else rel))
    for i, (sha, name) in enumerate(entries):
        new = _show(sha, name)
        # The parent's name: the next older entry (differs from `name` across a rename). None for the first commit.
        parent_name = entries[i + 1][1] if i + 1 < len(entries) else None
        old = _show(sha + "^", parent_name) if parent_name and _git("rev-parse", "--verify", "-q", sha + "^", check=False) else None
        if normalize(new or "") != normalize(old or ""):
            return _git("rev-parse", "--short=7", sha).strip()
    raise RuntimeError("no commit changed %s" % rel)


def sheet_stamp(path):
    """The stamp a generator should write for this sheet on disk right now.

    Committed and unchanged: its own hash. Uncommitted content (or not yet committed): keep the stamp it already
    carries, else HEAD's short hash as a placeholder. A generator re-run therefore never reverts a stamp."""
    path = Path(path).resolve()
    text = path.read_text()
    if REPO not in path.parents:  # a scratch copy outside the repo has no history: keep its stamp
        return read_stamp(text) or _git("rev-parse", "--short=7", "HEAD").strip()
    rel = _rel(path)
    head = _show("HEAD", rel)
    if head is not None and normalize(head) == normalize(text):
        return own_hash(path)
    return read_stamp(text) or _git("rev-parse", "--short=7", "HEAD").strip()


def title_comment(sheet_path, subdir):
    """The title block comment 1 for a sheet: 'host/owner/repo @ <stamp>: <subdir>' (same form as place.repo_comment)."""
    url = _git("remote", "get-url", "origin").strip()
    url = re.sub(r"\.git$", "", re.sub(r"^(https?://|git@)", "", url).replace(":", "/"))
    return "%s @ %s%s" % (url, sheet_stamp(sheet_path), (": " + subdir) if subdir else "")


def _sheets(args):
    if args:
        return [str(Path(a).resolve()) for a in args]
    return sorted(glob.glob(str(REPO / "benches" / "*" / "eda" / "*.kicad_sch")))


def write(paths):
    changed = []
    for p in paths:
        path = Path(p)
        text = path.read_text()
        if not STAMP_RE.search(text):
            raise SystemExit("%s: no title-block stamp (comment 1) to set" % _rel(path))
        h = own_hash(path)
        new = STAMP_RE.sub(lambda m: m.group(1) + h, text, count=1)
        if new != text:
            path.write_text(new)
            changed.append(_rel(path))
        print("%s: %s" % (_rel(path), h))
    for c in changed:
        print("changed: " + c)
    if not changed:
        print("no sheet changed")
    return 0


def check(paths):
    fail = 0
    for p in paths:
        path = Path(p)
        rel = _rel(path)
        text = path.read_text()
        head = _show("HEAD", rel)
        if head is None or normalize(head) != normalize(text):
            print("%s: pending stamp (content changed; stamp after commit)" % rel)
            continue
        stamped = read_stamp(text)
        expected = own_hash(path)
        if stamped != expected:
            print("%s: STALE stamp %s, expected %s (run tools/schematic/stamp_hash.py)" % (rel, stamped, expected))
            fail = 1
        else:
            print("%s: OK %s" % (rel, stamped))
    return fail


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--check" in args:
        args.remove("--check")
        sys.exit(check(_sheets(args)))
    sys.exit(write(_sheets(args)))
