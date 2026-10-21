"""
Named cluster features (long coding bits), per cluster profile.

A profile only applies when the connected cluster identifies as that cluster (ODX file id and/or part
number). Writing a feature to a cluster that doesn't match is refused unless --any-car is given.

Status of each feature:
  TESTED  written on the profile's reference car and seen working
  FORUM   reported by owners/guides for this cluster family - NOT tested on the reference car
  CONFLICT sources disagree on the byte/bit - check with a coding helper first
  LOCKED  known to change something you don't want (shown, never written by 'features set')
"""

from dataclasses import dataclass, field

from udsoncan import services

from .coding import do_write, extended_session, read_coding
from .uds import die, request

TESTED, FORUM, CONFLICT, LOCKED = "TESTED", "FORUM", "CONFLICT", "LOCKED"
SRC_DX = "dx.dragan.ba/audi-sport"
SRC_8V = "audiworld / vwvortex A3 8V threads"


@dataclass
class Feature:
    key: str
    title: str
    byte: int
    bit: int
    status: str
    note: str = ""
    source: str = ""


@dataclass
class Profile:
    key: str
    name: str
    odx_ids: tuple = ()           # F19E prefixes
    part_numbers: tuple = ()      # F187 prefixes
    reference: str = ""
    features: list = field(default_factory=list)

    def matches(self, odx, part):
        odx_ok = any(odx.startswith(o) for o in self.odx_ids) if self.odx_ids else True
        part_ok = any(part.startswith(p) for p in self.part_numbers) if self.part_numbers else True
        return odx_ok and part_ok


A3_8V = Profile(
    "a3_8v_fpk", "Audi A3 8V Virtual Cockpit (Gen1 FPK)",
    odx_ids=("EV_DashBoardFPKAU37X",), part_numbers=("8V0920890",),
    reference="2017 A3 8V quattro, US, cluster 8V0920890 SW 0045, stock coding 08 E4 18 51 23 88 CF 05 02 85 E8 00 18 ...",
    features=[
        Feature("laptimer", "Lap timer", 1, 3, FORUM,
                "Listed as a prerequisite for the sport layout (which worked on the reference car).", SRC_8V),
        Feature("gmeter", "G-meter (acceleration display)", 10, 2, FORUM,
                "Can read 0 on cars without the sensor data.", SRC_8V),
        Feature("enginedata", "Engine data (power/torque)", 15, 4, FORUM,
                "Some owners got the G-meter here instead. May read 0 on non-RS cars.", SRC_8V),
        Feature("oiltemp_b1", "Oil temperature (variant A)", 1, 0, CONFLICT,
                "One list puts oil temp here, another at byte 10 bit 1.", SRC_8V),
        Feature("oiltemp_b10", "Oil temperature on all menus (variant B)", 10, 1, CONFLICT,
                "One list puts oil temp here, another at byte 1 bit 0.", SRC_8V),
        Feature("variant", "Car variant (whole byte 2)", 2, 1, LOCKED,
                "Changing byte 2 changed the range estimate on the reference car. Leave stock (18)."),
    ])

AUDI_GEN1_FPK = Profile(
    "audi_gen1_fpk", "Other Audi Gen1 Virtual Cockpit (TT 8S, A4 B9, A5 F5, Q5 FY, Q7 4M, Q2)",
    odx_ids=("EV_DashBoardFPK",),
    reference="none - not tested on any car here",
    features=[
        Feature("laptimer", "Lap timer", 1, 3, FORUM, "Prerequisite for the sport layout per the guide.", SRC_DX),
        Feature("gmeter", "G-meter (sport computer)", 10, 2, FORUM, "", SRC_DX),
        Feature("enginedata", "Engine data", 15, 4, FORUM, "", SRC_DX),
    ])

PROFILES = [A3_8V, AUDI_GEN1_FPK]  # most specific first
BY_KEY = {p.key: p for p in PROFILES}


def identify(client):
    def did(d):
        resp, err = request(client, services.ReadDataByIdentifier, d.to_bytes(2, "big"))
        return "" if err else bytes(resp.data[2:]).decode("ascii", "replace").strip("\x00 ").strip()
    return {"odx": did(0xF19E), "part": did(0xF187), "sw": did(0xF189)}


def match_profile(ident):
    return next((p for p in PROFILES if p.matches(ident["odx"], ident["part"])), None)


def all_feature_choices():
    """'profile:feature' ids for the deck's dropdown, writable ones only."""
    return [f"{p.key}:{f.key}" for p in PROFILES for f in p.features if f.status != LOCKED]


