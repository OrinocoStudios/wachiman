import os
import subprocess
import unittest

from tests.shell_helpers import ShellCase, MODULES, TOKEN, all_shell_scripts

LIB = MODULES / "lib" / "common.sh"


class LibTest(ShellCase):
    def run_lib(self, snippet, **env):
        p = subprocess.run(["/bin/bash", "-c", '. "%s"; %s' % (LIB, snippet)], capture_output=True, text=True,
                           env=self.env(**env), stdin=subprocess.DEVNULL, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def test_default_when_nothing_is_set(self):
        self.assertEqual(self.run_lib("cfg WACHIMAN_NOPE fallback"), "fallback")

    def test_config_file_beats_default_and_environment_beats_file(self):
        self.write_config("WACHIMAN_X=from-file\n")
        self.assertEqual(self.run_lib("cfg WACHIMAN_X d"), "from-file")
        self.assertEqual(self.run_lib("cfg WACHIMAN_X d", WACHIMAN_X="from-env"), "from-env")

    def test_config_parsing_matches_the_python_reader(self):
        self.write_config('# comment\nexport WACHIMAN_A="quoted value"\nWACHIMAN_B=\'single\'\nWACHIMAN_C=a=b\n'
                          'WACHIMAN_D=first\nWACHIMAN_D=second\r\n')
        self.assertEqual(self.run_lib("cfg WACHIMAN_A"), "quoted value")
        self.assertEqual(self.run_lib("cfg WACHIMAN_B"), "single")
        self.assertEqual(self.run_lib("cfg WACHIMAN_C"), "a=b")
        self.assertEqual(self.run_lib("cfg WACHIMAN_D"), "second")

    def test_config_is_data_never_code(self):
        marker = self.wm / "pwned"
        self.write_config("WACHIMAN_X=$(touch %s)\n`touch %s`\n" % (marker, marker))
        self.assertEqual(self.run_lib("cfg WACHIMAN_X"), "$(touch %s)" % marker)
        self.assertFalse(marker.exists())

    def test_numbers_fall_back_when_garbage(self):
        self.write_config("WACHIMAN_N=abc\nWACHIMAN_M=7\n")
        self.assertEqual(self.run_lib("cfg_num WACHIMAN_N 20"), "20")
        self.assertEqual(self.run_lib("cfg_num WACHIMAN_M 20"), "7")

    def test_home_defaults_to_dot_wachiman_and_tilde_expands(self):
        out = self.run_lib('echo "$WM_HOME"', WACHIMAN_HOME="")
        self.assertEqual(out, str(self.home / ".wachiman"))
        self.assertEqual(self.run_lib('echo "$WM_HOME"', WACHIMAN_HOME="~/data"), str(self.home / "data"))

    def test_telegram_credentials_come_from_the_wachiman_settings(self):
        self.write_config("WACHIMAN_TELEGRAM_BOT_TOKEN=%s\nWACHIMAN_TELEGRAM_CHAT_ID=77\n" % TOKEN)
        self.assertEqual(self.run_lib('echo "$CHAT"'), "77")
        self.assertEqual(self.run_lib('[ "$TOKEN" = "%s" ] && echo same' % TOKEN), "same")

    def test_html_escape(self):
        self.assertEqual(self.run_lib("esc '<b>a&b</b>'"), "&lt;b&gt;a&amp;b&lt;/b&gt;")

    def test_send_posts_to_the_api_and_hides_the_token_from_argv(self):
        tg = self.telegram()
        self.run_lib("tg_text 'hello there'")
        self.assertEqual(tg.texts(), ["hello there"])
        self.assertTrue(tg.requests[0][0].startswith("/bot%s/" % TOKEN))
        # curl is given the url on stdin, so the token never shows in `ps`
        self.fake("curl", 'echo "$@" > "%s/args"; cat > /dev/null' % self.seq)
        self.run_lib("tg_text x")
        self.assertNoToken((self.seq / "args").read_text())

    def test_send_without_credentials_is_a_quiet_no_op(self):
        self.assertEqual(self.run_lib("tg_text hi; echo done"), "done")

    def test_every_script_parses(self):
        scripts = all_shell_scripts()
        self.assertGreaterEqual(len(scripts), 6)
        for s in scripts:
            r = subprocess.run(["/bin/bash", "-n", str(s)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (s, r.stderr))

    def test_entry_scripts_are_executable(self):
        for s in all_shell_scripts():
            if s.name != "common.sh":
                self.assertTrue(os.access(str(s), os.X_OK), s)


if __name__ == "__main__":
    unittest.main()
