"""Entry point for both `python wardogs_calc.py` and the PyInstaller build."""

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wardogs_calc.ui import run  # noqa: E402

if __name__ == "__main__":
    run()
