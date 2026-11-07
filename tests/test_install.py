import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "install.sh"


class SyntaxTest(unittest.TestCase):
    def test_script_exists_and_parses_in_sh_and_bash(self):
        self.assertTrue(SCRIPT.exists())
        for shell in ("sh", "bash"):
            r = subprocess.run([shell, "-n", str(SCRIPT)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_mentions_the_documented_knobs(self):
        text = SCRIPT.read_text()
        for needle in ("WACHIMAN_SRC", "geo-update", "setup", "service install", "/dev/tty", "python3", "lsof"):
            self.assertIn(needle, text)

    def test_no_secrets_or_user_paths(self):
        text = SCRIPT.read_text()
        self.assertNotIn("/Users/", text)


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("git"), "installer targets macOS and needs git")
class RunTest(unittest.TestCase):
    """Run the real script against a local clone source in a throwaway HOME. No network."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.origin = t / "origin"
        shutil.copytree(ROOT, self.origin, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        git = lambda *a: subprocess.run(["git", "-C", str(self.origin)] + list(a), check=True, capture_output=True)
        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.home = t / "home"
        self.home.mkdir()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "WACHIMAN_REPO_URL": str(self.origin),
                    "WACHIMAN_SKIP_GEO": "1", "WACHIMAN_NONINTERACTIVE": "1", "WACHIMAN_LANG": "es"}

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, **extra):
        env = dict(self.env, **extra)
        return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_fresh_install_then_update(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        src = self.home / ".wachiman" / "src"
        self.assertTrue((src / "bin" / "wachiman").exists())
        self.assertIn("WACHIMAN_LANG=es", (self.home / ".wachiman" / "config.env").read_text())
        link = self.home / ".local" / "bin" / "wachiman"
        self.assertTrue(link.is_symlink())
        h = subprocess.run([str(link), "--help"], env=self.env, capture_output=True, text=True)
        self.assertEqual(h.returncode, 0, h.stderr)
        self.assertIn("wachiman", h.stdout)
        self.assertIn("wachiman web", r.stdout)  # next steps
        r2 = self.install()  # second run updates instead of failing
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)

    def test_custom_source_dir(self):
        dest = Path(self.tmp.name) / "elsewhere"
        r = self.install(WACHIMAN_SRC=str(dest))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((dest / "wachiman" / "cli.py").exists())

    def test_does_not_install_the_service_when_non_interactive(self):
        r = self.install()
        self.assertFalse((self.home / "Library" / "LaunchAgents").exists())

    def test_refuses_without_python3(self):
        r = subprocess.run(["/bin/sh", str(SCRIPT)], env={"HOME": str(self.home), "PATH": "/nonexistent"},
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
