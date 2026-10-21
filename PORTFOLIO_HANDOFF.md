> Temporary file for the portfolio build - delete once the portfolio entry is done.

# Handoff: add "clusterleg" to xodq's portfolio

**For:** the Claude session building xodq's portfolio entry for this project.
**Author:** GitHub [@xodq](https://github.com/xodq), also goes by xoddx.
**Name rule:** the repo and everything on GitHub credit **xodq** only, never the full name. Use the full name on the portfolio itself only if the user confirms it.
**Repo:** https://github.com/xodq/clusterleg

---

## One-line pitch

A command deck and instrument-cluster toolkit for Audi/VW MQB cars, built on top of [VW_Flash](https://github.com/bri3d/VW_Flash). It talks to the car through a ~$10 DIY ESP32 CAN bridge instead of a paid diagnostic app.

## Credit: this must be prominent in the portfolio entry

**Most of the credit goes to VW_Flash** by Brian Ledbetter (bri3d) and contributors: https://github.com/bri3d/VW_Flash. All the engine and transmission flashing, unlocking, logging and DTC work comes from it. clusterleg is **partial work added on top**: the cluster tools, the deck UI, the bridge firmware patch and the tests. Link VW_Flash wherever the project is described. Also credit [esp32-isotp-ble-bridge](https://github.com/bri3d/esp32-isotp-ble-bridge) and the sport-layout write-ups at [dx.dragan.ba](https://dx.dragan.ba/audi-sport/) and [mqb.pl](https://mqb.pl/en/sport-layout-activation-on-audi-virtual-cockpit/).

## What xodq built (accurate claims only)

- **Got a DIY bridge talking to the instrument cluster.** The stock ESP32 bridge firmware hard-codes four CAN links and silently drops cluster traffic. xodq dumped the firmware, found the two instructions that set an unused link's CAN IDs, retargeted them to the cluster (0x714/0x77E), and fixed the ESP32 image checksum and SHA-256 so it boots. This is a binary patch with no source rebuild. (The tool prepares the patched image but deliberately never flashes the bridge itself.)
- **Worked out the cluster's coding protocol.** The first coding writes were refused (UDS NRC 0x24). The fix was VW's "coding login" order: workshop code (F198), then date (F199), then the coding.
- **Turned on the Virtual Cockpit S sport layout** on his own 2017 A3. The setting isn't in long coding; it's a byte in cluster memory, written after a development-session login. xodq captured OBDeleven's traffic with a passive listener he wrote, matched it to the published procedure, and re-implemented it. It worked on cluster software 0045, although the guides say 0069+ is needed. That's a genuine new data point.
- **Built a dark "ops deck" GUI (tkinter)** that wraps every VW_Flash action plus the cluster tools:
  - every button builds an editable command line
  - a queue with saved playbooks
  - flashing that can start from any block, or flash a single block, to recover a hung flash
  - a real Ctrl+C stop path on Windows
  - a launch disclaimer and a confirm step before writes
- **Added safety rails:**
  - every write is a dry run until confirmed, and coding is backed up first with a restore to stock
  - per-car feature profiles that refuse a cluster that doesn't match
  - a byte known to break the fuel range estimate is locked
  - the layout write refuses unless the target bytes look right
- **Read-only odometer check:** reads the mileage stored in each module, like OBDeleven's feature, to spot tampering.
- **32 offline tests** run against a simulated cluster that behaves like the real one: it refuses coding without the login, checks the 20103 login key and stores the layout byte. They also check every deck command's flags against the real VW_Flash scripts.

## Do NOT claim

- That this is the first time anyone has done the sport layout. The method is public; what's new is doing it with a DIY bridge and own code, and the SW 0045 data point.
- That the FORUM- or CONFLICT-tagged cluster features (G-meter, engine data, oil temp, the other-Audi profile) were tested. They weren't.
- Any affiliation with Audi, VW or the VW_Flash authors.

## Stack

Python 3 · udsoncan (UDS) · pyserial · tkinter · esptool · ESP32 (Xtensa) firmware patching · ISO-TP over CAN · unittest

## Screenshots (in the repo)

- `docs/deck-flash.png`: the deck resuming a flash at block 4
- `docs/deck-cluster.png`: cluster page with the feature scan output
- `docs/deck-disclaimer.png`: launch disclaimer with credits

## Suggested portfolio blurb

> **clusterleg** — instrument-cluster tools and a command deck for Audi/VW, built on top of [VW_Flash](https://github.com/bri3d/VW_Flash) (most of the credit goes there). I binary-patched an ESP32 CAN bridge's firmware so it could reach the instrument cluster, worked out VW's coding login, and turned on the Virtual Cockpit sport layout on my own A3 by sniffing and re-implementing the diagnostic sequence. Wrapped it all in a desktop deck with queued commands, resumable block flashing, dry runs, backups and per-car safety checks, plus 32 tests against a simulated cluster. [GitHub](https://github.com/xodq/clusterleg)

## Status

- Public repo: https://github.com/xodq/clusterleg
- Launch: double-click `clusterleg-deck.bat` (installs deps on first run). There's no exe yet.
- **This file is temporary.** Delete it from the repo once the portfolio entry is done.
