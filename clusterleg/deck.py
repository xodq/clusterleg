"""
clusterleg deck - stage, review and run diagnostic commands from one window.

  python -m clusterleg.deck        (or: clusterleg deck)

Every command is a plain command line you can read and edit before it runs. Commands that write
or flash ask for a yes/no with the exact line first.
"""

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import deck_catalog as cat

HOME = Path.home() / ".clusterleg"
CONFIG = HOME / "deck.json"
PLAYBOOK_DIR = HOME / "playbooks"
HISTORY = HOME / "history.log"
SHIM = str(Path(__file__).with_name("deck_shim.py"))
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
PY_ALIASES = {"python", "python3", "py", "python.exe"}

# monochrome
BG, PANEL, EDGE = "#0b0b0b", "#151515", "#2c2c2c"
FG, DIM, BRIGHT = "#cfcfcf", "#7a7a7a", "#ffffff"
INK = "#050505"
BTN, BTN_HOT = "#1f1f1f", "#bdbdbd"

UI = ("Segoe UI", 10)
UI_S = ("Segoe UI", 9)
UI_B = ("Segoe UI Semibold", 10)
UI_H = ("Segoe UI Semibold", 14)
MONO = ("Consolas", 10)  # command line + console only, so tables and hex line up

LINE_TAGS = [
    ("err", re.compile(r"Traceback|Error|refused|REFUSED|failed|FAILED|NO ANSWER|denied|BAD\b", re.I)),
    ("ok", re.compile(r"verified|accepted|ANSWERED|LINK|Done|OK\b|SUCCESS|COMPLETE", re.I)),
    ("warn", re.compile(r"NOTE|WARNING|Dry run|Do NOT|Refusing")),
]

DISCLAIMER = """this tool talks directly to your car's control units and to your bridge's firmware.

flashing, coding and memory writes can brick your engine ecu, transmission, instrument cluster or the bridge itself, leave the car unable to start, or set faults that only a dealer can clear.

none of this is made, tested or approved by audi, volkswagen or the vw_flash authors. most cluster features here were never tested on any car, and anything that worked on one car may not work on yours.

you use this entirely at your own risk. the author is not responsible for any damage, repair costs, lost data or bricked hardware, whatever the cause.

keep backups, keep a battery charger on the car, and don't flash anything you can't restore."""

# all flashing, logging and ecu work in this deck runs on vw_flash - full credit to its authors
CREDITS = [
    ("vw_flash", "https://github.com/bri3d/VW_Flash",
     "most of the credit goes here. the engine and transmission flashing, unlock, logging and dtc tools "
     "this deck runs are vw_flash, by brian ledbetter (bri3d) and contributors."),
    ("clusterleg", "https://github.com/xodq",
     "cluster long coding (read, backup, write, restore to stock), sport layout, feature profiles and this deck "
     "- partial work on top of vw_flash, "
     "by xodq (aka xoddx)."),
    ("esp32-isotp-ble-bridge", "https://github.com/bri3d/esp32-isotp-ble-bridge",
     "the bridge firmware that turns a cheap esp32 into the usb/ble interface vw_flash talks through."),
    ("sport layout method", "https://dx.dragan.ba/audi-sport/",
     "the virtual cockpit sport layout procedure (also documented at mqb.pl)."),
    ("mqb.pl", "https://mqb.pl/en/sport-layout-activation-on-audi-virtual-cockpit/",
     "sport layout write sequence and gen1 cockpit notes."),
]


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def default_vwflash_dir():
    guess = Path(PROJECT_ROOT).parent / "VW_Flash_src"
    return str(guess) if guess.exists() else os.getcwd()


def oldest(folder, pattern):
    try:
        files = sorted(Path(folder).glob(pattern), key=lambda p: p.stat().st_mtime)
    except OSError:
        return ""
    return str(files[0]) if files else ""


def resolve(tokens):
    if tokens and tokens[0].lower() in PY_ALIASES:
        return [sys.executable, "-u", SHIM] + tokens[1:]
    return tokens


def to_line(argv):
    return " ".join(f'"{a}"' if (not a or any(c.isspace() for c in a)) else a for a in argv)


def line_tag(text):
    for tag, rx in LINE_TAGS:
        if rx.search(text):
            return tag
    return None


def vwflash_gui_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq VW_Flash_GUI.exe", "/NH"],
                             capture_output=True, text=True, timeout=5).stdout
        return "VW_Flash_GUI.exe" in out
    except Exception:
        return False


