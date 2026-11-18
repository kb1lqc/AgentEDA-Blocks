#!/usr/bin/env python3
"""Claude Code PostToolUse hook (matcher Edit|Write|MultiEdit): format an edited .zen file with `pcb fmt`.

Reads the hook JSON on stdin (tool_input.file_path). Silent and exit 0 when all is well. On a failure it prints only the
failing lines to stderr and exits 2 (Claude Code shows stderr of exit 2 to the model).
  *.zen inside this repo            -> `pcb fmt <file>` (the only file this hook ever modifies)
  anything else, a path outside the repo, a missing file -> nothing (exit 0)
A command that cannot start is a failure (exit 2), never a silent pass. Wiring: .claude/settings.json.
Usage: python3 tools/hook_post_edit.py [--root R] < hook.json
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path



def run_cmd(cmd, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, f"cannot run {cmd[0]}: {e}"
    return p.returncode, p.stdout + p.stderr


def handle(payload, root, runner=run_cmd):
    """(exit code, stderr text) for one hook payload."""
    root = Path(root).resolve()
    path = (payload.get("tool_input") or {}).get("file_path")
    if not path:
        return 0, ""
    p = Path(path)
    p = (p if p.is_absolute() else root / p).resolve()
    try:
        rel = p.relative_to(root)
    except ValueError:
        return 0, ""
    if not p.is_file() or ".pcb" in rel.parts or rel.parts[0] == ".git":
        return 0, ""
    if p.suffix == ".zen":
        rc, out = runner(["pcb", "fmt", str(p)], root)
        return (0, "") if rc == 0 else (2, f"pcb fmt {rel}: failed\n" + "\n".join(out.splitlines()[-5:]))
    return 0, ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=Path(__file__).resolve().parent.parent)
    a = ap.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        print("hook_post_edit: stdin is not JSON", file=sys.stderr)
        return 2
    rc, msg = handle(payload, a.root)
    if msg:
        print(msg, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
