import tempfile
import unittest
from pathlib import Path

from wachiman import paths


class HomeTest(unittest.TestCase):
    def test_defaults_to_dot_wachiman_in_home(self):
        self.assertEqual(paths.home({"HOME": "/Users/ana"}), Path("/Users/ana/.wachiman"))

    def test_wachiman_home_overrides(self):
        env = {"HOME": "/Users/ana", "WACHIMAN_HOME": "/srv/wm"}
        self.assertEqual(paths.home(env), Path("/srv/wm"))

    def test_tilde_expands_against_given_home(self):
        env = {"HOME": "/Users/ana", "WACHIMAN_HOME": "~/sec"}
        self.assertEqual(paths.home(env), Path("/Users/ana/sec"))

    def test_files_live_under_home_by_default(self):
        env = {"HOME": "/Users/ana"}
        self.assertEqual(paths.db_path(env), Path("/Users/ana/.wachiman/radar.db"))
        self.assertEqual(paths.geo_db_path(env), Path("/Users/ana/.wachiman/geo.db"))
        self.assertEqual(paths.config_path(env), Path("/Users/ana/.wachiman/config.env"))

    def test_each_file_can_be_moved_alone(self):
        env = {"HOME": "/Users/ana", "WACHIMAN_DB": "/tmp/r.db", "WACHIMAN_GEO_DB": "~/g.db"}
        self.assertEqual(paths.db_path(env), Path("/tmp/r.db"))
        self.assertEqual(paths.geo_db_path(env), Path("/Users/ana/g.db"))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        (self.home / "config.env").write_text(text, encoding="utf-8")

    def test_missing_file_is_empty(self):
        self.assertEqual(paths.load_config(self.home / "nope.env"), {})

    def test_parses_key_values_comments_quotes_and_export(self):
        self.write(
            "# comment\n"
            "\n"
            "WACHIMAN_RADAR_INTERVAL=60\n"
            "WACHIMAN_TELEGRAM_CHAT_ID=\"123\"\n"
            "export WACHIMAN_TELEGRAM_BOT_TOKEN='abc:def'\n"
            "garbage line\n"
        )
        self.assertEqual(
            paths.load_config(self.home / "config.env"),
            {
                "WACHIMAN_RADAR_INTERVAL": "60",
                "WACHIMAN_TELEGRAM_CHAT_ID": "123",
                "WACHIMAN_TELEGRAM_BOT_TOKEN": "abc:def",
            },
        )

    def test_value_may_contain_equals(self):
        self.write("WACHIMAN_X=a=b\n")
        self.assertEqual(paths.load_config(self.home / "config.env"), {"WACHIMAN_X": "a=b"})

    def test_layers_defaults_then_file_then_environment(self):
        self.write("WACHIMAN_RADAR_INTERVAL=60\nWACHIMAN_TELEGRAM_CHAT_ID=1\n")
        env = {"HOME": "/x", "WACHIMAN_HOME": str(self.home), "WACHIMAN_TELEGRAM_CHAT_ID": "2", "PATH": "/bin"}
        s = paths.settings(env)
        self.assertEqual(s["WACHIMAN_RADAR_INTERVAL"], "60")  # file beats default
        self.assertEqual(s["WACHIMAN_TELEGRAM_CHAT_ID"], "2")  # env beats file
        self.assertEqual(s["WACHIMAN_TELEGRAM_BOT_TOKEN"], "")  # default kept
        self.assertNotIn("PATH", s)  # only WACHIMAN_* from the environment


if __name__ == "__main__":
    unittest.main()
