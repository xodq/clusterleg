import os
import queue
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clusterleg import deck_catalog as cat  # noqa: E402
from clusterleg.cli import build_parser  # noqa: E402
from clusterleg.deck import Runner, resolve, to_line  # noqa: E402

VWFLASH_DIR = Path(__file__).resolve().parents[2] / "VW_Flash_src"
DUMMY = {"file": r"C:\some dir\file.bin", "dir": r"C:\some dir", "text": "1", "int": "1"}


def filled(cmd, **overrides):
    vals = {f.key: f.default for f in cmd.fields}
    for f in cmd.fields:
        if f.required and vals[f.key] in ("", None):
            vals[f.key] = DUMMY.get(f.kind, "1")
    vals.update(overrides)
    return vals


BASE = {"c_write": {"bit": "3"}}  # the CLI needs --value or --bit; the user fills one in


def all_variants(cmd):
    """Defaults, plus every checkbox on, plus every choice value - so every flag gets exercised."""
    base = BASE.get(cmd.id, {})
    yield filled(cmd, **base)
    yield filled(cmd, **base, **{f.key: True for f in cmd.fields if f.kind == "check"})
    for f in cmd.fields:
        if f.kind == "choice":
            for c in f.choices:
                yield filled(cmd, **base, **{f.key: c})


class CatalogTests(unittest.TestCase):
    def test_ids_unique_and_playbooks_valid(self):
        self.assertEqual(len(cat.BY_ID), len(cat.CATALOG))
        for name, steps in cat.PLAYBOOKS.items():
            for cid, overrides in steps:
                cmd = cat.BY_ID[cid]
                for key in overrides:
                    self.assertIn(key, {f.key for f in cmd.fields}, f"{name}: {cid}.{key}")

    def test_clusterleg_commands_parse(self):
        p = build_parser()
        for cmd in cat.CATALOG:
            if cmd.head[:3] != ["python", "-m", "clusterleg"]:
                continue
            for vals in all_variants(cmd):
                argv = cat.build_argv(cmd, vals, "COM9")[3:]
                with self.subTest(cmd=cmd.id, argv=argv):
                    args = p.parse_args(argv)
                    self.assertEqual(args.port, "COM9")

    @unittest.skipUnless(VWFLASH_DIR.exists(), "VW_Flash source not next to clusterleg")
    def test_vwflash_flags_exist(self):
        helps = {}
        for cmd in cat.CATALOG:
            if cmd.head[:1] != ["python"] or cmd.head[1:2] == ["-m"] or not cmd.head[1:]:
                continue
            script = cmd.head[1]
            if script not in helps:
                r = subprocess.run([sys.executable, script, "--help"], cwd=VWFLASH_DIR, capture_output=True,
                                   text=True, timeout=120)
                self.assertEqual(r.returncode, 0, f"{script} --help failed:\n{r.stderr[-800:]}")
                helps[script] = r.stdout
            for vals in all_variants(cmd):
                for tok in cat.build_argv(cmd, vals, "COM9")[2:]:
                    if tok.startswith("--"):
                        with self.subTest(cmd=cmd.id, flag=tok):
                            self.assertIn(tok, helps[script])

    def test_dry_runs_are_read_only(self):
        self.assertEqual(cat.BY_ID["flash_bin"].effective_risk({"dry": True}), cat.READ)
        self.assertEqual(cat.BY_ID["flash_bin"].effective_risk({"dry": False}), cat.FLASH)
        self.assertEqual(cat.BY_ID["c_layout"].effective_risk({"yes": False}), cat.READ)
        self.assertEqual(cat.BY_ID["c_layout"].effective_risk({"yes": True}), cat.WRITE)
        self.assertEqual(cat.BY_ID["vwflash"].effective_risk({"action": "get_ecu_info"}), cat.READ)
        self.assertEqual(cat.BY_ID["vwflash"].effective_risk({"action": "flash_unlock"}), cat.FLASH)

    def test_example_lines_match_what_was_typed(self):
        line = to_line(cat.build_argv(cat.BY_ID["c_layout"], {"layout": "s", "yes": True}, "COM4"))
        self.assertEqual(line, "python -m clusterleg --port COM4 layout set s --yes")
        line = to_line(cat.build_argv(cat.BY_ID["ecuinfo"], filled(cat.BY_ID["ecuinfo"]), "COM4"))
        self.assertEqual(line, "python VW_Flash.py --action get_ecu_info --interface USBISOTP --usb_name COM4")

    def test_cmdline_roundtrip_with_spaces(self):
        argv = ["python", "flash_cal.py", "--bin", r"C:\My Tunes\stage 1.bin", "--dry-run"]
        self.assertEqual(cat.split_cmdline(to_line(argv)), argv)


class RunnerTests(unittest.TestCase):
    def _collect(self, runner, until, timeout=15):
        out, deadline = "", time.time() + timeout
        while time.time() < deadline:
            try:
                kind, payload = runner.out.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "exit":
                return out, payload
            out += payload
            if until and until in out:
                return out, None
        self.fail(f"timed out; output so far:\n{out}")

    def test_runs_module_through_shim(self):
        r = Runner()
        r.start(resolve(["python", "-m", "clusterleg", "--version"]), os.getcwd())
        out, code = self._collect(r, None)
        self.assertEqual(code, 0)
        self.assertIn("clusterleg", out)

    @unittest.skipUnless(sys.platform == "win32", "CTRL_BREAK path is Windows-only")
    def test_stop_gives_script_a_clean_ctrl_c(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "loop.py"
            script.write_text(
                "import time\n"
                "try:\n"
                "    print('ready', flush=True)\n"
                "    while True: time.sleep(0.1)\n"
                "except KeyboardInterrupt:\n"
                "    print('clean exit', flush=True)\n")
            r = Runner()
            r.start(resolve(["python", str(script)]), d)
            self._collect(r, "ready")
            r.interrupt()
            out, code = self._collect(r, None)
            self.assertIn("clean exit", out)


if __name__ == "__main__":
    unittest.main()
