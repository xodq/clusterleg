"""A simulated Gen1 Virtual Cockpit that answers UDS the way the real one was observed to."""

import queue

from udsoncan.connections import BaseConnection
from udsoncan.exceptions import TimeoutException

LAYOUT_ADDR = 0x03003C09


class FakeCluster(BaseConnection):
    def __init__(self, coding="08 E4 18 51 23 88 CF 05 02 85 E8 00 18 00 00 00 00 00 00 00", layout=b"\x00\x57",
                 login=20103, seed=b"\xDC\xDE\xAB\x2D",
                 part=b"8V0920890  ", odx=b"EV_DashBoardFPKAU37X\x00"):
        BaseConnection.__init__(self, "fake-cluster")
        self.coding = bytes.fromhex(coding.replace(" ", ""))
        self.memory = {LAYOUT_ADDR: bytearray(layout)}
        self.fingerprint = bytes.fromhex("00 00 12 34 56 78")
        self.part = part
        self.odx = odx
        self.login = login
        self.seed = seed
        self.session = 0x01
        self.unlocked = False
        self.f198 = self.f199 = False
        self.log = []
        self.q = queue.Queue()
        self.opened = False

    # --- BaseConnection plumbing
    def open(self):
        self.opened = True
        return self

    def close(self):
        self.opened = False

    def is_open(self):
        return self.opened

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def empty_rxqueue(self):
        while not self.q.empty():
            self.q.get_nowait()

    def specific_wait_frame(self, timeout=None):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            raise TimeoutException("fake cluster: no reply")

    def specific_send(self, payload):
        payload = bytes(payload)
        self.log.append(payload)
        self.q.put(self.answer(payload))

    # --- the cluster
    def answer(self, p):
        sid = p[0]
        nrc = lambda code: bytes([0x7F, sid, code])
        if sid == 0x10:
            self.session, self.unlocked, self.f198, self.f199 = p[1], False, False, False
            return bytes([0x50, p[1], 0x00, 0x32, 0x01, 0xF4])
        if sid == 0x3E:
            return b"\x7E\x00"
        if sid == 0x11:
            self.session, self.unlocked = 0x01, False
            return bytes([0x51, p[1]])
        if sid == 0x22:
            did = p[1:3]
            data = {b"\x06\x00": self.coding, b"\xF1\xA5": self.fingerprint,
                    b"\xF1\x90": b"TESTVIN0000000000",
                    b"\xF1\x87": self.part, b"\xF1\x89": b"0045", b"\xF1\x9E": self.odx,
                    b"\x22\x03": b"\x01\x4A\x53"}.get(did)
            return b"\x62" + did + data if data is not None else nrc(0x31)
        if sid == 0x2E:
            did, data = p[1:3], p[3:]
            if self.session != 0x03:
                return nrc(0x7F)
            if did == b"\xF1\x98" and len(data) == 6:
                self.f198 = True
            elif did == b"\xF1\x99" and len(data) == 3:
                self.f199 = True
            elif did == b"\x06\x00":
                if not (self.f198 and self.f199):
                    return nrc(0x24)
                if len(data) != len(self.coding):
                    return nrc(0x13)
                self.coding = data
            else:
                return nrc(0x31)
            return b"\x6E" + did
        if sid == 0x27:
            if self.session != 0x4F:
                return nrc(0x7F)
            if p[1] == 0x03:
                return b"\x67\x03" + self.seed
            if p[1] == 0x04:
                want = ((int.from_bytes(self.seed, "big") + self.login) & 0xFFFFFFFF).to_bytes(4, "big")
                if p[2:] != want:
                    return nrc(0x35)
                self.unlocked = True
                return b"\x67\x04"
            return nrc(0x12)
        if sid in (0x23, 0x3D):
            if not self.unlocked:
                return nrc(0x33)
            alfid = p[1]
            alen, llen = alfid & 0x0F, alfid >> 4
            addr = int.from_bytes(p[2:2 + alen], "big")
            size = int.from_bytes(p[2 + alen:2 + alen + llen], "big")
            mem = self.memory.get(addr)
            if mem is None or size != len(mem):
                return nrc(0x31)
            if sid == 0x23:
                return b"\x63" + bytes(mem)
            mem[:] = p[2 + alen + llen:]
            return b"\x7D" + p[1:2 + alen + llen]
        return nrc(0x11)
