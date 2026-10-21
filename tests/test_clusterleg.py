import contextlib
import hashlib
import io
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from udsoncan import configs  # noqa: E402
from udsoncan.client import Client  # noqa: E402

from clusterleg import coding, firmware, layout  # noqa: E402
from clusterleg.cli import build_parser  # noqa: E402
from clusterleg.uds import describe  # noqa: E402
from fake_cluster import LAYOUT_ADDR, FakeCluster  # noqa: E402

STOCK = "08 E4 18 51 23 88 CF 05 02 85 E8 00 18 00 00 00 00 00 00 00"


def open_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def client_for(fake):
    return Client(fake, request_timeout=2, config=configs.default_client_config)


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = fn(*a, **kw)
    return result, out.getvalue()


class LayoutTests(unittest.TestCase):
    def test_login_key_matches_capture(self):
        # seed seen from a real cluster -> key OBDeleven got accepted with
        self.assertEqual(layout.login_key(bytes.fromhex("DCDEAB2D")).hex().upper(), "DCDEF9B4")

    def test_read(self):
        fake = FakeCluster(coding=STOCK)
        with client_for(fake) as c:
            data, _ = quiet(layout.do_read, c)
        self.assertEqual(data, b"\x00\x57")

    def test_set_rs_writes_and_resets(self):
        fake = FakeCluster(coding=STOCK)
        with client_for(fake) as c:
            changed, out = quiet(layout.do_set, c, "rs", yes=True, reset_wait=0)
        self.assertTrue(changed)
        self.assertEqual(bytes(fake.memory[LAYOUT_ADDR]), b"\x03\x57")
        self.assertIn(bytes.fromhex("3D14 03003C09 02 0357"), fake.log)
        self.assertEqual(fake.log[-1], b"\x11\x01")

    def test_dry_run_writes_nothing(self):
        fake = FakeCluster(coding=STOCK)
        with client_for(fake) as c:
            quiet(layout.do_set, c, "s", yes=False)
        self.assertEqual(bytes(fake.memory[LAYOUT_ADDR]), b"\x00\x57")
        self.assertFalse(any(p[0] == 0x3D for p in fake.log))

    def test_refuses_unrecognised_bytes(self):
        fake = FakeCluster(coding=STOCK, layout=b"\xFF\xFF")
        with client_for(fake) as c, self.assertRaises(SystemExit):
            quiet(layout.do_set, c, "rs", yes=True)
        self.assertEqual(bytes(fake.memory[LAYOUT_ADDR]), b"\xFF\xFF")

    def test_wrong_login_stops_without_retry(self):
        fake = FakeCluster(coding=STOCK, login=12345)
        with client_for(fake) as c, self.assertRaises(SystemExit):
            quiet(layout.do_set, c, "rs", yes=True)
        self.assertEqual(sum(1 for p in fake.log if p[:2] == b"\x27\x04"), 1)
        self.assertFalse(any(p[0] == 0x3D for p in fake.log))

    def test_laptimer_hint(self):
        fake = FakeCluster(coding=STOCK)  # byte 1 = E4, bit 3 off
        with client_for(fake) as c:
            _, out = quiet(layout.do_read, c)
        self.assertIn("lap timer", out)


class CodingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backup = os.path.join(self.tmp.name, "backup.txt")
        with open(self.backup, "w") as f:
            f.write(STOCK + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_cluster_rejects_coding_without_login(self):
        fake = FakeCluster(coding=STOCK)
        self.assertEqual(fake.answer(b"\x10\x03")[0], 0x50)
        self.assertEqual(fake.answer(b"\x2E\x06\x00" + bytes(20)), b"\x7F\x2E\x24")

    def test_write_bit_does_login_then_coding(self):
        fake = FakeCluster(coding=STOCK)
        with client_for(fake) as c:
            ok, _ = quiet(coding.do_write, c, self.backup, 1, bit=3, on=True, yes=True)
        self.assertTrue(ok)
        self.assertEqual(fake.coding[1], 0xEC)
        writes = [p[1:3] for p in fake.log if p[0] == 0x2E]
        self.assertEqual(writes, [b"\xF1\x98", b"\xF1\x99", b"\x06\x00"])
        f198 = next(p for p in fake.log if p[:3] == b"\x2E\xF1\x98")
        self.assertEqual(f198[3:], fake.fingerprint)  # workshop code reused, fingerprint unchanged

    def test_dry_run_and_mismatch_guard(self):
        fake = FakeCluster(coding=STOCK.replace("E4", "EC"))
        with client_for(fake) as c, self.assertRaises(SystemExit):
            quiet(coding.do_write, c, self.backup, 10, value="EC", yes=True)
        self.assertFalse(any(p[0] == 0x2E for p in fake.log))

    def test_restore(self):
        fake = FakeCluster(coding=STOCK.replace("18 51", "1A 51"))
        with client_for(fake) as c:
            quiet(coding.do_write, c, self.backup, None, yes=True, restore=True)
        self.assertEqual(fake.coding, bytes.fromhex(STOCK.replace(" ", "")))

    def test_bit_math(self):
        cur = bytes.fromhex(STOCK.replace(" ", ""))
        self.assertEqual(coding.plan_change(cur, 10, bit=2, on=True)[10], 0xEC)
        self.assertEqual(coding.plan_change(cur, 10, bit=3, on=False)[10], 0xE0)
        self.assertEqual(coding.plan_change(cur, 15, bit=4)[15], 0x10)


class ListenTests(unittest.TestCase):
    def test_decodes_capture(self):
        self.assertEqual(describe(bytes.fromhex("7D14 03003C09 02")), "MEMORY WRITE accepted: 2 bytes at 0x03003C09")
        self.assertEqual(describe(bytes.fromhex("67 03 DCDEAB2D")), "SECURITY ACCESS (seed sent, level 0x03)")
        self.assertEqual(describe(bytes.fromhex("7F 2E 24")), "REFUSED WriteDataByIdentifier: requestSequenceError")


def fake_esp_image(size=0x30000):
    """Minimal one-segment ESP32 app image with the stock bytes at the patch offsets."""
    seg = bytearray(size)
    for off, old, _, _ in firmware.CLUSTER_PATCH:
        seg[off - 32:off - 32 + 3] = old
    header = bytearray(24)
    header[0], header[1], header[23] = 0xE9, 1, 1
    body = bytes(header) + struct.pack("<II", 0x400D0020, size) + bytes(seg)
    pos = len(body)
    body += bytes((15 - (pos % 16)) % 16)
    chk = 0xEF
    for b in seg:
        chk ^= b
    body += bytes([chk])
    return body + hashlib.sha256(body).digest()


class FirmwareTests(unittest.TestCase):
    def test_patch_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "in.bin"), os.path.join(d, "out.bin")
            with open(src, "wb") as f:
                f.write(fake_esp_image())
            self.assertTrue(firmware.image_ok(open_bytes(src)))
            out, _ = quiet(firmware.patch, src, dst)
            self.assertTrue(firmware.image_ok(out))
            for off, _, new, _ in firmware.CLUSTER_PATCH:
                self.assertEqual(out[off:off + 3], new)

    def test_refuses_unknown_build(self):
        img = bytearray(fake_esp_image())
        img[0x28C34] ^= 0xFF
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.bin")
            with open(src, "wb") as f:
                f.write(img)
            with self.assertRaises(SystemExit):
                quiet(firmware.patch, src, os.path.join(d, "out.bin"))


class CliTests(unittest.TestCase):
    def test_parses(self):
        p = build_parser()
        a = p.parse_args(["coding", "write", "--backup", "b.txt", "--byte", "10", "--bit", "2", "--yes"])
        self.assertEqual((a.byte, a.bit, a.on, a.yes), (10, 2, True, True))
        a = p.parse_args(["--port", "COM7", "layout", "set", "s"])
        self.assertEqual((a.port, a.layout, a.yes), ("COM7", "s", False))


if __name__ == "__main__":
    unittest.main()
