"""clusterleg - Audi/VW instrument cluster tools for the ESP32 ISO-TP bridge."""

import argparse

from . import __version__, coding, features, firmware, layout, listen, probe
from .uds import MODULES, open_client


def build_parser():
    p = argparse.ArgumentParser(prog="clusterleg", description=__doc__)
    p.add_argument("--version", action="version", version=f"clusterleg {__version__}")
    p.add_argument("--port", default="COM4", help="bridge serial port (default COM4)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("deck", help="open the ops deck (GUI)")
    sub.add_parser("links", help="which CAN links the bridge firmware has (bridge only, read-only)")
    pr = sub.add_parser("probe", help="which modules answer (read-only)")
    pr.add_argument("--only", choices=sorted(MODULES))

    li = sub.add_parser("listen", help="passively record a module's replies while another tool runs")
    li.add_argument("--rx", type=lambda s: int(s, 0), default=0x77E, help="module reply id (default 0x77E cluster)")

    co = sub.add_parser("coding", help="cluster long coding")
    cs = co.add_subparsers(dest="action", required=True)
    cs.add_parser("info", help="identifiers + coding (read-only)")
    cs.add_parser("read", help="print coding and save a backup file")
    cw = cs.add_parser("write", help="change one byte (--value) or one bit (--bit --on/--off)")
    cw.add_argument("--backup", required=True, help="backup file from 'coding read'")
    cw.add_argument("--byte", type=int, required=True, help="0-based byte index (VCDS 'Byte 0' = first)")
    g = cw.add_mutually_exclusive_group(required=True)
    g.add_argument("--value", help="new byte value in hex, e.g. EC")
    g.add_argument("--bit", type=int, help="bit 0-7 (VCDS numbering)")
    onoff = cw.add_mutually_exclusive_group()
    onoff.add_argument("--on", dest="on", action="store_true", default=True)
    onoff.add_argument("--off", dest="on", action="store_false")
    cr = cs.add_parser("restore", help="write the backup file back")
    cr.add_argument("--backup", required=True)
    for c in (cw, cr):
        c.add_argument("--yes", action="store_true", help="actually write (default is a dry run)")
        c.add_argument("--workshop", help="6-byte workshop code for F198 in hex (default: reuse current)")
    cw.add_argument("--force", action="store_true", help="write even if the car's coding differs from --backup")

    fe = sub.add_parser("features", help="named cluster features per car profile")
    fs_ = fe.add_subparsers(dest="action", required=True)
    fsc = fs_.add_parser("scan", help="identify the cluster and show every known feature's state (read-only)")
    fsc.add_argument("--backup", help="stock backup to compare against")
    fsc.add_argument("--profile", choices=sorted(features.BY_KEY), help="force a profile")
    fse = fs_.add_parser("set", help="turn one feature on/off")
    fse.add_argument("feature", choices=features.all_feature_choices())
    fse.add_argument("state", choices=["on", "off"])
    fse.add_argument("--backup", required=True)
    fse.add_argument("--yes", action="store_true", help="actually write (default is a dry run)")
    fse.add_argument("--force", action="store_true", help="write even if the car's coding differs from --backup")
    fse.add_argument("--any-car", action="store_true", help="write even if the cluster doesn't match the profile")
    fse.add_argument("--workshop", help="6-byte workshop code for F198 in hex (default: reuse current)")

    mi = sub.add_parser("mileage", help="odometer value stored in each module (read-only)")
    mi.add_argument("--only", choices=sorted(MODULES), action="append")

    la = sub.add_parser("layout", help="Virtual Cockpit sport layout (Gen1 FPK)")
    ls = la.add_subparsers(dest="action", required=True)
    ls.add_parser("read", help="show the current layout (read-only)")
    lset = ls.add_parser("set", help="set standard / s / rs")
    lset.add_argument("layout", choices=sorted(layout.LAYOUTS))
    lset.add_argument("--yes", action="store_true", help="actually write (default is a dry run)")

    fw = sub.add_parser("fw", help="bridge firmware: dump / patch (never flashes the bridge)")
    fs = fw.add_subparsers(dest="action", required=True)
    fd = fs.add_parser("dump", help="save the bridge's active firmware (your restore file)")
    fd.add_argument("--baud", type=int, default=230400)
    fp = fs.add_parser("patch", help="add the cluster link to a dumped firmware (offline)")
    fp.add_argument("src")
    fp.add_argument("dst")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "deck":
        from .deck import main as deck_main
        deck_main()
    elif args.cmd == "links":
        probe.links(args.port)
    elif args.cmd == "probe":
        probe.modules(args.port, args.only)
    elif args.cmd == "listen":
        listen.run(args.port, args.rx)
    elif args.cmd == "mileage":
        features.mileage(args.port, args.only)
    elif args.cmd == "fw":
        if args.action == "dump":
            firmware.dump(args.port, args.baud)
        else:
            firmware.patch(args.src, args.dst)
    else:
        with open_client(args.port) as client:
            if args.cmd == "coding":
                if args.action == "info":
                    coding.do_info(client)
                elif args.action == "read":
                    coding.do_read(client)
                elif args.action == "restore":
                    coding.do_write(client, args.backup, None, yes=args.yes, workshop_hex=args.workshop,
                                    restore=True)
                else:
                    coding.do_write(client, args.backup, args.byte, value=args.value, bit=args.bit, on=args.on,
                                    yes=args.yes, force=args.force, workshop_hex=args.workshop)
            elif args.cmd == "features":
                if args.action == "scan":
                    features.scan(client, args.backup, args.profile)
                else:
                    features.set_feature(client, args.feature, args.state == "on", args.backup, yes=args.yes,
                                         force=args.force, any_car=args.any_car, workshop_hex=args.workshop)
            elif args.action == "read":
                layout.do_read(client)
            else:
                layout.do_set(client, args.layout, args.yes)


if __name__ == "__main__":
    main()
