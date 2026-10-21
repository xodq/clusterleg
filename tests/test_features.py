import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from udsoncan import configs  # noqa: E402
from udsoncan.client import Client  # noqa: E402

from clusterleg import deck_catalog as cat, features, uds  # noqa: E402
from fake_cluster import FakeCluster  # noqa: E402

STOCK = "08 E4 18 51 23 88 CF 05 02 85 E8 00 18 00 00 00 00 00 00 00"


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = fn(*a, **kw)
    return result, out.getvalue()


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backup = os.path.join(self.tmp.name, "stock.txt")
        with open(self.backup, "w") as f:
            f.write(STOCK + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, fake):
        return Client(fake, request_timeout=2, config=configs.default_client_config)

    def test_scan_identifies_your_cluster(self):
        fake = FakeCluster(coding=STOCK.replace("E4", "EC"))
        with self.client(fake) as c:
            (ident, profile, coding), out = quiet(features.scan, c, self.backup)
        self.assertEqual(profile.key, "a3_8v_fpk")
        self.assertIn("differs from stock at byte(s) [1]", out)
        self.assertRegex(out, r"laptimer\s+1\.3\s+ON\s+off")
        self.assertIn("not tested on the reference car", out)

    def test_other_gen1_cluster_gets_generic_profile(self):
        fake = FakeCluster(coding=STOCK, part=b"8S0920890  ", odx=b"EV_DashBoardFPKAU36X\x00")
        with self.client(fake) as c:
            (_, profile, _), _ = quiet(features.scan, c)
        self.assertEqual(profile.key, "audi_gen1_fpk")

    def test_set_feature_writes_the_bit(self):
        fake = FakeCluster(coding=STOCK)
        with self.client(fake) as c:
            ok, _ = quiet(features.set_feature, c, "a3_8v_fpk:gmeter", True, self.backup, yes=True)
        self.assertTrue(ok)
        self.assertEqual(fake.coding[10], 0xEC)

    def test_refuses_profile_mismatch_without_any_car(self):
        fake = FakeCluster(coding=STOCK, part=b"5G0920320  ", odx=b"EV_DashBoardVDDMQBAB\x00")
        with self.client(fake) as c, self.assertRaises(SystemExit):
            quiet(features.set_feature, c, "a3_8v_fpk:laptimer", True, self.backup, yes=True)
        self.assertFalse(any(p[0] == 0x2E for p in fake.log))
        with self.client(fake) as c:
            quiet(features.set_feature, c, "a3_8v_fpk:laptimer", True, self.backup, yes=True, any_car=True)
        self.assertEqual(fake.coding[1], 0xEC)

    def test_locked_feature_is_never_written(self):
        fake = FakeCluster(coding=STOCK)
        with self.client(fake) as c, self.assertRaises(SystemExit):
            quiet(features.set_feature, c, "a3_8v_fpk:variant", True, self.backup, yes=True)
        self.assertNotIn("a3_8v_fpk:variant", features.all_feature_choices())

    def test_mileage_reads_each_module(self):
        fake = FakeCluster(coding=STOCK)
        with mock.patch.object(uds, "open_client", lambda *a, **k: Client(fake, request_timeout=2)):
            rows, out = quiet(features.mileage, "COM9", ["cluster"])
        self.assertEqual(rows, [("Instrument cluster (17)", 0x2203, 84563)])
        self.assertIn("84,563 km", out)


class BlockPickTests(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(cat.pick_blocks({"bmode": "all"}, cat.ENGINE_ORDER), ["1", "2", "3", "4", "5"])
        self.assertEqual(cat.pick_blocks({"bmode": "start from", "bpick": "3"}, cat.ENGINE_ORDER), ["3", "4", "5"])
        self.assertEqual(cat.pick_blocks({"bmode": "only", "bpick": "5"}, cat.ENGINE_ORDER), ["5"])
        self.assertEqual(cat.pick_blocks({"bmode": "custom list", "blist": "1, 5"}, cat.ENGINE_ORDER), ["1", "5"])
        self.assertEqual(cat.pick_blocks({"bmode": "start from", "bpick": "9"}, cat.UNLOCK_ORDER), ["9", "5"])

    def test_flash_line(self):
        cmd = cat.BY_ID["flash_bin"]
        vals = {f.key: f.default for f in cmd.fields}
        vals.update(bin=r"C:\t\a.bin", bmode="start from", bpick="4", dry=True)
        argv = cat.build_argv(cmd, vals, "COM4")
        self.assertEqual(argv[argv.index("--blocks") + 1], "4,5")
        self.assertIn("--dry-run", argv)
        self.assertEqual(cat.BY_ID["unlock_resume"].build_fn(
            {f.key: f.default for f in cat.BY_ID["unlock_resume"].fields} | {"frf": "x.frf"}, "COM4")[-1], "4,9,5")


if __name__ == "__main__":
    unittest.main()
