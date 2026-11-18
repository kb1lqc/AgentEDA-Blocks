"""Tests for tools/hook_post_edit.py (fake runner, plus one real pcb fmt in a temp dir)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "hook_post_edit.py"
sys.path.insert(0, str(TOOL.parent))
import hook_post_edit as h  # noqa: E402


def write(p, text=""):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class TestHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.calls, self.reply = [], (0, "")

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, cmd, cwd):
        self.calls.append(cmd)
        return self.reply

    def go(self, path):
        return h.handle({"tool_input": {"file_path": str(path)}}, self.root, self.runner)

    def test_zen_runs_pcb_fmt_on_that_file_only(self):
        f = write(self.root / "benches/B/bench.zen")
        self.assertEqual(self.go(f), (0, ""))
        self.assertEqual(self.calls, [["pcb", "fmt", str(f)]])

    def test_fmt_failure_is_exit_2_and_unstartable_too(self):
        f = write(self.root / "x.zen")
        self.reply = (1, "syntax error line 3\n")
        rc, msg = self.go(f)
        self.assertEqual(rc, 2)
        self.assertIn("syntax error line 3", msg)
        self.reply = (127, "cannot run pcb")
        self.assertEqual(self.go(f)[0], 2)

    def test_other_files_outside_repo_and_missing_are_ignored(self):
        for p in (write(self.root / "README.md"), write(self.root / "a/params.json"), self.root / "gone.zen",
                  write(self.root / ".pcb/x.zen"), Path("/etc/hosts")):
            self.assertEqual(self.go(p), (0, ""), p)
        self.assertEqual(self.calls, [])
        self.assertEqual(h.handle({}, self.root, self.runner), (0, ""))

    def test_non_json_stdin_fails(self):
        p = subprocess.run([sys.executable, "-B", str(TOOL), "--root", str(self.root)], input="x", capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_real_pcb_fmt_formats_only_the_edited_zen(self):
        f = write(self.root / "a.zen", "x   =   1\n")
        other = write(self.root / "b.zen", "y   =   2\n")
        p = subprocess.run([sys.executable, "-B", str(TOOL), "--root", str(self.root)],
                           input=json.dumps({"tool_input": {"file_path": str(f)}}), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(f.read_text(), "x = 1\n")
        self.assertEqual(other.read_text(), "y   =   2\n")


if __name__ == "__main__":
    unittest.main()
