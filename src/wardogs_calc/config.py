"""Portable configuration: a single config.json living next to the exe."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

CONFIG_NAME = "config.json"


def base_dir() -> Path:
    """Directory the app treats as its home.

    Frozen by PyInstaller  -> the folder holding the exe (portable install).
    Running from source     -> the repository root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    """Directory holding read-only bundled data (firing tables, glyphs)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "wardogs_calc"
    return Path(__file__).resolve().parent


@dataclass
class Config:
    # --- hotkeys (pass-through: the game still receives the key) ---------
    #: Function keys, not digits: the coordinates are read off the chat input
    #: while it holds keyboard focus, so a digit would be typed straight into
    #: the line we are trying to read and sent to the team.
    hotkey_gun: str = "f1"
    hotkey_target: str = "f2"
    hotkey_reset: str = "f3"

    # --- what we shoot with ---------------------------------------------
    #: Key into firing_tables.json: "mortar" or "sph2".
    weapon: str = "mortar"

    # --- capture ---------------------------------------------------------
    #: auto | bitblt | dxgi
    capture_backend: str = "auto"

    # --- coordinate readout ---------------------------------------------
    #: Screen rectangle [x, y, w, h] holding the chat line, in pixels. Set once
    #: by dragging a box over it; None means "fall back to chat_region_rel".
    readout_region: list[int] | None = None
    #: Rough resolution-independent guess at where the chat input sits, so the
    #: app has a chance of working with no setup at all. Fractions of the
    #: screen, [x, y, w, h]. Measured off one 2548x1440 screenshot, so treat it
    #: as a starting point rather than a truth — the reliable path is to drag
    #: the box once.
    chat_region_rel: list[float] = field(
        default_factory=lambda: [0.14, 0.33, 0.26, 0.13]
    )
    #: Readings outside the playable area are rejected. The full terrain spans
    #: 0..163.84, but no player ever stands outside these: Bakurani covers
    #: X 23.35-133.6 / Y 19.34-129.65 and Ozeti X 57.58-143.07 / Y 21.81-99.56.
    #: Their union plus a little slack turns a whole class of misreads into a
    #: clean refusal — a clipped "y9.07" read as "y0.07" lands outside and is
    #: thrown away. Widen these if a future map needs it.
    valid_x: list[float] = field(default_factory=lambda: [20.0, 147.0])
    valid_y: list[float] = field(default_factory=lambda: [16.0, 133.0])
    #: How decisively a glyph must beat the runner-up label to be trusted.
    #: The bundled bank is one real font, so the runner-up is a different
    #: digit of it and the honest gap is small; 0.05 is the measured middle.
    #: Raise it to refuse more and misread less, lower it to read more often.
    #: Renamed from ocr_min_margin, which lived on a much coarser scale — the
    #: old key is simply dropped as unknown and the new default takes over.
    match_margin: float = 0.05

    #: Metres per one unit of the X/Y readout. Both shipped maps use 100;
    #: exposed in case a future map is scaled differently.
    metres_per_unit: float = 100.0

    # --- ui ---------------------------------------------------------------
    always_on_top: bool = True
    opacity: float = 0.96
    window_pos: tuple[int, int] | None = None
    #: "dark" | "light"
    theme: str = "dark"
    #: Key into wardogs_calc.ui.theme.ACCENTS.
    accent: str = "green"
    #: Whole-UI scale; bump it on a 4K screen.
    ui_scale: float = 1.0
    #: Collapsed to just the range readout.
    compact: bool = False

    # --- diagnostics -------------------------------------------------------
    debug_dumps: bool = False

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or (base_dir() / CONFIG_NAME)
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        # Unknown keys are dropped, which is also how settings retired between
        # versions (readout_mode, calibration, readout_region_rel) fall away
        # from an existing config.json without a migration step.
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or (base_dir() / CONFIG_NAME)
        try:
            path.write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
