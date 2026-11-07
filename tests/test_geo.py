import gzip
import tempfile
import unittest
from pathlib import Path

from wachiman.radar import geo

TSV = (
    "1.0.0.0\t1.0.0.255\t13335\tUS\tCLOUDFLARENET - Cloudflare, Inc.\n"
    "1.0.1.0\t1.0.3.255\t0\tNone\tNot routed\n"
    "34.224.0.0\t34.239.255.255\t14618\tUS\tAMAZON-AES - Amazon.com, Inc.\n"
    "8.8.8.0\t8.8.8.255\t15169\tUS\tGOOGLE - Google LLC\n"
    "95.163.0.0\t95.163.255.255\t47764\tRU\tVK-AS - LLC VK\n"
    "2a00:1450::\t2a00:1450:ffff:ffff:ffff:ffff:ffff:ffff\t15169\tIE\tGOOGLE - Google LLC\n"
    "broken line\n"
)


class GeoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.src = d / "ip2asn.tsv.gz"
        with gzip.open(self.src, "wt", encoding="utf-8") as f:
            f.write(TSV)
        self.db = d / "geo.db"
        self.count = geo.build(self.src, self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_skips_unrouted_and_broken_rows(self):
        self.assertEqual(self.count, 5)

    def test_lookup_ipv4_and_ipv6(self):
        g = geo.Geo(self.db)
        self.assertEqual(g.lookup("34.237.73.95"), geo.Owner(14618, "US", "Amazon.com, Inc."))
        self.assertEqual(g.lookup("8.8.8.8"), geo.Owner(15169, "US", "Google LLC"))
        self.assertEqual(g.lookup("2a00:1450:4003:80e::200e"), geo.Owner(15169, "IE", "Google LLC"))
        self.assertEqual(g.lookup("95.163.10.1").country, "RU")

    def test_unknown_ip_gaps_and_bad_input(self):
        g = geo.Geo(self.db)
        self.assertIsNone(g.lookup("1.0.2.1"))  # not routed, not stored
        self.assertIsNone(g.lookup("9.9.9.9"))  # gap between ranges
        self.assertIsNone(g.lookup("not-an-ip"))

    def test_missing_db_degrades_to_none(self):
        g = geo.Geo(Path(self.tmp.name) / "absent.db")
        self.assertFalse(g.available)
        self.assertIsNone(g.lookup("8.8.8.8"))

    def test_org_name_drops_the_as_handle(self):
        self.assertEqual(geo.clean_org("GOOGLE - Google LLC"), "Google LLC")
        self.assertEqual(geo.clean_org("SOMEISP"), "SOMEISP")


if __name__ == "__main__":
    unittest.main()
