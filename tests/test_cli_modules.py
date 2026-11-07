import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from wachiman import cli


class ModuleCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name}
        self.ran = []
        self.gateway_calls = []

    def run_cli(self, *argv):
        def runner(argv_, env):
            self.ran.append((list(argv_), dict(env)))
            return 0

        def gw(settings, home, repo, once, env):
            self.gateway_calls.append(once)
            return 0

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(list(argv), env=self.env, script_runner=runner, gateway_runner=gw)
        return code, buf.getvalue()

    def script(self):
        argv = self.ran[0][0]
        return argv[0], Path(argv[1]).relative_to(Path(argv[1]).parents[2]).as_posix(), argv[2:]

    def test_guard_subcommands_map_to_guardctl(self):
        for sub in ("start", "start-nolock", "stop", "status"):
            self.ran.clear()
            code, _ = self.run_cli("guard", sub)
            self.assertEqual(code, 0)
            self.assertEqual(self.script(), ("/bin/bash", "modules/guard/guardctl.sh", [sub]))

    def test_stealerwatch_scan_and_baseline(self):
        self.run_cli("stealerwatch", "scan")
        self.assertEqual(self.script(), ("/bin/bash", "modules/stealerwatch/stealerwatch.sh", ["--dry"]))
        self.ran.clear()
        self.run_cli("stealerwatch", "baseline")
        self.assertEqual(self.script()[2], ["--baseline"])

    def test_stealerwatch_watch_and_memwatch_watch_run_the_loops(self):
        self.run_cli("stealerwatch", "watch")
        self.assertEqual(self.script()[2], [])
        self.ran.clear()
        self.run_cli("memwatch", "watch")
        self.assertEqual(self.script(), ("/bin/bash", "modules/memwatch/memwatch.sh", []))

    def test_memwatch_now_and_test(self):
        self.run_cli("memwatch", "now")
        self.assertEqual(self.script(), ("/bin/bash", "modules/memwatch/memwatch.sh", ["--dry"]))
        self.ran.clear()
        self.run_cli("memwatch", "test")
        self.assertEqual(self.script()[2], ["--test"])

    def test_scripts_inherit_the_environment(self):
        self.env["WACHIMAN_HOME"] = "/srv/wm"
        self.run_cli("memwatch", "now")
        self.assertEqual(self.ran[0][1]["WACHIMAN_HOME"], "/srv/wm")

    def test_exit_code_of_the_script_is_returned(self):
        def runner(argv_, env):
            return 3
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["guard", "status"], env=self.env, script_runner=runner), 3)

    def test_gateway_run_and_once(self):
        self.run_cli("gateway", "run")
        self.assertEqual(self.gateway_calls, [None])
        self.run_cli("gateway", "run", "--once", "/status")
        self.assertEqual(self.gateway_calls, [None, "/status"])

    def test_gateway_without_credentials_explains_instead_of_crashing(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["gateway", "run"], env=self.env)
        self.assertEqual(code, 1)
        self.assertIn("WACHIMAN_TELEGRAM_BOT_TOKEN", buf.getvalue())

    def test_bad_arguments_print_usage(self):
        for argv in (["guard"], ["guard", "explode"], ["memwatch"], ["stealerwatch"], ["gateway"]):
            with redirect_stdout(io.StringIO()):
                self.assertNotEqual(cli.main(argv, env=self.env, script_runner=lambda a, e: 0), 0, argv)


if __name__ == "__main__":
    unittest.main()
