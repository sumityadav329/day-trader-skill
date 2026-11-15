"""Load a script from a skill folder (the folder names have hyphens)."""

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
# tests never hit the network for the Upstox instrument list
os.environ.setdefault("TRADER_INSTRUMENTS", os.path.join(FIX, "trader-broker", "upstox-instruments-slice.json"))


def load(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("trader_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
