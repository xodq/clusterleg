"""
Launcher the deck wraps every Python command in:  python -u deck_shim.py <script.py | -m module> [args]

The deck's STOP button sends CTRL_BREAK (the only console signal Windows can aim at one child).
Python would just die on it, so this maps it onto whatever Ctrl+C would do in the script - loggers
and the passive tap get their KeyboardInterrupt and close their files cleanly.
"""

import os
import runpy
import signal
import sys


def _ctrl_break(signum, frame):
    handler = signal.getsignal(signal.SIGINT)
    if callable(handler):
        handler(signal.SIGINT, frame)
    else:
        raise KeyboardInterrupt


if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _ctrl_break)

args = sys.argv[1:]
if not args:
    sys.exit("usage: deck_shim.py <script.py | -m module> [args]")
if args[0] == "-m":
    sys.argv = [args[1]] + args[2:]
    runpy.run_module(args[1], run_name="__main__", alter_sys=True)
else:
    script = os.path.abspath(args[0])
    sys.argv = [script] + args[1:]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")