class Runner:
    """One child process at a time; output is pushed onto a queue the UI drains."""

    def __init__(self):
        self.proc = None
        self.out = queue.Queue()
        self.started = 0.0

    @property
    def busy(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, argv, cwd):
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                   PYTHONPATH=os.pathsep.join(filter(None, [PROJECT_ROOT, os.environ.get("PYTHONPATH")])))
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.stdout is None:  # pythonw: no console to share, keep children windowless
                flags |= subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, creationflags=flags)
        self.started = time.time()
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc):
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1)
            if not chunk:
                break
            self.out.put(("data", chunk.decode("utf-8", "replace")))
        self.out.put(("exit", proc.wait()))

    def send(self, line):
        if self.busy:
            try:
                self.proc.stdin.write((line + "\n").encode())
                self.proc.stdin.flush()
            except OSError:
                pass

    def interrupt(self):
        if not self.busy:
            return
        try:
            if sys.platform == "win32":
                os.kill(self.proc.pid, signal.CTRL_BREAK_EVENT)
            else:
                self.proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        threading.Timer(4.0, self._hard_stop, args=(self.proc,)).start()

    @staticmethod
    def _hard_stop(proc):
        if proc.poll() is None:
            proc.kill()


class Deck(tk.Tk):
    def __init__(self, show_disclaimer=True):
        super().__init__()
        self.cfg = load_config()
        self.cfg.setdefault("port", "COM4")
        self.cfg.setdefault("vwflash_dir", default_vwflash_dir())
        self.cfg.setdefault("settings", {})
        self.cfg.setdefault("values", {})
        if not self.cfg["settings"].get("cluster_backup"):
            self.cfg["settings"]["cluster_backup"] = oldest(self.cfg["vwflash_dir"], "cluster_coding_backup_*.txt")
        self.runner = Runner()
        self.current = None
        self.vars = {}
        self.line_edited = False
        self.cr_pending = False
        self.queue_items = []
        self.queue_pos = None
        self.running_meta = None

        self.title("clusterleg")
        self.configure(bg=BG)
        self.geometry(self.cfg.get("geometry", "1440x900"))
        self.minsize(1180, 720)
        self._style()
        self._build()
        self.select_command(self.cfg.get("last_command", "links"))
        self.after(50, self._drain)
        self.after(500, self._tick)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<F5>", lambda e: self.execute())
        self.bind("<Control-Return>", lambda e: self.execute())
        self.bind("<Control-q>", lambda e: self.stage())
        self.bind("<Escape>", lambda e: self.stop())
        self.log_line("ready. workdir: " + self.cfg["vwflash_dir"], "dim")
        self.log_line("f5 or ctrl+enter runs, ctrl+q stages, esc stops", "dim")
        if show_disclaimer:
            self.after(150, self.show_disclaimer)

    # ------------------------------------------------------------------ look
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=UI,
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE, troughcolor=BG, focuscolor=EDGE,
                    selectbackground=EDGE, selectforeground=BRIGHT, insertcolor=FG)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Panel.TLabel", background=PANEL)
        s.configure("Dim.TLabel", background=PANEL, foreground=DIM, font=UI_S)
        s.configure("Head.TLabel", background=PANEL, foreground=BRIGHT, font=UI_H)
        s.configure("Sec.TLabel", background=BG, foreground=DIM, font=UI_B)
        s.configure("TEntry", fieldbackground=INK, foreground=BRIGHT, insertcolor=FG)
        s.configure("TCombobox", fieldbackground=INK, foreground=BRIGHT, arrowcolor=FG, background=PANEL)
        s.map("TCombobox", fieldbackground=[("readonly", INK)], foreground=[("readonly", BRIGHT)],
              selectbackground=[("readonly", INK)], selectforeground=[("readonly", BRIGHT)])
        s.configure("TCheckbutton", background=PANEL, foreground=FG, indicatorbackground=INK,
                    indicatorforeground=FG)
        s.map("TCheckbutton", background=[("active", PANEL)], indicatorbackground=[("selected", DIM)])
        s.configure("TButton", background=BTN, foreground=FG, padding=(10, 4), font=UI)
        s.map("TButton", background=[("active", EDGE), ("disabled", BG)], foreground=[("disabled", DIM)])
        s.configure("Main.TButton", foreground=BG, background=FG, font=UI_B)
        s.map("Main.TButton", background=[("active", BRIGHT)])
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=24,
                    font=UI, borderwidth=0)
        s.map("Treeview", background=[("selected", EDGE)], foreground=[("selected", BRIGHT)])
        s.configure("Vertical.TScrollbar", background=EDGE, troughcolor=INK, bordercolor=PANEL, arrowcolor=FG,
                    lightcolor=EDGE, darkcolor=EDGE, gripcount=0, arrowsize=12)
        s.map("Vertical.TScrollbar", background=[("active", DIM), ("!active", EDGE)])
        s.configure("TPanedwindow", background=BG)
        self.option_add("*TCombobox*Listbox.background", INK)
        self.option_add("*TCombobox*Listbox.foreground", BRIGHT)
        self.option_add("*TCombobox*Listbox.selectBackground", EDGE)
        self.option_add("*TCombobox*Listbox.font", UI)

    def _box(self, parent, title):
        outer = ttk.Frame(parent)
        ttk.Label(outer, text=title, style="Sec.TLabel").pack(anchor="w", pady=(0, 2))
        inner = ttk.Frame(outer, style="Panel.TFrame", padding=8)
        inner.pack(fill="both", expand=True)
        return outer, inner

    def _listbox(self, parent, height):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        lb = tk.Listbox(frame, bg=INK, fg=FG, selectbackground=EDGE, selectforeground=BRIGHT, font=UI_S,
                        height=height, relief="flat", highlightthickness=1, highlightbackground=EDGE,
                        highlightcolor=DIM, activestyle="none")
        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return frame, lb

    def _build(self):
        top = ttk.Frame(self, padding=(12, 8))
        top.pack(fill="x")
        ttk.Label(top, text="port", foreground=DIM).pack(side="left")
        self.port_var = tk.StringVar(value=self.cfg["port"])
        self.port_box = ttk.Combobox(top, textvariable=self.port_var, width=8)
        self.port_box.pack(side="left", padx=6)
        self.port_var.trace_add("write", lambda *a: self._port_changed())
        ttk.Button(top, text="refresh", command=self.refresh_ports).pack(side="left")
        ttk.Button(top, text="workdir", command=self.pick_workdir).pack(side="left", padx=6)
        ttk.Button(top, text="credits", command=self.show_credits).pack(side="right")
        self.status = ttk.Label(top, text="idle", foreground=DIM)
        self.status.pack(side="right", padx=12)
        self.refresh_ports()

        body = ttk.PanedWindow(self, orient="vertical")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        upper = ttk.PanedWindow(body, orient="horizontal")
        body.add(upper, weight=3)

        # command list
        o, box = self._box(upper, "commands")
        upper.add(o, weight=1)
        self.tree = ttk.Treeview(box, show="tree", selectmode="browse")
        self.tree.column("#0", width=250, minwidth=200, stretch=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("group", foreground=DIM, font=UI_B)
        for group in cat.GROUPS:
            gid = "g:" + group
            self.tree.insert("", "end", iid=gid, text=group.lower(), open=True, tags=("group",))
            for c in cat.CATALOG:
                if c.group == group:
                    self.tree.insert(gid, "end", iid=c.id, text=c.title.lower())
        self.tree.bind("<<TreeviewSelect>>", self._tree_pick)
        self.tree.bind("<Double-1>", lambda e: self.stage())

        # options form
        o, box = self._box(upper, "options")
        upper.add(o, weight=4)
        self.form_title = ttk.Label(box, text="", style="Head.TLabel")
        self.form_title.pack(anchor="w")
        self.form_desc = ttk.Label(box, text="", style="Dim.TLabel", wraplength=640, justify="left")
        self.form_desc.pack(anchor="w", pady=(2, 0))
        self.form_warn = ttk.Label(box, text="", style="Panel.TLabel", foreground=BRIGHT, wraplength=640,
                                   justify="left")
        self.form_warn.pack(anchor="w", pady=(4, 8))
        self.fields_frame = ttk.Frame(box, style="Panel.TFrame")
        self.fields_frame.pack(fill="x", anchor="n")
        # inputs keep a real width; long help text wraps instead of squeezing them out
        self.fields_frame.columnconfigure(1, weight=1, minsize=250)

        ttk.Label(box, text="command line (editable, runs exactly as shown)", style="Dim.TLabel").pack(
            anchor="w", pady=(10, 2))
        self.preview = tk.Text(box, height=3, bg=INK, fg=BRIGHT, insertbackground=FG, font=MONO, wrap="word",
                               relief="flat", highlightthickness=1, highlightbackground=EDGE, highlightcolor=DIM)
        self.preview.pack(fill="x")
        self.preview.bind("<KeyRelease>", lambda e: setattr(self, "line_edited", True))

        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="run", style="Main.TButton", command=self.execute).pack(side="right")
        ttk.Button(row, text="stage", command=self.stage).pack(side="right", padx=6)
        ttk.Button(row, text="copy", command=self.copy_line).pack(side="right")
        ttk.Button(row, text="reset", command=self.reset_form).pack(side="right", padx=6)

        # queue + history
        right = ttk.Frame(upper)
        upper.add(right, weight=2)
        o, box = self._box(right, "queue")
        o.pack(fill="both", expand=True)
        f, self.qlist = self._listbox(box, 10)
        f.pack(fill="both", expand=True)
        self.qlist.bind("<Double-1>", lambda e: self.load_staged())
        r1 = ttk.Frame(box, style="Panel.TFrame")
        r1.pack(fill="x", pady=(6, 0))
        for text, fn in (("up", lambda: self.move_staged(-1)), ("down", lambda: self.move_staged(1)),
                         ("remove", self.drop_staged), ("clear", self.clear_staged)):
            ttk.Button(r1, text=text, command=fn).pack(side="left", padx=(0, 4))
        r2 = ttk.Frame(box, style="Panel.TFrame")
        r2.pack(fill="x", pady=(4, 0))
        ttk.Button(r2, text="run all", style="Main.TButton", command=self.run_queue).pack(side="right")
        ttk.Button(r2, text="run selected", command=self.run_selected).pack(side="right", padx=4)
        r3 = ttk.Frame(box, style="Panel.TFrame")
        r3.pack(fill="x", pady=(4, 0))
        self.pb_var = tk.StringVar(value="playbooks")
        pb = ttk.Combobox(r3, textvariable=self.pb_var, values=[n.lower() for n in cat.PLAYBOOKS],
                          state="readonly", width=26)
        pb.pack(side="left")
        pb.bind("<<ComboboxSelected>>", lambda e: self.load_builtin_playbook(self.pb_var.get()))
        ttk.Button(r3, text="load", command=self.load_playbook).pack(side="right")
        ttk.Button(r3, text="save", command=self.save_playbook).pack(side="right", padx=4)

        o, box = self._box(right, "history")
        o.pack(fill="both", expand=True, pady=(6, 0))
        f, self.hlist = self._listbox(box, 6)
        f.pack(fill="both", expand=True)
        self.hlist.bind("<Double-1>", lambda e: self.load_history())
        self.history = []

        # console
        o, box = self._box(body, "output")
        body.add(o, weight=2)
        bottom = ttk.Frame(box, style="Panel.TFrame")
        bottom.pack(side="bottom", fill="x", pady=(6, 0))
        con = ttk.Frame(box, style="Panel.TFrame")
        con.pack(fill="both", expand=True)
        self.console = tk.Text(con, bg=INK, fg=FG, insertbackground=FG, font=MONO, wrap="char", relief="flat",
                               highlightthickness=0, state="disabled", height=8)
        csb = ttk.Scrollbar(con, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=csb.set)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self.console.tag_configure("ok", foreground=BRIGHT)
        self.console.tag_configure("dim", foreground=DIM)
        self.console.tag_configure("cmd", foreground=BRIGHT, font=("Consolas", 10, "bold"))
        self.console.tag_configure("err", foreground=BRIGHT, background="#2a2a2a", font=("Consolas", 10, "bold"))
        self.console.tag_configure("warn", foreground=BRIGHT, font=("Consolas", 10, "italic"))
        ttk.Label(bottom, text="input", style="Dim.TLabel").pack(side="left")
        self.stdin_var = tk.StringVar()
        e = ttk.Entry(bottom, textvariable=self.stdin_var)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda ev: self.send_stdin())
        ttk.Button(bottom, text="send", command=self.send_stdin).pack(side="left")
        ttk.Button(bottom, text="stop", style="Main.TButton", command=self.stop).pack(side="left", padx=6)
        ttk.Button(bottom, text="clear", command=self.clear_console).pack(side="left")

    def show_disclaimer(self):
        d = tk.Toplevel(self, bg=PANEL, padx=28, pady=24)
        d.title("before you start")
        d.transient(self)
        d.resizable(False, False)
        tk.Label(d, text="before you start", bg=PANEL, fg=BRIGHT, font=UI_H).pack(anchor="w")
        tk.Label(d, text=DISCLAIMER, bg=PANEL, fg=FG, font=UI, justify="left", wraplength=520).pack(
            anchor="w", pady=(10, 12))
        tk.Label(d, text="most of the credit goes to vw_flash. this deck is partial work on top of it by xodq:", bg=PANEL, fg=FG, font=UI).pack(
            anchor="w")
        self._link(d, CREDITS[0][1]).pack(anchor="w", pady=(0, 18))
        row = tk.Frame(d, bg=PANEL)
        row.pack(fill="x")
        ttk.Button(row, text="credits", command=self.show_credits).pack(side="left")

        def leave():
            d.destroy()
            self.on_close(force=True)

        ttk.Button(row, text="i understand, continue", style="Main.TButton", command=d.destroy).pack(side="right")
        ttk.Button(row, text="quit", command=leave).pack(side="right", padx=8)
        d.protocol("WM_DELETE_WINDOW", leave)
        d.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - d.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - d.winfo_height()) // 3
        d.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        d.grab_set()
        d.focus_set()

    def _link(self, parent, url):
        lbl = tk.Label(parent, text=url, bg=PANEL, fg=BRIGHT, font=("Segoe UI", 10, "underline"), cursor="hand2")
        lbl.bind("<Button-1>", lambda e: webbrowser.open(url))
        return lbl

    def show_credits(self):
        d = tk.Toplevel(self, bg=PANEL, padx=28, pady=24)
        d.title("credits")
        d.transient(self)
        d.resizable(False, False)
        tk.Label(d, text="credits", bg=PANEL, fg=BRIGHT, font=UI_H).pack(anchor="w")
        tk.Label(d, text="most of the credit goes to vw_flash and the projects below - go check them out. the cluster tools "
                 "and this deck are partial work added on top by xodq (aka xoddx).", bg=PANEL, fg=FG,
                 font=UI, justify="left", wraplength=520).pack(anchor="w", pady=(6, 14))
        for name, url, desc in CREDITS:
            tk.Label(d, text=name, bg=PANEL, fg=BRIGHT, font=UI_B).pack(anchor="w")
            tk.Label(d, text=desc, bg=PANEL, fg=FG, font=UI_S, justify="left", wraplength=520).pack(anchor="w")
            self._link(d, url).pack(anchor="w", pady=(0, 12))
        ttk.Button(d, text="close", command=d.destroy).pack(anchor="e")
        d.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - d.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - d.winfo_height()) // 3
        d.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        d.grab_set()

    # ------------------------------------------------------------------ top bar
    def refresh_ports(self):
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
        except Exception:
            ports = []
        self.port_box["values"] = ports or [self.port_var.get()]

    def _port_changed(self):
        self.cfg["port"] = self.port_var.get().strip()
        self.refresh_preview()

    def pick_workdir(self):
        d = filedialog.askdirectory(initialdir=self.cfg["vwflash_dir"], title="vw_flash source folder")
        if d:
            self.cfg["vwflash_dir"] = d
            self.log_line("workdir: " + d, "dim")

    # ------------------------------------------------------------------ form
    def _tree_pick(self, _e=None):
        sel = self.tree.selection()
        if sel and not sel[0].startswith("g:"):
            self.select_command(sel[0])

    def select_command(self, cid):
        cmd = cat.BY_ID.get(cid) or cat.CATALOG[0]
        self.current = cmd
        self.cfg["last_command"] = cmd.id
        if self.tree.selection() != (cmd.id,):
            self.tree.selection_set(cmd.id)
            self.tree.see(cmd.id)
        self.form_title.configure(text=cmd.title.lower())
        self.form_desc.configure(text=cmd.desc.lower())
        self.form_warn.configure(text=cmd.warn.lower())
        for w in self.fields_frame.winfo_children():
            w.destroy()
        self.vars = {}
        saved = self.cfg["values"].get(cmd.id, {})
        for row, f in enumerate(cmd.fields):
            val = self.cfg["settings"].get(f.setting) if f.setting else None
            if val in (None, ""):
                val = saved.get(f.key, f.default)
            self._field_row(row, f, val)
        if not cmd.fields:
            ttk.Label(self.fields_frame, text="no options", style="Dim.TLabel").grid(row=0, column=0, sticky="w")
        self.line_edited = False
        self.refresh_preview()

    def _field_row(self, row, f, val):
        ff = self.fields_frame
        ttk.Label(ff, text=f.label.lower(), style="Panel.TLabel").grid(row=row, column=0, sticky="w",
                                                                       padx=(0, 10), pady=3)
        if f.kind == "check":
            var = tk.BooleanVar(value=bool(val))
            ttk.Checkbutton(ff, variable=var).grid(row=row, column=1, sticky="w")
        elif f.kind == "choice":
            var = tk.StringVar(value=str(val))
            ttk.Combobox(ff, textvariable=var, values=f.choices, state="readonly").grid(
                row=row, column=1, sticky="ew")
        else:
            var = tk.StringVar(value="" if val is None else str(val))
            ttk.Entry(ff, textvariable=var).grid(row=row, column=1, sticky="ew")
            if f.kind in ("file", "dir"):
                ttk.Button(ff, text="browse", command=lambda f=f, v=var: self._browse(f, v)).grid(
                    row=row, column=2, padx=(4, 0))
        if f.help:
            ttk.Label(ff, text=f.help.lower(), style="Dim.TLabel", wraplength=190, justify="left").grid(
                row=row, column=3, sticky="w", padx=(10, 0))
        var.trace_add("write", lambda *a, f=f: self._field_changed(f))
        self.vars[f.key] = var

    def _browse(self, f, var):
        start = os.path.dirname(var.get()) if var.get() else self.cfg["vwflash_dir"]
        if f.kind == "dir":
            path = filedialog.askdirectory(initialdir=start)
        else:
            path = filedialog.askopenfilename(initialdir=start, filetypes=f.filetypes or [("all files", "*.*")])
        if path:
            var.set(os.path.normpath(path))

    def _field_changed(self, f):
        values = self.values()
        self.cfg["values"].setdefault(self.current.id, {})[f.key] = values[f.key]
        if f.setting:
            self.cfg["settings"][f.setting] = values[f.key]
        self.line_edited = False
        self.refresh_preview()

    def values(self):
        return {k: v.get() for k, v in self.vars.items()}

    def reset_form(self):
        self.cfg["values"].pop(self.current.id, None)
        for f in self.current.fields:
            if f.setting:
                continue
            self.vars[f.key].set(bool(f.default) if f.kind == "check" else str(f.default))
        self.line_edited = False
        self.refresh_preview()

    def refresh_preview(self):
        if not self.current or self.line_edited:
            return
        argv = cat.build_argv(self.current, self.values(), self.port_var.get().strip() or "COM4")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", argv[0] if self.current.id == "custom" else to_line(argv))

    def current_risk(self):
        # a hand-edited line can't be trusted to still be a dry run, so use the command's base level
        return self.current.risk if self.line_edited else self.current.effective_risk(self.values())

    def current_line(self):
        return self.preview.get("1.0", "end").strip()

    def copy_line(self):
        self.clipboard_clear()
        self.clipboard_append(self.current_line())
        self.log_line("copied", "dim")

    # ------------------------------------------------------------------ queue
    def _item(self):
        missing = [] if self.line_edited else cat.missing_required(self.current, self.values())
        if missing:
            messagebox.showwarning("missing", "fill in: " + ", ".join(m.lower() for m in missing))
            return None
        line = self.current_line()
        if not line:
            return None
        return {"id": self.current.id, "title": self.current.title.lower(), "risk": self.current_risk(),
                "line": line, "warn": self.current.warn.lower(), "long": self.current.long_running}

    def stage(self):
        item = self._item()
        if item:
            self.queue_items.append(item)
            self._render_queue()

    def _render_queue(self):
        self.qlist.delete(0, "end")
        for i, it in enumerate(self.queue_items, 1):
            mark = "> " if self.queue_pos is not None and i - 1 == self.queue_pos else "  "
            self.qlist.insert("end", f"{mark}{i:02d}  {it['title']}")

    def _sel(self):
        s = self.qlist.curselection()
        return s[0] if s else None

    def move_staged(self, d):
        i = self._sel()
        if i is None or self.queue_pos is not None or not 0 <= i + d < len(self.queue_items):
            return
        q = self.queue_items
        q[i], q[i + d] = q[i + d], q[i]
        self._render_queue()
        self.qlist.selection_set(i + d)

    def drop_staged(self):
        i = self._sel()
        if i is not None and self.queue_pos is None:
            del self.queue_items[i]
            self._render_queue()

    def clear_staged(self):
        if self.queue_pos is None:
            self.queue_items = []
            self._render_queue()

    def load_staged(self):
        i = self._sel()
        if i is None:
            return
        it = self.queue_items[i]
        self.select_command(it["id"])
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", it["line"])
        self.line_edited = True

    def load_builtin_playbook(self, name):
        name = next((n for n in cat.PLAYBOOKS if n.lower() == name), name)
        port = self.port_var.get().strip() or "COM4"
        for cid, overrides in cat.PLAYBOOKS.get(name, []):
            cmd = cat.BY_ID[cid]
            vals = {f.key: f.default for f in cmd.fields}
            vals.update(self.cfg["values"].get(cid, {}))
            for f in cmd.fields:
                if f.setting and self.cfg["settings"].get(f.setting):
                    vals[f.key] = self.cfg["settings"][f.setting]
            vals.update(overrides)
            argv = cat.build_argv(cmd, vals, port)
            self.queue_items.append({"id": cid, "title": cmd.title.lower(), "risk": cmd.effective_risk(vals),
                                     "line": argv[0] if cid == "custom" else to_line(argv),
                                     "warn": cmd.warn.lower(), "long": cmd.long_running})
        self._render_queue()
        self.pb_var.set("playbooks")
        self.log_line(f"queued playbook: {name.lower()}", "dim")

    def save_playbook(self):
        if not self.queue_items:
            return
        PLAYBOOK_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(initialdir=PLAYBOOK_DIR, defaultextension=".json",
                                            filetypes=[("playbook", "*.json")])
        if path:
            Path(path).write_text(json.dumps(self.queue_items, indent=2))
            self.log_line("playbook saved: " + path, "dim")

    def load_playbook(self):
        PLAYBOOK_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.askopenfilename(initialdir=PLAYBOOK_DIR, filetypes=[("playbook", "*.json")])
        if not path:
            return
        try:
            items = json.loads(Path(path).read_text())
            for it in items:
                it.setdefault("risk", cat.BY_ID.get(it.get("id"), cat.BY_ID["custom"]).risk)
                it.setdefault("title", it.get("id", "custom"))
                it.setdefault("warn", "")
                it.setdefault("long", False)
            self.queue_items += [it for it in items if it.get("line")]
        except Exception as e:
            messagebox.showerror("playbook", f"could not load: {e}")
            return
        self._render_queue()

    # ------------------------------------------------------------------ running
    def _gate(self, items):
        """Busy/port checks, then one yes/no for anything that writes or flashes."""
        if self.runner.busy:
            messagebox.showwarning("busy", "a command is still running. stop it or wait.")
            return False
        if vwflash_gui_running():
            messagebox.showerror("port in use", "vw_flash_gui.exe is running and holds the port. close it first.")
            return False
        writes = [it for it in items if it["risk"] != cat.READ]
        if not writes:
            return True
        lines = "\n\n".join(it["line"] + (f"\n({it['warn']})" if it["warn"] else "") for it in writes)
        return messagebox.askyesno("run this?", f"port {self.port_var.get()}\n\n{lines}\n\nthis writes to the car "
                                   "or the bridge. continue?", icon="warning")

    def execute(self):
        item = self._item()
        if item and self._gate([item]):
            self.queue_pos = None
            self._launch(item)

    def run_selected(self):
        i = self._sel()
        if i is not None and self._gate([self.queue_items[i]]):
            self.queue_pos = None
            self._launch(self.queue_items[i])

    def run_queue(self):
        if not self.queue_items or not self._gate(self.queue_items):
            return
        longs = [it["title"] for it in self.queue_items[:-1] if it.get("long")]
        if longs:
            self.log_line(", ".join(longs) + " run until stopped. the queue waits for each.", "warn")
        self.queue_pos = 0
        self._render_queue()
        self._launch(self.queue_items[0])

    def _launch(self, item):
        tokens = cat.split_cmdline(item["line"])
        if not tokens:
            return
        cwd = self.cfg["vwflash_dir"]
        if not os.path.isdir(cwd):
            messagebox.showerror("workdir", f"workdir not found:\n{cwd}")
            self.queue_pos = None
            return
        self.log_line("")
        self.log_line(f"> {item['line']}", "cmd")
        try:
            self.runner.start(resolve(tokens), cwd)
        except Exception as e:
            self.log_line(f"could not start: {e}", "err")
            self.queue_pos = None
            return
        self.running_meta = item
        self.cr_pending = False

    def _finished(self, code):
        item, self.running_meta = self.running_meta, None
        elapsed = time.time() - self.runner.started
        self.log_line(f"exit {code}, {elapsed:.1f}s", "ok" if code == 0 else "err")
        if item:
            stamp = time.strftime("%H:%M:%S")
            self.history.insert(0, item)
            self.hlist.insert(0, f"{stamp}  {'ok  ' if code == 0 else 'fail'}  {item['title']}")
            try:
                HOME.mkdir(parents=True, exist_ok=True)
                with HISTORY.open("a", encoding="utf-8") as h:
                    h.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\texit={code}\t{self.port_var.get()}\t{item['line']}\n")
            except OSError:
                pass
        if self.queue_pos is not None:
            if code != 0:
                self.log_line(f"queue stopped at step {self.queue_pos + 1} (exit {code})", "err")
                self.queue_pos = None
            elif self.queue_pos + 1 < len(self.queue_items):
                self.queue_pos += 1
                self._render_queue()
                self._launch(self.queue_items[self.queue_pos])
                return
            else:
                self.log_line("queue done", "ok")
                self.queue_pos = None
            self._render_queue()

    def stop(self):
        if self.runner.busy:
            self.log_line("stop sent (ctrl+c)", "warn")
            if self.queue_pos is not None:
                self.log_line("the queue stops after this step", "warn")
                self.queue_items = self.queue_items[:self.queue_pos + 1]
            self.runner.interrupt()

    def send_stdin(self):
        text = self.stdin_var.get()
        if self.runner.busy:
            self.runner.send(text)
            self.log_line(f"< {text}", "dim")
        self.stdin_var.set("")

    def load_history(self):
        s = self.hlist.curselection()
        if not s:
            return
        it = self.history[s[0]]
        self.select_command(it["id"])
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", it["line"])
        self.line_edited = True

    # ------------------------------------------------------------------ console
    def log_line(self, text, tag=None):
        self._write(text + "\n", tag)

    def clear_console(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _write(self, text, force_tag=None):
        c = self.console
        at_end = c.yview()[1] > 0.98
        c.configure(state="normal")
        for piece in re.split(r"(\r\n|\n|\r)", text):
            if piece in ("\n", "\r\n"):
                start = c.index("end-1c linestart")
                tag = force_tag or line_tag(c.get(start, "end-1c"))
                if tag:
                    c.tag_add(tag, start, "end-1c")
                c.insert("end", "\n")
                self.cr_pending = False
            elif piece == "\r":
                self.cr_pending = True  # progress bars redraw the same line
            elif piece:
                if self.cr_pending:
                    c.delete("end-1c linestart", "end-1c")
                    self.cr_pending = False
                c.insert("end", piece)
        if int(c.index("end-1c").split(".")[0]) > 6000:
            c.delete("1.0", "1000.0")
        c.configure(state="disabled")
        if at_end:
            c.see("end")

    def _drain(self):
        try:
            while True:
                kind, payload = self.runner.out.get_nowait()
                if kind == "data":
                    self._write(payload)
                else:
                    if self.console.get("end-2c", "end-1c") not in ("\n", ""):
                        self._write("\n")
                    self._finished(payload)
        except queue.Empty:
            pass
        self.after(50, self._drain)

    def _tick(self):
        if self.runner.busy:
            secs = int(time.time() - self.runner.started)
            step = f" {self.queue_pos + 1}/{len(self.queue_items)}" if self.queue_pos is not None else ""
            self.status.configure(text=f"running{step}  {secs // 60:02d}:{secs % 60:02d}", foreground=BRIGHT)
        else:
            self.status.configure(text="idle", foreground=DIM)
        self.after(500, self._tick)

    def on_close(self, force=False):
        if not force and self.runner.busy and not messagebox.askyesno("running",
                                                                      "a command is running. stop it and quit?"):
            return
        if self.runner.busy:
            self.runner.interrupt()
        self.cfg["geometry"] = self.geometry()
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps(self.cfg, indent=2))
        except OSError:
            pass
        self.destroy()


def main():
    Deck().mainloop()


if __name__ == "__main__":
    main()
