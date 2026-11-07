import unittest

from wachiman.radar import traffic

NETTOP = """,bytes_in,bytes_out,
syslogd.587,0,1706,
apsd.594,794747,2459838,
Brave Browser H.1354,355489,184151,
Slack Helper.1406,352418,139192,
weird line
name.with.dots.77,10,20,
"""


class ParseTest(unittest.TestCase):
    def test_parses_pid_and_counters(self):
        c = traffic.parse_nettop(NETTOP)
        self.assertEqual(c[594], ("apsd", 794747, 2459838))
        self.assertEqual(c[1354], ("Brave Browser H", 355489, 184151))
        self.assertEqual(c[77], ("name.with.dots", 10, 20))
        self.assertEqual(len(c), 5)

    def test_empty_and_garbage(self):
        self.assertEqual(traffic.parse_nettop(""), {})
        self.assertEqual(traffic.parse_nettop("x\n,,,\n"), {})


class DeltaTest(unittest.TestCase):
    def test_growth_since_last_sample(self):
        prev = {1: ("Slack", 100, 1000)}
        cur = {1: ("Slack", 150, 4000)}
        self.assertEqual(traffic.delta(prev, cur), {1: (50, 3000)})

    def test_new_process_counts_from_zero_only_after_first_sample(self):
        # a process we never saw: its counters started before we looked, so they are not counted
        self.assertEqual(traffic.delta({}, {2: ("evil", 10, 9_000_000)}), {})

    def test_counters_that_shrink_are_clamped(self):
        # nettop sums open flows; when flows close the total can drop
        prev = {1: ("Brave", 500, 500)}
        cur = {1: ("Brave", 300, 700)}
        self.assertEqual(traffic.delta(prev, cur), {1: (0, 200)})

    def test_pid_reused_by_another_program_restarts(self):
        prev = {1: ("Slack", 100, 100)}
        cur = {1: ("curl", 5, 50)}
        self.assertEqual(traffic.delta(prev, cur), {})

    def test_zero_traffic_is_dropped(self):
        self.assertEqual(traffic.delta({1: ("a", 1, 1)}, {1: ("a", 1, 1)}), {})


class SerialiseTest(unittest.TestCase):
    def test_round_trip(self):
        c = {1: ("Slack Helper", 1, 2), 99: ("x", 3, 4)}
        self.assertEqual(traffic.loads(traffic.dumps(c)), c)

    def test_bad_input_is_empty(self):
        self.assertEqual(traffic.loads(None), {})
        self.assertEqual(traffic.loads("not json"), {})


class HumanTest(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(traffic.human(0), "0 B")
        self.assertEqual(traffic.human(999), "999 B")
        self.assertEqual(traffic.human(1536), "1.5 KB")
        self.assertEqual(traffic.human(250 * 1024 * 1024), "250.0 MB")
        self.assertEqual(traffic.human(3 * 1024 ** 3), "3.0 GB")


if __name__ == "__main__":
    unittest.main()
