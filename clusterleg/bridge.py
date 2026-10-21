"""
Transport for the ESP32 ISO-TP bridge (Macchina A0 / AMAleg / Switchleg1 esp32-isotp-ble-bridge) over USB serial.

Wire format, both directions:  F1 <flags> <rxid LE16> <txid LE16> <len LE16> <payload>
  flags 0x00          ISO-TP payload for the link rxid/txid
  flags 0x80 | id     bridge setting (0x40 set = get), payload is the setting value

USBISOTPConnection is adapted from VW_Flash (lib/connections/usb_isotp_connection.py),
Copyright (c) 2022-2024, Brian Ledbetter and contributors, BSD 2-Clause License.
"""

import queue
import sys
import threading
import time

import serial
from udsoncan.connections import BaseConnection
from udsoncan.exceptions import TimeoutException

HEADER_ID = 0xF1
FLAG_SETTINGS = 0x80
FLAG_GET = 0x40
SETTING_STMIN = 0x01
SETTING_GAP = 0x08  # 'get' on this id returns the bridge name; used as a harmless keepalive
BAUD = 250000
DEFAULT_TX_STMIN_US = 350


def open_port(port, timeout=0.1):
    """Open the bridge's serial port without toggling DTR/RTS (that would put the ESP32 in its bootloader)."""
    ser = serial.Serial(baudrate=BAUD, timeout=timeout)
    ser.port = port
    # Same quirk as VW_Flash: 0 works on Windows, 1 on macOS/Linux.
    level = 0 if sys.platform == "win32" else 1
    ser.dtr = level
    ser.rts = level
    ser.open()
    return ser


def frame(flags, rxid, txid, payload=b""):
    return (bytes([HEADER_ID, flags]) + rxid.to_bytes(2, "little") + txid.to_bytes(2, "little")
            + len(payload).to_bytes(2, "little") + payload)


def read_frames(buf):
    """Split complete bridge frames off the front of buf. Returns ([(flags, sender, payload)], rest)."""
    out = []
    while len(buf) >= 8:
        if buf[0] != HEADER_ID:
            buf = buf[1:]
            continue
        size = int.from_bytes(buf[6:8], "little")
        if len(buf) < 8 + size:
            break
        out.append((buf[1], int.from_bytes(buf[4:6], "little"), buf[8:8 + size]))
        buf = buf[8 + size:]
    return out, buf


def query_setting(ser, setting, rxid=0, txid=0, wait=1.0):
    """Ask the bridge itself for a setting. Nothing goes onto the CAN bus. Returns the payload or None."""
    ser.reset_input_buffer()
    ser.write(frame(FLAG_SETTINGS | FLAG_GET | setting, rxid, txid, (0).to_bytes(2, "little")))
    deadline = time.time() + wait
    buf = b""
    while time.time() < deadline:
        buf += ser.read(64)
        frames, buf = read_frames(buf)
        for flags, _, payload in frames:
            if flags == (FLAG_SETTINGS | setting):
                return payload
    return None


class USBISOTPConnection(BaseConnection):
    """udsoncan connection that talks UDS through the bridge on one rxid/txid link."""

    def __init__(self, port, rxid, txid, tx_stmin_us=DEFAULT_TX_STMIN_US, name=None):
        BaseConnection.__init__(self, name)
        self.port = port
        self.rxid = rxid
        self.txid = txid
        self.tx_stmin_us = tx_stmin_us
        self.rxqueue = queue.Queue()
        self.exit_requested = False
        self.opened = False
        self.serial = None

    def open(self):
        if self.opened:
            return self
        self.serial = open_port(self.port, timeout=None)
        if self.tx_stmin_us is not None:
            # Spacing of the bridge's own transmissions, not the STmin sent to the ISO-TP partner.
            self.serial.write(frame(FLAG_SETTINGS | SETTING_STMIN, self.rxid, self.txid,
                                    self.tx_stmin_us.to_bytes(2, "little")))
        self.exit_requested = False
        self.rxthread = threading.Thread(target=self._rx_task, daemon=True)
        self.rxthread.start()
        self.opened = True
        return self

    def _rx_task(self):
        while not self.exit_requested:
            try:
                header = self.serial.read(8)
                if header == b"assertio":
                    self.logger.error("bridge firmware assertion: %r", self.serial.read_all())
                    self.exit_requested = True
                    break
                size = int.from_bytes(header[6:8], "little")
                sender = int.from_bytes(header[4:6], "little")
                data = self.serial.read(size)
                if data is not None and sender == self.rxid:
                    self.rxqueue.put(data)
            except Exception:
                self.exit_requested = True

    def close(self):
        self.exit_requested = True
        if self.serial is not None:
            self.serial.close()
        self.opened = False

    def is_open(self):
        return self.opened

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def specific_send(self, payload):
        self.serial.write(frame(0x00, self.rxid, self.txid, payload))

    def specific_wait_frame(self, timeout=None):
        # Fixed 10 s like VW_Flash: the client's own P2 timer still applies, and the cluster can be slow
        # to answer right after a reset.
        if not self.opened:
            raise RuntimeError("bridge connection is not open")
        try:
            return self.rxqueue.get(block=True, timeout=10)
        except queue.Empty:
            raise TimeoutException("no frame from the bridge within 10 s")

    def empty_rxqueue(self):
        while not self.rxqueue.empty():
            self.rxqueue.get_nowait()
