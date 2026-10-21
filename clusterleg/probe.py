"""Read-only checks: which links the bridge firmware has, and which modules answer."""

import time

from udsoncan import services

from .bridge import SETTING_GAP, SETTING_STMIN, open_port, query_setting
from .uds import MODULES, open_client, request

LINK_PAIRS = [
    ("Engine ECU (01)", 0x7E8, 0x7E0),
    ("Transmission (02)", 0x7E9, 0x7E1),
    ("stock 'Haldex' slot", 0x7ED, 0x7E5),
    ("DTC clear slot", 0x7E8, 0x700),
    ("Gateway (19)", 0x77A, 0x710),
    ("Instrument cluster (17)", 0x77E, 0x714),
]


def links(port):
    """Ask the bridge which rx/tx pairs it has ISO-TP links for. Talks only to the bridge, not the car."""
    ser = open_port(port, timeout=0.2)
    time.sleep(0.3)
    try:
        name = query_setting(ser, SETTING_GAP)
        print(f"bridge name: {name.decode(errors='replace') if name is not None else '(no answer)'}\n")
        found = {}
        for label, rx, tx in LINK_PAIRS:
            payload = query_setting(ser, SETTING_STMIN, rx, tx)
            found[label] = payload is not None
            status = f"LINK (stmin={int.from_bytes(payload, 'little')})" if payload is not None else "-"
            print(f"  {label:26s} rx=0x{rx:03X} tx=0x{tx:03X}  {status}")
    finally:
        ser.close()
    print()
    if not found["Engine ECU (01)"]:
        print("No answer even for the engine link: this firmware doesn't support the query. Inconclusive.")
    elif not found["Instrument cluster (17)"]:
        print("No cluster link. The firmware needs the cluster link (see clusterleg fw patch).")
    return found


def modules(port, only=None):
    """Read the VIN (22 F1 90) from each module. Read-only."""
    for key, (name, tx, rx) in MODULES.items():
        if only and only != key:
            continue
        print(f"asking {name} (a silent module takes ~10 s)...", flush=True)
        client = open_client(port, txid=tx, rxid=rx, timeout=3)
        with client:
            resp, err = request(client, services.ReadDataByIdentifier, b"\xF1\x90")
        if err == "no response":
            print(f"  {name:26s} NO ANSWER")
        elif err:
            print(f"  {name:26s} ANSWERED (refused: {err})")
        else:
            print(f"  {name:26s} ANSWERED  VIN {bytes(resp.data[2:]).decode(errors='replace')}")
