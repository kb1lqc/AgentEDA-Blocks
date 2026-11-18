"""Tests for analysis/_zl.py: the released-checkout test that sets each report's library line."""

import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def sh(cwd, *cmd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, env=env)


class ZL(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.zl = Path(self._t.name) / "zl"
        (self.zl / "lib").mkdir(parents=True)
        (self.zl / "lib" / "__init__.py").write_text("")
        sh(self.zl, "git", "init", "-q")
        sh(self.zl, "git", "add", "-A")
        sh(self.zl, "git", "commit", "-qm", "a")
        self._env = os.environ.get("ZENER_LIBRARY")
        os.environ["ZENER_LIBRARY"] = str(self.zl)
        sys.path.insert(0, str(REPO))
        import analysis._zl as zl
        self.m = importlib.reload(zl)
        sh(self.zl, "git", "tag", self.m.ZL_VERSION)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("ZENER_LIBRARY", None)
        else:
            os.environ["ZENER_LIBRARY"] = self._env
        while str(self.zl) in sys.path:  # _zl.py puts the checkout on sys.path; do not leak the fixture
            sys.path.remove(str(self.zl))
        sys.modules.pop("analysis._zl", None)
        sys.path.remove(str(REPO))
        self._t.cleanup()

    def test_porcelain_rules(self):
        self.assertTrue(self.m.porcelain_clean(""))
        self.assertTrue(self.m.porcelain_clean("?? .claude/skills/context-diet/\n"))
        self.assertFalse(self.m.porcelain_clean("?? notes.txt\n"))
        self.assertFalse(self.m.porcelain_clean(" M lib/__init__.py\n"))
        self.assertFalse(self.m.porcelain_clean("?? .claude/x\n M lib/__init__.py\n"))

    def test_released_with_untracked_claude_dir(self):
        (self.zl / ".claude" / "skills" / "x").mkdir(parents=True)
        (self.zl / ".claude" / "skills" / "x" / "SKILL.md").write_text("x")
        self.assertTrue(self.m.zl_released())

    def test_untracked_file_outside_claude_is_not_released(self):
        (self.zl / "stray.txt").write_text("x")
        self.assertFalse(self.m.zl_released())

    def test_wrong_tag_is_not_released(self):
        sh(self.zl, "git", "tag", "-d", self.m.ZL_VERSION)
        self.assertFalse(self.m.zl_released())

    def test_hook_git_env_does_not_leak(self):
        other = Path(self._t.name) / "other"
        other.mkdir()
        sh(other, "git", "init", "-q")
        old = {k: os.environ.get(k) for k in ("GIT_DIR", "GIT_INDEX_FILE")}
        os.environ["GIT_DIR"] = str(other / ".git")
        os.environ["GIT_INDEX_FILE"] = str(other / ".git" / "index")
        try:
            self.assertTrue(self.m.zl_released())
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
