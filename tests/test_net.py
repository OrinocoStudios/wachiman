import unittest

from wachiman.radar import net

# Real `lsof -nP -i -F pcPnT +c 0` shape: process sets (p, c) then file sets (f, t, P, n, T...).
LSOF = """p984
cidentityservicesd
f10
tIPv4
PUDP
n*:*
f56
tIPv6
PTCP
n[fe80:19::ad05:ed14:493a:8373]:1024->[fe80:19::c388:6a9f:9251:8875]:1024
TST=ESTABLISHED
TQR=0
TQS=0
p1263
cSlack
f40
tIPv4
PTCP
n192.168.1.20:52344->34.237.73.95:443
TST=ESTABLISHED
f41
tIPv4
PTCP
n127.0.0.1:52345->127.0.0.1:8080
TST=ESTABLISHED
f42
tIPv4
PTCP
n*:8092
TST=LISTEN
f43
tIPv6
PTCP
n[2a00:1450:4003:80e::200e]:443
TST=ESTABLISHED
p2001
cGoogle Chrome Helper
f60
tIPv6
PTCP
n[2001:db8::5]:50000->[2a00:1450:4003:80e::200e]:443
TST=ESTABLISHED
f61
tIPv4
PUDP
n192.168.1.20:61000->8.8.8.8:53
"""


class ParseLsofTest(unittest.TestCase):
    def test_parses_every_socket_with_its_process(self):
        socks = net.parse_lsof(LSOF)
        self.assertEqual(len(socks), 8)
        slack = [s for s in socks if s.pid == 1263]
        self.assertEqual({s.command for s in slack}, {"Slack"})

    def test_splits_ipv4_and_ipv6_endpoints(self):
        socks = {(s.pid, s.fd): s for s in net.parse_lsof(LSOF)}
        s = socks[(1263, "40")]
        self.assertEqual((s.proto, s.local_ip, s.local_port, s.remote_ip, s.remote_port, s.state),
                         ("TCP", "192.168.1.20", 52344, "34.237.73.95", 443, "ESTABLISHED"))
        v6 = socks[(2001, "60")]
        self.assertEqual((v6.remote_ip, v6.remote_port), ("2a00:1450:4003:80e::200e", 443))

    def test_udp_without_state_and_unconnected_sockets(self):
        socks = {(s.pid, s.fd): s for s in net.parse_lsof(LSOF)}
        dns = socks[(2001, "61")]
        self.assertEqual((dns.proto, dns.remote_ip, dns.remote_port, dns.state), ("UDP", "8.8.8.8", 53, ""))
        unbound = socks[(984, "10")]
        self.assertIsNone(unbound.remote_ip)
        listen = socks[(1263, "42")]
        self.assertIsNone(listen.remote_ip)
        self.assertEqual(listen.local_port, 8092)

    def test_garbage_and_empty_input(self):
        self.assertEqual(net.parse_lsof(""), [])
        self.assertEqual(net.parse_lsof("zzz\n\nnot lsof\n"), [])


class OutboundTest(unittest.TestCase):
    def test_keeps_only_internet_destinations(self):
        out = net.outbound(net.parse_lsof(LSOF))
        self.assertEqual(
            sorted((s.pid, s.remote_ip, s.remote_port) for s in out),
            [(1263, "34.237.73.95", 443), (2001, "2a00:1450:4003:80e::200e", 443), (2001, "8.8.8.8", 53)],
        )

    def test_is_public(self):
        for ip in ["34.237.73.95", "8.8.8.8", "2a00:1450:4003:80e::200e"]:
            self.assertTrue(net.is_public(ip), ip)
        for ip in ["127.0.0.1", "192.168.1.10", "10.0.0.1", "172.16.4.4", "169.254.1.1",
                   "fe80::1", "::1", "100.64.0.1", "224.0.0.251", "ff02::fb", "2001:db8::1", "nonsense"]:
            self.assertFalse(net.is_public(ip), ip)


class AppNameTest(unittest.TestCase):
    def test_app_bundle_wins(self):
        self.assertEqual(net.app_name("/Applications/Slack.app/Contents/MacOS/Slack", "Slack"), "Slack")

    def test_helpers_roll_up_to_the_outer_app(self):
        path = ("/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/"
                "Versions/1/Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper")
        self.assertEqual(net.app_name(path, "Google Chrome Helper"), "Google Chrome")

    def test_plain_binaries_use_their_file_name(self):
        self.assertEqual(net.app_name("/usr/libexec/apsd", "apsd"), "apsd")
        self.assertEqual(net.app_name("/opt/homebrew/bin/node", "node"), "node")

    def test_falls_back_to_command_when_path_unknown(self):
        self.assertEqual(net.app_name(None, "curl"), "curl")
        self.assertEqual(net.app_name("", "curl"), "curl")

    def test_parse_ps(self):
        ps = "  984 /usr/libexec/identityservicesd\n 1263 /Applications/Slack.app/Contents/MacOS/Slack\nbad\n"
        self.assertEqual(net.parse_ps(ps), {984: "/usr/libexec/identityservicesd",
                                            1263: "/Applications/Slack.app/Contents/MacOS/Slack"})


if __name__ == "__main__":
    unittest.main()