def scan(client, backup_path=None, profile_key=None):
    extended_session(client)
    ident = identify(client)
    profile = BY_KEY.get(profile_key) if profile_key else match_profile(ident)
    print(f"Cluster: part {ident['part'] or '?'}   SW {ident['sw'] or '?'}   ODX {ident['odx'] or '?'}")
    coding = read_coding(client)
    print(f"Coding:  {coding.hex(' ').upper()}")
    stock = None
    if backup_path:
        from .coding import load_backup
        stock = load_backup(backup_path)
        diff = [i for i in range(min(len(stock), len(coding))) if stock[i] != coding[i]]
        print(f"Stock:   {stock.hex(' ').upper()}")
        print("         " + (f"differs from stock at byte(s) {diff}" if diff else "identical to stock"))
    if profile is None:
        print("\nNo profile matches this cluster. Features are unknown here - use raw coding with care.")
        return ident, None, coding
    print(f"\nProfile: {profile.name}")
    print(f"Tested on: {profile.reference}\n")
    print(f"  {'feature':14s} {'byte.bit':8s} {'state':5s} {'stock':5s} {'status':8s} title")
    for f in profile.features:
        if f.byte >= len(coding):
            continue
        if f.status == LOCKED:
            state = f"{coding[f.byte]:02X}"
            st = f"{stock[f.byte]:02X}" if stock else ""
        else:
            state = "ON" if coding[f.byte] >> f.bit & 1 else "off"
            st = ("ON" if stock[f.byte] >> f.bit & 1 else "off") if stock else ""
        print(f"  {f.key:14s} {f.byte:>3d}.{f.bit:<4d} {state:5s} {st:5s} {f.status:8s} {f.title}")
        if f.note:
            print(f"  {'':14s} {'':8s} {'':5s} {'':5s} {'':8s} - {f.note}")
    print("\nFORUM / CONFLICT features are not tested on the reference car and may not work on yours.")
    return ident, profile, coding


def set_feature(client, feature_id, on, backup_path, yes=False, force=False, any_car=False, workshop_hex=None):
    pkey, _, fkey = feature_id.partition(":")
    profile = BY_KEY.get(pkey)
    feat = next((f for f in profile.features if f.key == fkey), None) if profile else None
    if feat is None:
        die(f"unknown feature '{feature_id}'. Choices: {', '.join(all_feature_choices())}")
    if feat.status == LOCKED:
        die(f"{feat.title} is LOCKED: {feat.note}")
    extended_session(client)
    ident = identify(client)
    if not profile.matches(ident["odx"], ident["part"]):
        msg = (f"This cluster (part {ident['part'] or '?'}, ODX {ident['odx'] or '?'}) is not a "
               f"'{profile.name}'.")
        if not any_car:
            die(msg + "\nRefusing. Pass --any-car to write it anyway.")
        print("WARNING: " + msg + " Writing anyway (--any-car).")
    print(f"{feat.title}: byte {feat.byte} bit {feat.bit} -> {'ON' if on else 'off'}   [{feat.status}]")
    if feat.status != TESTED:
        print("Not tested on the reference car - it may do nothing, or something else, on yours.")
    return do_write(client, backup_path, feat.byte, bit=feat.bit, on=on, yes=yes, force=force,
                    workshop_hex=workshop_hex)


# ------------------------------------------------------------------ mileage (read-only)
MILEAGE_DIDS = [(0x295A, "odometer"), (0x2203, "distance")]


def mileage(port, modules=None):
    """Read the odometer value each module stores. Read-only - a mismatch between modules is the
    classic sign of a tampered odometer."""
    from .uds import MODULES, open_client
    rows = []
    for key, (name, tx, rx) in MODULES.items():
        if modules and key not in modules:
            continue
        print(f"asking {name}...", flush=True)
        with open_client(port, txid=tx, rxid=rx, timeout=3) as client:
            try:
                client.change_session(services.DiagnosticSessionControl.Session.extendedDiagnosticSession)
            except Exception:
                pass
            for did, label in MILEAGE_DIDS:
                resp, err = request(client, services.ReadDataByIdentifier, did.to_bytes(2, "big"))
                if err == "no response":
                    print(f"  {name:26s} NO ANSWER")
                    break
                if err:
                    continue
                raw = bytes(resp.data[2:])
                km = int.from_bytes(raw, "big") if raw else 0
                rows.append((name, did, km))
                print(f"  {name:26s} 0x{did:04X} {label:9s} {raw.hex(' ').upper():12s} = {km:>9,} km  "
                      f"({km * 0.621371:,.0f} mi)")
    if rows:
        values = {km for _, _, km in rows if km}
        spread = max(values) - min(values) if values else 0
        print(f"\nspread between modules: {spread:,} km"
              + ("  (normal - modules update at different times)" if spread < 500 else "  <-- check this"))
    else:
        print("\nNo module reported a mileage value.")
    return rows
