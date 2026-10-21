"""
Command catalog for the ops deck: every action as a form that builds the exact command line.

Commands are written the way you'd type them ("python flash_bin.py --bin ..."); the deck swaps the
leading "python" for the real interpreter when it runs them. All commands run with the VW_Flash
source folder as the working directory, so VW_Flash scripts and cluster backups resolve there.

Risk levels:  read  = runs freely
              write = changes a module setting (needs ARM)
              flash = rewrites module or bridge firmware (needs ARM, extra confirm)
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import features

READ, WRITE, FLASH = "read", "write", "flash"

MODULE_FLAGS = {
    "simos 18.1/6 (engine)": [],
    "simos 18.10 (engine)": ["--simos1810"],
    "dq250-mqb dsg": ["--dsg"],
    "dq381 dsg (cal only)": ["--dq381"],
}
VWFLASH_ACTIONS = ["get_ecu_info", "get_dtcs", "log", "flash_cal", "flash_bin", "flash_frf", "flash_unlock",
                   "checksum", "checksum_ecm3", "lzss", "encrypt", "prepare"]
VWFLASH_RISK = {"get_ecu_info": READ, "get_dtcs": READ, "log": READ, "checksum": READ, "checksum_ecm3": READ,
                "lzss": READ, "encrypt": READ, "prepare": READ}
VWFLASH_OFFLINE = {"checksum", "checksum_ecm3", "lzss", "encrypt", "prepare"}
BIN = [("Flash bin", "*.bin"), ("All files", "*.*")]
FRF = [("FRF flashpack", "*.frf"), ("All files", "*.*")]
TXT = [("Backup", "*.txt"), ("All files", "*.*")]


@dataclass
class Field:
    key: str
    label: str
    kind: str = "text"            # text | int | file | dir | choice | check | list
    flag: Optional[str] = None    # None + not positional -> value only used by the builder
    default: object = ""
    choices: list = field(default_factory=list)
    help: str = ""
    filetypes: list = field(default_factory=list)
    setting: Optional[str] = None  # remembered across sessions under this name
    positional: bool = False
    required: bool = False


@dataclass
class Command:
    id: str
    group: str
    title: str
    risk: str
    desc: str
    head: list                     # leading argv, may contain {port}
    fields: list = field(default_factory=list)
    long_running: bool = False
    warn: str = ""
    risk_fn: Optional[Callable] = None   # values -> effective risk (e.g. dry run = read)
    build_fn: Optional[Callable] = None  # (values, port) -> extra argv, replaces generic field handling

    def effective_risk(self, values):
        return self.risk_fn(values) if self.risk_fn else self.risk


def py(script, *rest):
    return ["python", script, *rest]


def cl(*rest):
    return ["python", "-m", "clusterleg", "--port", "{port}", *rest]


USB = ["--interface", "USBISOTP_{port}"]


def dry_or(risk, key="dry"):
    return lambda v: READ if v.get(key) else risk


def yes_or(risk, key="yes"):
    return lambda v: risk if v.get(key) else READ


def generic_args(cmd, values):
    out = []
    for f in cmd.fields:
        v = values.get(f.key, f.default)
        if f.kind == "check":
            if v and f.flag:
                out.append(f.flag)
        elif f.kind == "list":
            for item in [s.strip() for s in str(v).split(",") if s.strip()]:
                out += [f.flag, item]
        elif v not in ("", None) and (f.flag or f.positional):
            out += [str(v)] if f.positional else [f.flag, str(v)]
    return out


def build_argv(cmd, values, port):
    head = [a.replace("{port}", port) for a in cmd.head]
    tail = cmd.build_fn(values, port) if cmd.build_fn else generic_args(cmd, values)
    return head + tail


def missing_required(cmd, values):
    return [f.label for f in cmd.fields if f.required and values.get(f.key, f.default) in ("", None)]


# ---------------------------------------------------------------- shared field factories
def f_stmin():
    return Field("stmin", "STmin (ns)", "int", "--stmin", "", help="TX frame spacing. Blank = 350000. "
                 "900000 / 1400000 if the bridge wedges mid-block.", setting="stmin")


def f_dry(default=True):
    return Field("dry", "Dry run (touch no hardware)", "check", "--dry-run", default)


def f_yes():
    return Field("yes", "--yes  (really write; off = dry run)", "check", "--yes", False)


def f_backup():
    return Field("backup", "Coding backup", "file", "--backup", "", filetypes=TXT, setting="cluster_backup",
                 required=True, help="The ORIGINAL backup is your stock restore point.")


def f_force(default=False):
    return Field("force", "--force  (keep this backup as restore point)", "check", "--force", default,
                 help="Needed once the coding already differs from the backup file.")


# ---------------------------------------------------------------- builders with special logic
def build_vwflash(values, port):
    action = values.get("action", "get_ecu_info")
    out = ["--action", action]
    out += MODULE_FLAGS.get(values.get("module"), [])
    if action not in VWFLASH_OFFLINE:
        out += ["--interface", "USBISOTP", "--usb_name", port]
    for key, flag in (("frf", "--frf"), ("input_bin", "--input_bin"), ("output_bin", "--output_bin")):
        if values.get(key):
            out += [flag, values[key]]
    for key, flag in (("infile", "--infile"), ("block", "--block")):
        for item in [s.strip() for s in str(values.get(key, "")).split(",") if s.strip()]:
            out += [flag, item]
    if values.get("patch_cboot"):
        out.append("--patch-cboot")
    if values.get("is_early"):
        out.append("--is_early")
    if action == "log" and values.get("mode"):
        out += ["--mode", values["mode"]]
    return out


def build_ecu_info(values, port):
    return MODULE_FLAGS.get(values.get("module"), []) + ["--interface", "USBISOTP", "--usb_name", port]


# ---------------------------------------------------------------- block picking for interrupted flashes
ENGINE_ORDER = ["1", "2", "3", "4", "5"]
UNLOCK_ORDER = ["1", "2", "3", "4", "9", "5"]
BLOCK_MODES = ["all", "start from", "only", "custom list"]


def block_fields(order, mode="all", block=None):
    return [Field("bmode", "Blocks", "choice", None, mode, BLOCK_MODES,
                  help="start from = resume a hung flash at that block"),
            Field("bpick", "Block", "choice", None, block or order[0], order, help=f"order: {','.join(order)}"),
            Field("blist", "Custom list", "text", None, "", help="e.g. 1,5 - used with 'custom list'")]


def pick_blocks(values, order):
    mode, pick = values.get("bmode", "all"), str(values.get("bpick", order[0]))
    if mode == "start from" and pick in order:
        return order[order.index(pick):]
    if mode == "only":
        return [pick]
    if mode == "custom list":
        return [b.strip() for b in str(values.get("blist", "")).split(",") if b.strip()] or order
    return order


def with_blocks(order):
    def build(values, port):
        return ["--blocks", ",".join(pick_blocks(values, order))]
    return build


def flash_cmd(cid, title, desc, script, fields, order, default_mode="all", warn=""):
    cmd = Command(cid, "ENGINE // 01", title, FLASH, desc, py(script, *USB),
                  fields[:1] + block_fields(order, default_mode, "4" if default_mode == "start from" else None)
                  + fields[1:], risk_fn=dry_or(FLASH), warn=warn)
    blocks = with_blocks(order)
    cmd.build_fn = lambda v, p, c=cmd: generic_args(c, v) + blocks(v, p)
    return cmd


# ---------------------------------------------------------------- the catalog
CATALOG = [
    # BRIDGE
    Command("links", "BRIDGE", "Link table", READ,
            "Which CAN links the bridge firmware has. Talks only to the bridge, never the car.", cl("links")),
    Command("probe", "BRIDGE", "Module sweep", READ,
            "Reads the VIN from each module to see who answers. Ignition on.", cl("probe"),
            [Field("only", "Only", "choice", "--only", "", ["", "engine", "dsg", "cluster", "gateway"])]),
    Command("listen", "BRIDGE", "Passive tap", READ,
            "Records a module's replies while OBDeleven/VCDS works on it (Y-splitter). STOP when done.",
            cl("listen"), [Field("rx", "Reply id", "text", "--rx", "0x77E", help="0x77E cluster, 0x7E8 engine")],
            long_running=True),
    Command("bootcheck", "BRIDGE", "Boot check", READ,
            "Resets the ESP32 into normal firmware and prints its boot log.",
            py("bridge_check.py", "--port", "{port}"),
            [Field("seconds", "Listen (s)", "text", "--seconds", "5")]),

    # ENGINE
    Command("ecuinfo", "ENGINE // 01", "Identify ECU", READ,
            "VW_Flash get_ecu_info: VIN, part numbers, software, block fingerprints.",
            py("VW_Flash.py", "--action", "get_ecu_info"),
            [Field("module", "Target", "choice", None, "simos 18.1/6 (engine)", list(MODULE_FLAGS))],
            build_fn=build_ecu_info),
    Command("dtcs_all", "ENGINE // 01", "DTC sweep (all)", READ,
            "Every stored code, status mask 0xFF - catches pending codes VW_Flash's read hides.",
            py("read_dtcs_all.py", *USB),
            [Field("mask", "Status mask", "text", "--mask", "0xFF"),
             Field("filtered", "Confirmed faults only (VW_Flash mask)", "check", "--filtered", False)]),
    Command("dtcs_vwf", "ENGINE // 01", "DTC read (VW_Flash)", READ,
            "VW_Flash's built-in DTC read (filtered).",
            py("VW_Flash.py", "--action", "get_dtcs", "--interface", "USBISOTP", "--usb_name", "{port}")),
    Command("dtc_detail", "ENGINE // 01", "DTC freeze frame", READ,
            "Snapshot + extended data for one code.", py("dtc_detail.py", *USB),
            [Field("dtc", "DTC", "text", "--dtc", "", help="decimal or 0x hex", required=True)]),
    Command("clear", "ENGINE // 01", "Clear DTCs + log", WRITE,
            "OBD mode 04 clear, read back what survived, then start the logger.",
            py("clear_and_log.py", *USB),
            [Field("no_clear", "Don't clear (read + log only)", "check", "--no-clear", False),
             Field("no_log", "Don't log afterwards", "check", "--no-log", True),
             Field("mode", "Log mode", "choice", "--mode", "22", ["22", "3E", "HSL"]),
             Field("path", "Logger dir", "dir", "--path", "", setting="logger_dir")],
            long_running=True, risk_fn=lambda v: READ if v.get("no_clear") else WRITE),
    Command("logger", "ENGINE // 01", "Live logger", READ,
            "Simos HSL logger to CSV. STOP closes the CSV cleanly.", py("run_logger.py", *USB),
            [Field("mode", "Mode", "choice", "--mode", "22", ["22", "3E", "HSL"]),
             Field("path", "Logger dir", "dir", "--path", "", setting="logger_dir",
                   help="folder holding log_config.yaml")],
            long_running=True),
    Command("flash_cal", "ENGINE // 01", "Flash CAL (block 5)", FLASH,
            "Calibration Flash Unlocked: block 5 only, ~20 s. ASW on the car must match the bin.",
            py("flash_cal.py", *USB),
            [Field("bin", "Tune bin (4 MB)", "file", "--bin", "", filetypes=BIN, setting="tune_bin", required=True),
             f_stmin(), f_dry()],
            risk_fn=dry_or(FLASH), warn="Module target is the ENGINE ECU. Ignition on, battery charger recommended."),
    flash_cmd("flash_bin", "Flash full bin",
              "Full Flash Unlocked, per block. Blocks that write stay written - after a hang, 'start from' the "
              "block that failed instead of redoing all five.", "flash_bin.py",
              [Field("bin", "Tune bin (4 MB)", "file", "--bin", "", filetypes=BIN, setting="tune_bin", required=True),
               Field("patch_cboot", "Patch CBOOT (sample mode)", "check", "--patch-cboot", True,
                     help="Required for a modified calibration. Only matters when block 1 is flashed."),
               f_stmin(), Field("chunk", "Chunk (bytes)", "text", "--chunk", "", help="blank = 0xFFD"), f_dry()],
              ENGINE_ORDER, warn="Engine ECU flash, several minutes. Do not let the bridge or battery drop."),
    flash_cmd("flash_frf", "Flash stock (re-lock)",
              "Back to factory from an FRF flashpack. 'start from' resumes an interrupted re-lock.", "flash_frf.py",
              [Field("frf", "Stock FRF", "file", "--frf", "", filetypes=FRF, setting="frf_stock", required=True),
               f_stmin(), Field("chunk", "Chunk (bytes)", "text", "--chunk", ""), f_dry()],
              ENGINE_ORDER, warn="Re-locks the ECU to stock."),
    flash_cmd("unlock_resume", "Unlock (resume subset)",
              "Simos18.1 unlock from the donor FRF, order 1,2,3,4,9(patch),5. Default resumes at block 4.",
              "resume_unlock.py",
              [Field("frf", "Donor FRF", "file", "--frf", "", filetypes=FRF, setting="frf_unlock", required=True),
               f_stmin(), f_dry()],
              UNLOCK_ORDER, default_mode="start from",
              warn="Unlocking wipes ASW/CAL back to the donor. Don't run it on an ECU that is already unlocked."),
    Command("vwflash", "ENGINE // 01", "VW_Flash (any action)", FLASH,
            "Raw VW_Flash.py with every flag: offline checksum/prepare/encrypt too.", py("VW_Flash.py"),
            [Field("action", "Action", "choice", None, "get_ecu_info", VWFLASH_ACTIONS),
             Field("module", "Target", "choice", None, "simos 18.1/6 (engine)", list(MODULE_FLAGS)),
             Field("frf", "FRF", "file", None, "", filetypes=FRF),
             Field("input_bin", "Input bin", "file", None, "", filetypes=BIN),
             Field("output_bin", "Output bin", "text", None, ""),
             Field("infile", "Infiles (comma sep.)", "text", None, ""),
             Field("block", "Blocks (comma sep.)", "text", None, "", help="names or numbers, one per infile"),
             Field("patch_cboot", "Patch CBOOT", "check", None, False),
             Field("is_early", "Early car (ECM3 checksum)", "check", None, False),
             Field("mode", "Log mode", "choice", None, "22", ["22", "3E", "HSL"])],
            build_fn=build_vwflash, risk_fn=lambda v: VWFLASH_RISK.get(v.get("action"), FLASH),
            warn="Check the Target. Flashing with a DSG target writes the TRANSMISSION controller."),

    # DSG
    Command("tcuinfo", "DSG // 02", "Identify TCU", READ, "get_ecu_info against the DQ250 DSG.",
            py("VW_Flash.py", "--action", "get_ecu_info", "--dsg", "--interface", "USBISOTP", "--usb_name", "{port}")),
    Command("tcudtcs", "DSG // 02", "TCU DTC sweep", READ, "Every code stored in the DSG (mask 0xFF).",
            py("read_dtcs_all.py", *USB, "--dsg")),

    # CLUSTER
    Command("c_info", "CLUSTER // 17", "Identify cluster", READ,
            "Part number, software, workshop code, long coding.", cl("coding", "info")),
    Command("c_read", "CLUSTER // 17", "Backup coding", READ,
            "Prints the coding and saves a timestamped backup file.", cl("coding", "read")),
    Command("c_layout_read", "CLUSTER // 17", "Layout status", READ,
            "Logs in (20103) and reads the layout byte. Writes nothing.", cl("layout", "read")),
    Command("c_layout", "CLUSTER // 17", "Set cockpit layout", WRITE,
            "standard / S / RS. Then cycle VIEW on the wheel. Engine off, hood open, handbrake on.",
            cl("layout", "set"),
            [Field("layout", "Layout", "choice", None, "s", ["standard", "s", "rs"], positional=True), f_yes()],
            risk_fn=yes_or(WRITE)),
    Command("c_scan", "CLUSTER // 17", "Feature scan", READ,
            "Identifies the cluster, picks its profile, shows every known feature ON/off next to stock. Read-only.",
            cl("features", "scan"),
            [Field("backup", "Stock backup", "file", "--backup", "", filetypes=TXT, setting="cluster_backup"),
             Field("profile", "Force profile", "choice", "--profile", "", [""] + list(features.BY_KEY))]),
    Command("c_feature", "CLUSTER // 17", "Feature on/off", WRITE,
            "Named feature by car profile. a3_8v_fpk = YOUR cluster (8V0920890). audi_gen1_fpk = other Audi Gen1 "
            "cockpits, untested. Refuses a cluster that doesn't match the profile unless 'any car' is ticked.",
            cl("features", "set"),
            [Field("feature", "Feature", "choice", None, "a3_8v_fpk:laptimer", features.all_feature_choices(),
                   positional=True),
             Field("state", "State", "choice", None, "on", ["on", "off"], positional=True),
             f_backup(), f_force(True),
             Field("any_car", "--any-car  (skip the profile match check)", "check", "--any-car", False), f_yes()],
            risk_fn=yes_or(WRITE),
            warn="FORUM/CONFLICT features are not tested on your car - they may do nothing or something else. "
                 "'Restore stock coding' undoes everything."),
    Command("mileage", "CLUSTER // 17", "Mileage (all modules)", READ,
            "Odometer value each module stores (engine, DSG, cluster, gateway). A big spread = tamper sign.",
            cl("mileage")),
    Command("c_write", "CLUSTER // 17", "Coding: byte / bit", WRITE,
            "Any byte (hex value) or any bit. Leave Value blank to use Bit.", cl("coding", "write"),
            [f_backup(), Field("byte", "Byte", "int", "--byte", "", required=True, help="0-based, VCDS numbering"),
             Field("value", "Value (hex)", "text", "--value", ""),
             Field("bit", "Bit 0-7", "text", "--bit", ""),
             Field("off", "Bit off (default on)", "check", "--off", False),
             f_force(), f_yes()],
            risk_fn=yes_or(WRITE), warn="Byte 2 is the car variant - it changes the range estimate. Leave it stock."),
    Command("c_restore", "CLUSTER // 17", "Restore stock coding", WRITE,
            "Writes the backup file back.", cl("coding", "restore"), [f_backup(), f_yes()],
            risk_fn=yes_or(WRITE)),

    # FIRMWARE
    Command("fw_dump", "BRIDGE FW", "Dump bridge firmware", READ,
            "Saves the partition table and the active app - your restore file.", cl("fw", "dump"),
            [Field("baud", "Baud", "choice", "--baud", "230400", ["230400", "115200"])]),
    Command("fw_patch", "BRIDGE FW", "Patch in cluster link", READ,
            "Offline: retargets the unused Haldex slot to 0x714/0x77E and fixes checksums. clusterleg never flashes "
            "the bridge - writing the image is up to you.", cl("fw", "patch"),
            [Field("src", "Dumped firmware", "file", None, "", filetypes=BIN, setting="fw_original",
                   positional=True, required=True),
             Field("dst", "Output file", "text", None, "bridge_patched.bin", positional=True)]),

    # RAW
    Command("custom", "RAW", "Custom command", WRITE,
            "Anything you'd type in the terminal. Starts in the VW_Flash folder; 'python' = the deck's Python.",
            [], [Field("line", "Command line", "text", None, "python cluster_probe.py", required=True)],
            build_fn=lambda v, p: split_cmdline(v.get("line", "").replace("{port}", p))),
]


def split_cmdline(line):
    """Split a Windows-style command line: spaces separate, double quotes group."""
    out, cur, quoted, started = [], [], False, False
    for ch in line:
        if ch == '"':
            quoted, started = not quoted, True
        elif ch.isspace() and not quoted:
            if started:
                out.append("".join(cur))
                cur, started = [], False
        else:
            cur.append(ch)
            started = True
    if started:
        out.append("".join(cur))
    return out


BY_ID = {c.id: c for c in CATALOG}
GROUPS = list(dict.fromkeys(c.group for c in CATALOG))

# Built-in playbooks: (command id, field overrides). Lines are built with the current port and form values.
PLAYBOOKS = {
    "Preflight (read-only)": [("links", {}), ("probe", {}), ("ecuinfo", {}), ("dtcs_all", {})],
    "Cluster recon (read-only)": [("links", {}), ("probe", {"only": "cluster"}), ("c_info", {}),
                                  ("c_read", {}), ("c_layout_read", {})],
    "Cockpit S layout": [("c_read", {}), ("c_layout_read", {}), ("c_layout", {"layout": "s", "yes": True})],
    "Cockpit RS layout": [("c_read", {}), ("c_layout_read", {}), ("c_layout", {"layout": "rs", "yes": True})],
    "Cockpit back to standard": [("c_layout", {"layout": "standard", "yes": True})],
    "Tune swap: CAL dry run": [("ecuinfo", {}), ("flash_cal", {"dry": True})],
}
