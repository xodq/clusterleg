"""
Audi Virtual Cockpit (Gen1 FPK) sport layout: S / RS / standard.

The layout is not in long coding. It is one byte in cluster EEPROM:

  10 03, 10 4F                       extended, then development session
  27 03 / 27 04 <seed + 20103>       security access level 3, login 20103
  23 14 03 00 3C 09 02               read  -> <layout> 57
  3D 14 03 00 3C 09 02 <layout> 57   write: 00 standard, 01 S, 03 RS
  11 01                              hard reset so the cluster loads it

This module only ever reads and writes those two bytes at 0x03003C09, and it refuses to write
unless what is already there looks like the layout setting (<00|01|03> 57).
"""

import time

import udsoncan
from udsoncan import services
from udsoncan.exceptions import NegativeResponseException

from .uds import UDS_ERRORS, die, request

DEV_SESSION = 0x4F
LOGIN = 20103
LAYOUT_ADDR = 0x03003C09
MARKER = 0x57
LAYOUTS = {"standard": 0x00, "s": 0x01, "rs": 0x03}
NAMES = {v: k for k, v in LAYOUTS.items()}
LAPTIMER_BYTE, LAPTIMER_BIT = 1, 3


def login_key(seed, login=LOGIN):
    return ((int.from_bytes(seed, "big") + login) & 0xFFFFFFFF).to_bytes(4, "big")


def fail(step, e):
    msg = f"\n{step} failed: {e}"
    if isinstance(e, NegativeResponseException):
        code = e.response.code
        msg = f"\n{step} refused: {e.response.code_name} (0x{code:02X})"
        if code == 0x22:
            msg += "\nconditionsNotCorrect: engine off, ignition on, hood open, handbrake on - then retry."
        elif code in (0x35, 0x36, 0x37):
            msg += "\nThe login key was rejected. Do NOT retry - repeated tries lock the cluster out for a while."
    die(msg + "\nNothing was written.")


def login(client):
    try:
        client.change_session(services.DiagnosticSessionControl.Session.extendedDiagnosticSession)
        client.change_session(DEV_SESSION)
    except UDS_ERRORS as e:
        fail("development session", e)
    try:
        seed = client.request_seed(3).service_data.seed
        client.send_key(4, login_key(seed))
    except UDS_ERRORS as e:
        fail("security access (login 20103)", e)
    print("Logged in (development session, login 20103).")


def location():
    return udsoncan.MemoryLocation(LAYOUT_ADDR, 2, address_format=32, memorysize_format=8)


def read_layout(client):
    try:
        data = bytes(client.read_memory_by_address(location()).service_data.memory_block)
    except UDS_ERRORS as e:
        fail("memory read at 0x03003C09", e)
    looks_right = len(data) == 2 and data[1] == MARKER and data[0] in NAMES
    name = NAMES.get(data[0], "unknown") if looks_right else "unrecognised"
    print(f"Layout bytes at 0x{LAYOUT_ADDR:08X}: {data.hex(' ').upper()}  -> {name}")
    return data, looks_right


def check_coding(client):
    resp, err = request(client, services.ReadDataByIdentifier, b"\x06\x00")
    if err:
        print("(could not read long coding to check the lap-timer prerequisite)")
        return
    coding = bytes(resp.data[2:])
    if not coding[LAPTIMER_BYTE] >> LAPTIMER_BIT & 1:
        print(f"NOTE: lap timer (coding byte {LAPTIMER_BYTE} bit {LAPTIMER_BIT}) is off. The guides say the sport "
              f"layout needs it: clusterleg coding write --byte {LAPTIMER_BYTE} --bit {LAPTIMER_BIT} --on")


def do_read(client):
    check_coding(client)
    login(client)
    return read_layout(client)[0]


def do_set(client, layout, yes=False, reset_wait=2):
    check_coding(client)
    login(client)
    current, looks_right = read_layout(client)
    if not looks_right:
        die("The bytes there don't look like the layout setting (<00|01|03> 57) on this cluster software.\n"
            "Refusing to write.")
    new = bytes([LAYOUTS[layout], MARKER])
    print(f"Planned: {current.hex(' ').upper()} -> {new.hex(' ').upper()}  ({layout})")
    if new == current:
        print("Already set. Nothing to write.")
        return False
    if not yes:
        print("\nDry run - nothing written. Add --yes to write.")
        return False

    client.tester_present()
    try:
        client.write_memory_by_address(location(), new)
    except UDS_ERRORS as e:
        fail("layout write", e)
    after, _ = read_layout(client)
    if after != new:
        die("Write accepted but the read-back differs.")
    print("Written and verified. Resetting the cluster (it goes dark for a few seconds)...")
    try:
        client.ecu_reset(services.ECUReset.ResetType.hardReset)
    except Exception as e:
        print(f"(reset request: {e} - turn the key off and on instead)")
    time.sleep(reset_wait)
    print("Done. Use the VIEW button on the steering wheel to cycle to the sport layout.")
    return True
