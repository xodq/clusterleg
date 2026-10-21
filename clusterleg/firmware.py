"""
Bridge firmware: dump the active app partition and patch in a cluster link (offline).

clusterleg never flashes the bridge. Writing a patched image to the ESP32 is up to you.

The stock esp32-isotp-ble-bridge firmware hard-codes four ISO-TP links (ECU, TCU, a 'Haldex' slot
0x7E5/0x7ED, DTC clear). The patch retargets the unused Haldex slot to the instrument cluster
(0x714/0x77E): two MOVI immediates, then the ESP image checksum and appended SHA-256 are fixed.
It only applies to a build where the expected bytes are found at the expected offsets; anything
else is refused. Your own dump is the restore file - keep it.
"""

import hashlib
import struct
import subprocess
import sys
from pathlib import Path

# (file offset of the 3-byte xtensa MOVI, expected bytes, new bytes, meaning)
CLUSTER_PATCH = [
    (0x28C34, bytes([0xC2, 0xA7, 0xED]), bytes([0xC2, 0xA7, 0x7E]), "Haldex slot response 0x7ED -> 0x77E"),
    (0x28C37, bytes([0xB2, 0xA7, 0xE5]), bytes([0xB2, 0xA7, 0x14]), "Haldex slot request  0x7E5 -> 0x714"),
]


def esptool(port, baud, *args):
    cmd = [sys.executable, "-m", "esptool", "--port", port, "--baud", str(baud), *args]
    print("\n$ " + " ".join(cmd), flush=True)
    if subprocess.run(cmd).returncode != 0:
        sys.exit("esptool failed. Try --baud 115200, and unplug/replug the bridge.")


def parse_partitions(blob):
    parts = []
    for i in range(0, len(blob), 32):
        e = blob[i:i + 32]
        if len(e) < 32 or e[0:2] != b"\xAA\x50":
            continue
        offset, size = struct.unpack("<II", e[4:12])
        parts.append(dict(type=e[2], subtype=e[3], offset=offset, size=size,
                          label=e[12:28].split(b"\x00")[0].decode(errors="replace")))
    return parts


def active_app(parts, otadata=None):
    apps = [p for p in parts if p["type"] == 0]
    ota = sorted([p for p in apps if 0x10 <= p["subtype"] <= 0x1F], key=lambda p: p["subtype"])
    if otadata and ota:
        seqs = [s for s in (struct.unpack("<I", otadata[o:o + 4])[0] for o in (0, 0x1000))
                if s not in (0, 0xFFFFFFFF)]
        if seqs:
            return ota[(max(seqs) - 1) % len(ota)]
    return next((p for p in apps if p["subtype"] == 0), ota[0] if ota else (apps[0] if apps else None))


def dump(port, baud=230400):
    esptool(port, baud, "read-flash", "0x8000", "0x1000", "bridge_partition_table.bin")
    parts = parse_partitions(Path("bridge_partition_table.bin").read_bytes())
    if not parts:
        sys.exit("could not parse a partition table at 0x8000")
    print("\nPartition table:")
    for p in parts:
        print(f"  {p['label']:12s} type={p['type']:#04x} subtype={p['subtype']:#04x} "
              f"offset={p['offset']:#08x} size={p['size']:#08x}")
    otadata = None
    od = next((p for p in parts if p["type"] == 1 and p["subtype"] == 0), None)
    if od:
        esptool(port, baud, "read-flash", hex(od["offset"]), "0x2000", "bridge_otadata.bin")
        otadata = Path("bridge_otadata.bin").read_bytes()
    app = active_app(parts, otadata)
    if app is None:
        sys.exit("no app partition found")
    out = f"bridge_app_{app['label']}_{app['offset']:#x}.bin"
    print(f"\nActive firmware: {app['label']} at {app['offset']:#x}")
    esptool(port, baud, "read-flash", hex(app["offset"]), hex(app["size"]), out)
    print(f"\nSaved {out}. This is your restore file - keep it.")
    return out, app["offset"]


def image_layout(data):
    if data[0] != 0xE9:
        raise ValueError("not an ESP32 app image")
    pos = 24
    for _ in range(data[1]):
        pos += 8 + struct.unpack_from("<I", data, pos + 4)[0]
    return pos + ((15 - (pos % 16)) % 16), bool(data[23])


def image_checksum(data):
    chk, pos = 0xEF, 24
    for _ in range(data[1]):
        size = struct.unpack_from("<I", data, pos + 4)[0]
        for b in data[pos + 8:pos + 8 + size]:
            chk ^= b
        pos += 8 + size
    return chk


def image_ok(data):
    chk_off, has_hash = image_layout(data)
    if data[chk_off] != image_checksum(data):
        return False
    return not has_hash or data[chk_off + 1:chk_off + 33] == hashlib.sha256(data[:chk_off + 1]).digest()


def patch(src, dst, patches=CLUSTER_PATCH):
    orig = Path(src).read_bytes()
    if not image_ok(orig):
        sys.exit(f"{src} is not an intact ESP32 image - re-dump before patching")
    data = bytearray(orig)
    for off, old, new, why in patches:
        if bytes(data[off:off + 3]) != old:
            sys.exit(f"unexpected bytes at 0x{off:X}: {bytes(data[off:off + 3]).hex()} (wanted {old.hex()}).\n"
                     "This firmware build isn't one the patch knows. Not patching.")
        data[off:off + 3] = new
        print(f"  0x{off:X}: {old.hex(' ')} -> {new.hex(' ')}   ({why})")
    chk_off, has_hash = image_layout(data)
    data[chk_off] = image_checksum(data)
    if has_hash:
        data[chk_off + 1:chk_off + 33] = hashlib.sha256(bytes(data[:chk_off + 1])).digest()
    out = bytes(data[:chk_off + 1 + (32 if has_hash else 0)])
    if not image_ok(out):
        sys.exit("patched image failed verification - not writing")
    Path(dst).write_bytes(out)
    print(f"Wrote {dst} ({len(out)} bytes, checksum + sha256 verified). {src} is your restore file.")
    return out

