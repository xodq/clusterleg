"""
Instrument cluster long coding: read, back up, change one byte or bit, restore.

  10 03                     extended session
  22 06 00                  read long coding
  2E F1 98 <workshop code>  -- VW "coding login" - without these two the cluster
  2E F1 99 <YY MM DD BCD>   -- refuses the coding write with requestSequenceError (0x24)
  2E 06 00 <coding>         write long coding

F1A5 (coding fingerprint) is read-only; the cluster fills it from F198 when coding is stored.
By default the workshop code already in F1A5 is reused, so the fingerprint does not change.
"""

import time
from pathlib import Path

from udsoncan import services

from .uds import UDS_ERRORS, die, request

CODING_DID = b"\x06\x00"

INFO_DIDS = [
    (0xF190, "VIN"),
    (0xF187, "spare part number"),
    (0xF189, "software version"),
    (0xF19E, "ASAM/ODX file id"),
    (0xF1A2, "ASAM/ODX version"),
    (0xF186, "active session"),
    (0xF1A5, "coding fingerprint (workshop code)"),
    (0x0600, "long coding"),
]


def extended_session(client):
    try:
        client.change_session(services.DiagnosticSessionControl.Session.extendedDiagnosticSession)
    except UDS_ERRORS as e:
        die(f"extended session refused: {e}")


def read_coding(client):
    resp, err = request(client, services.ReadDataByIdentifier, CODING_DID)
    if err:
        die(f"coding read refused: {err}")
    if resp.data[:2] != CODING_DID:
        die(f"unexpected response: {resp.data.hex(' ')}")
    return bytes(resp.data[2:])


def coding_login(client, workshop_hex=None):
    if workshop_hex:
        workshop = bytes.fromhex(workshop_hex.replace(" ", ""))
    else:
        resp, err = request(client, services.ReadDataByIdentifier, b"\xF1\xA5")
        if err:
            die(f"could not read the current coding fingerprint: {err}")
        workshop = bytes(resp.data[2:])
    date = bytes.fromhex(time.strftime("%y%m%d"))
    for did, data, name in ((b"\xF1\x98", workshop, "workshop code F198"),
                            (b"\xF1\x99", date, "programming date F199")):
        print(f"Writing {name}: {data.hex(' ').upper()}")
        _, err = request(client, services.WriteDataByIdentifier, did + data)
        if err:
            die(f"The cluster refused the {name} write: {err}\nCoding was not touched.")


def write_coding(client, coding):
    _, err = request(client, services.WriteDataByIdentifier, CODING_DID + coding)
    if err:
        die(f"The cluster refused the coding write: {err}\nNothing was changed.")


def show(coding, highlight=()):
    print(f"  {coding.hex(' ').upper()}   ({len(coding)} bytes)")
    for i, b in enumerate(coding):
        mark = "  <-- changing" if i in highlight else ""
        print(f"  byte {i:2d}: {b:02X}  {b:08b}{mark}")


def load_backup(path):
    return bytes.fromhex(Path(path).read_text().strip())


def do_info(client):
    extended_session(client)
    print("Cluster identifiers (read-only):\n")
    for did, name in INFO_DIDS:
        resp, err = request(client, services.ReadDataByIdentifier, did.to_bytes(2, "big"))
        if err:
            print(f"  0x{did:04X} {name:36s} -> {err}")
            continue
        payload = bytes(resp.data[2:])
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in payload)
        print(f"  0x{did:04X} {name:36s} -> {payload.hex(' ').upper()}   '{text}'")


def do_read(client, out_dir="."):
    extended_session(client)
    current = read_coding(client)
    print("Current cluster coding:")
    show(current)
    out = Path(out_dir) / f"cluster_coding_backup_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    out.write_text(current.hex(" ").upper() + "\n")
    print(f"\nBackup saved: {out.resolve()}\nKeep this file - 'coding restore' puts it back.")
    return out


def plan_change(current, index, value=None, bit=None, on=True):
    if not 0 <= index < len(current):
        die(f"byte index must be 0..{len(current) - 1}")
    new = bytearray(current)
    if bit is not None:
        if not 0 <= bit <= 7:
            die("bit must be 0..7")
        new[index] = current[index] | (1 << bit) if on else current[index] & ~(1 << bit) & 0xFF
    else:
        new[index] = int(value, 16)
    return bytes(new)


def do_write(client, backup_path, index, value=None, bit=None, on=True, yes=False, force=False,
             workshop_hex=None, restore=False):
    extended_session(client)
    current = read_coding(client)
    backup = load_backup(backup_path)
    if restore:
        new = backup
    else:
        if current != backup and not force:
            die("The car's coding differs from the backup file. Run 'coding read' for a fresh backup, "
                "or pass --force to keep the old file as your restore point.")
        new = plan_change(current, index, value, bit, on)

    changed = [i for i in range(len(new)) if i >= len(current) or new[i] != current[i]]
    print("Current coding:")
    show(current)
    print("\nPlanned coding:")
    show(new, changed)
    if not changed:
        print("\nNothing to change.")
        return False
    if not yes:
        print("\nDry run - nothing written. Add --yes to write.")
        return False

    client.tester_present()
    coding_login(client, workshop_hex)
    client.tester_present()
    write_coding(client, new)
    after = read_coding(client)
    if after != new:
        print("\nWrite accepted but the read-back differs:")
        show(after)
        die("Restore your backup if the cluster misbehaves.")
    print("\nWritten and verified. Turn the key off and on to see it.")
    return True
