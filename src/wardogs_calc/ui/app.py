"""Main window: a frameless, always-on-top overlay with a settings page.

The window is frameless on purpose — it sits over a game, and the stock
Windows chrome is both ugly and taller than the content it would frame.  The
title bar here does the dragging, collapsing and closing itself.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from .. import __version__
from ..ballistics import Point, Weapon, load_weapons, solve
from ..capture import Region, ScreenCapture
from ..config import Config, base_dir, resource_dir
from ..diagnostics import log, log_path
from ..hotkeys import (
    HotkeyError,
    HotkeyListener,
    edits_text,
    is_elevated,
    parse_hotkey,
    pretty_hotkey,
)
from ..reader import CoordinateReader
from ..terrain import available_maps, load as load_terrain
from ..vision.ocr import recognise_text
from .region_select import select_region
from .theme import ACCENTS, Theme
from .trainer import train_from_crop
from .widgets import (
    Card,
    FlatButton,
    HotkeyRecorder,
    IconButton,
    Row,
    ScrollArea,
    Segmented,
    Slider,
    SwatchRow,
    Toggle,
    round_rect,
)

BACKENDS = [("auto", "Auto"), ("bitblt", "BitBlt"), ("dxgi", "DXGI")]
#: Maps that ship with a height grid. Empty if the grids are missing, in
#: which case the Terrain section is left out and every solution is level.
MAPS = list(available_maps().items())
THEMES = [("dark", "Dark"), ("light", "Light")]

#: Label -> minimum match margin. Stricter refuses more, misreads less. The
#: bank holds one real font, so the runner-up is a different digit of it and
#: the honest gap is small — these are far below a cross-font scale.
STRICTNESS = [("strict", "Strict"), ("normal", "Normal"), ("loose", "Loose")]
STRICTNESS_VALUES = {"strict": 0.10, "normal": 0.05, "loose": 0.02}

WINDOW_WIDTH = 400


class App(tk.Tk):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.cfg = config
        self.weapons: dict[str, Weapon] = load_weapons(resource_dir() / "firing_tables.json")
        if config.weapon not in self.weapons:
            config.weapon = next(iter(self.weapons))

        self.theme = Theme(
            dark=config.theme != "light",
            accent_key=config.accent if config.accent in ACCENTS else "green",
            scale=max(0.8, min(1.6, config.ui_scale)),
        )
        self.reader = CoordinateReader(config)
        self.events: queue.Queue[Callable[[], None]] = queue.Queue()
        self.listener = HotkeyListener()

        self.gun: Point | None = None
        self.target: Point | None = None
        self.page = "main"
        self._status: tuple[str, str] = ("", "muted")
        self._drag_origin: tuple[int, int] | None = None

        self.overrideredirect(True)
        self.configure(bg=self.theme.bg)
        self._apply_window_prefs()

        self.root = tk.Frame(self, bg=self.theme.bg)
        self.root.pack(fill="both", expand=True)
        self._build()

        self._rebind_hotkeys(initial=True)
        self.listener.start()
        self.after(40, self._pump)
        self.bind("<Escape>", lambda _e: self._cancel_recording())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._announce_readiness()
        self._log_startup()

    def _log_startup(self) -> None:
        from ..capture import screen_size

        log("=" * 60)
        log(
            f"start: screen {screen_size()}, "
            f"{'administrator' if is_elevated() else 'standard user'}, "
            f"config {base_dir() / 'config.json'}"
        )
        log(
            f"  hotkeys {self.cfg.hotkey_gun} / {self.cfg.hotkey_target} / "
            f"{self.cfg.hotkey_reset}, weapon {self.cfg.weapon}"
        )
        log(
            f"  font: {len(self.reader.glyphs)} templates in "
            f"{list(self.reader.glyphs.groups)}, "
            f"characters {''.join(sorted(self.reader.glyphs.labels))}, "
            f"margin {self.reader.glyphs.min_margin}"
        )
        region = self.reader._region()
        log(
            f"  area {region.as_tuple() if region else None}, "
            f"saved {self.cfg.readout_region}, "
            f"default {self.cfg.chat_region_rel}"
        )
        if not self.cfg.terrain_correction:
            log("  height correction off, level-ground tables only")
        else:
            grid = load_terrain(self._terrain_key())
            if grid is None:
                log(f"  height correction on but {self._terrain_key()} has no grid")
            else:
                log(
                    f"  height correction on, {grid.label} "
                    f"{grid.grid.shape[1]}x{grid.grid.shape[0]} at "
                    f"{grid.step * self.cfg.metres_per_unit:.0f} m, "
                    f"X {grid.x0:.2f}..{grid.x1:.2f} Y {grid.y0:.2f}..{grid.y1:.2f}"
                )

    # ==================================================================
    # construction
    # ==================================================================
    def _build(self) -> None:
        t = self.theme
        for child in self.root.winfo_children():
            child.destroy()
        # The recorders live on the settings page only, and the line above has
        # just destroyed them. Leaving them listed made Esc on the main page --
        # which cancels any recording in progress -- reach for dead widgets.
        self.recorders: dict[str, HotkeyRecorder] = {}
        self.configure(bg=t.bg)
        self.root.configure(bg=t.bg)

        # A hairline frame stands in for the window border we gave up.
        self.shell = tk.Frame(
            self.root, bg=t.bg, highlightthickness=1,
            highlightbackground=t.border, highlightcolor=t.border,
        )
        self.shell.pack(fill="both", expand=True)

        self._build_titlebar()
        self.content = tk.Frame(self.shell, bg=t.bg)
        self.content.pack(fill="both", expand=True)

        if self.page == "settings":
            self._build_settings()
        else:
            self._build_main()

        self._fit()

    def _fit(self) -> None:
        """Resize to the content.

        Needed after _refresh() as well as after _build(): firing solutions
        appear only once both points are set, and a frameless window has no
        chrome to grow on its own.
        """
        self.update_idletasks()
        self.geometry(f"{self.theme.px(WINDOW_WIDTH)}x{self.winfo_reqheight()}")

    # -- title bar ------------------------------------------------------
    def _build_titlebar(self) -> None:
        t = self.theme
        bar = tk.Frame(self.shell, bg=t.surface, height=t.px(38))
        bar.pack(fill="x")
        bar.pack_propagate(False)

        mark = tk.Canvas(
            bar, width=t.px(26), height=t.px(38), bg=t.surface, highlightthickness=0, bd=0
        )
        mark.pack(side="left", padx=(t.px(10), 0))
        cy = t.px(19)
        mark.create_oval(t.px(6), cy - t.px(5), t.px(16), cy + t.px(5),
                         fill=t.accent, outline=t.accent)

        title = tk.Label(
            bar, text="WARDOGS ARTILLERY", bg=t.surface, fg=t.text,
            font=t.sans(9, "bold"),
        )
        title.pack(side="left")

        IconButton(bar, t, "✕", self.close, danger=True).pack(side="right", padx=(0, t.px(8)))
        self._compact_button = IconButton(
            bar, t, "▴" if not self.cfg.compact else "▾", self._toggle_compact
        )
        self._compact_button.pack(side="right", padx=t.px(1))
        IconButton(
            bar, t, "⚙", self._toggle_settings, font_size=12
        ).pack(side="right", padx=t.px(1))

        for widget in (bar, mark, title):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)

    def _drag_start(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_move(self, event: tk.Event) -> None:
        if not self._drag_origin:
            return
        dx, dy = self._drag_origin
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _drag_end(self, _event: tk.Event) -> None:
        self._drag_origin = None
        self.cfg.window_pos = (self.winfo_x(), self.winfo_y())
        self.cfg.save()

    # -- main page ------------------------------------------------------
    def _build_main(self) -> None:
        t = self.theme
        pad = t.px(10)

        # Elevation leads: it is the number the player dials on the weapon.
        # Range and azimuth are what you check, not what you enter.
        hero = Card(self.content, t, padding=12)
        hero.pack(fill="x", padx=pad, pady=(pad, t.px(6)))
        head = tk.Frame(hero.body, bg=t.surface)
        head.pack(fill="x")
        tk.Label(head, text="ELEVATION", bg=t.surface, fg=t.muted,
                 font=t.sans(8, "bold")).pack(side="left")
        self.weapon_chip = Segmented(
            head, t,
            [(key, w.chip) for key, w in self.weapons.items()],
            self.cfg.weapon, self._on_weapon, height=22, font_size=8, width=130,
        )
        self.weapon_chip.pack(side="right")

        # Rebuilt on every refresh: the mortar has one arc, the SPH-2 has two.
        self.solution_frame = tk.Frame(hero.body, bg=t.surface)
        self.solution_frame.pack(fill="x", pady=(t.px(4), 0))
        self.meta_label = tk.Label(
            hero.body, text="", bg=t.surface, fg=t.muted, font=t.mono(10),
            anchor="w", justify="left",
        )
        self.meta_label.pack(fill="x", pady=(t.px(6), 0))

        if self.cfg.compact:
            self._refresh()
            return

        points = Card(self.content, t, padding=10)
        points.pack(fill="x", padx=pad, pady=t.px(6))
        self.gun_row = self._point_row(points.body, "GUN", self.cfg.hotkey_gun)
        tk.Frame(points.body, bg=t.border, height=1).pack(fill="x", pady=t.px(7))
        self.target_row = self._point_row(points.body, "TARGET", self.cfg.hotkey_target)

        self.status_label = tk.Label(
            self.content, text="", bg=t.bg, fg=t.muted, font=t.sans(8),
            justify="left", anchor="w", wraplength=t.px(WINDOW_WIDTH - 34),
        )
        self.status_label.pack(fill="x", padx=pad + t.px(2), pady=(t.px(6), t.px(4)))

        actions = tk.Frame(self.content, bg=t.bg)
        actions.pack(fill="x", padx=pad, pady=(0, pad))
        for text, command in (
            ("Area", self._pick_region),
            ("Train", self._train),
            ("Reset", self._reset),
        ):
            FlatButton(actions, t, text, command, width=118, height=30).pack(
                side="left", expand=True, padx=t.px(2)
            )

        self._apply_status()
        self._refresh()

    def _point_row(self, parent: tk.Frame, title: str, hotkey: str) -> dict:
        t = self.theme
        row = tk.Frame(parent, bg=t.surface)
        row.pack(fill="x")
        left = tk.Frame(row, bg=t.surface)
        left.pack(side="left")
        dot = tk.Canvas(left, width=t.px(8), height=t.px(14), bg=t.surface,
                        highlightthickness=0, bd=0)
        dot.pack(side="left", padx=(0, t.px(7)))
        title_label = tk.Label(left, text=title, bg=t.surface, fg=t.muted, font=t.sans(8, "bold"))
        title_label.pack(side="left")
        chip = tk.Canvas(left, height=t.px(16), bg=t.surface, highlightthickness=0, bd=0)
        chip.pack(side="left", padx=(t.px(6), 0))
        value = tk.Label(row, text="not set", bg=t.surface, fg=t.faint, font=t.mono(10))
        value.pack(side="right")
        entry = {"dot": dot, "chip": chip, "value": value, "title": title_label}
        self._paint_key_chip(chip, hotkey)
        self._paint_dot(dot, False)
        return entry

    def _paint_key_chip(self, chip: tk.Canvas, hotkey: str) -> None:
        t = self.theme
        text = pretty_hotkey(hotkey)
        from tkinter import font as tkfont

        width = tkfont.Font(font=t.mono(8, "bold")).measure(text) + t.px(12)
        chip.configure(width=width)
        chip.delete("all")
        h = t.px(16)
        round_rect(chip, 0, 1, width, h - 1, t.px(4), fill=t.surface_hi, outline=t.border)
        chip.create_text(width / 2, h / 2, text=text, fill=t.muted, font=t.mono(8, "bold"))

    def _paint_dot(self, dot: tk.Canvas, filled: bool) -> None:
        t = self.theme
        dot.delete("all")
        r = t.px(4)
        cx, cy = t.px(4), t.px(7)
        colour = t.accent if filled else t.faint
        dot.create_oval(cx - r, cy - r, cx + r, cy + r,
                        fill=colour if filled else "", outline=colour, width=t.px(2))

    # -- settings page --------------------------------------------------
    def _build_settings(self) -> None:
        t = self.theme
        pad = t.px(10)
        area = ScrollArea(self.content, t, height=440)
        area.pack(fill="both", expand=True, padx=pad, pady=(pad, 0))
        body = area.body
        body.configure(bg=t.bg)

        # --- hotkeys ---------------------------------------------------
        card = self._section(
            body, "Hotkeys", "Click a button, then press the key or mouse button you want"
        )
        self.recorders: dict[str, HotkeyRecorder] = {}
        rows = (
            ("hotkey_gun", "Gun position", None),
            ("hotkey_target", "Target position", None),
            ("hotkey_reset", "Clear both points", None),
        )
        for i, (key, label, hint) in enumerate(rows):
            row = Row(card, t, label, hint)
            row.pack(fill="x", pady=t.px(4) if i else 0)
            recorder = HotkeyRecorder(
                row, t, getattr(self.cfg, key),
                on_record=lambda spec, k=key: self._on_hotkey_recorded(k, spec),
                start_capture=self._start_capture,
                cancel_capture=self.listener.cancel_capture,
                pretty=pretty_hotkey,
            )
            recorder.pack(side="right")
            self.recorders[key] = recorder
        tk.Label(
            card, text="Mouse buttons work too: side, middle, right, wheel. "
                       "For a combination hold Ctrl / Shift / Alt / Win and press. "
                       "Esc cancels. The left button is reserved for this window.",
            bg=t.surface, fg=t.faint, font=t.sans(8), justify="left", anchor="w",
            wraplength=t.px(WINDOW_WIDTH - 56),
        ).pack(fill="x", pady=(t.px(8), 0))

        # --- coordinates -----------------------------------------------
        card = self._section(
            body, "Coordinates", "Reads the “x.., y..” line out of the chat input"
        )
        row = Row(card, t, "Strictness", "Stricter reads less often and misreads less")
        row.pack(fill="x")
        Segmented(
            row, t, STRICTNESS, self._strictness_key(), self._on_strictness,
            width=180, height=28,
        ).pack(side="right")

        buttons = tk.Frame(card, bg=t.surface)
        buttons.pack(fill="x", pady=(t.px(10), 0))
        for text, command in (
            ("Set area", self._pick_region),
            ("Train font", self._train),
        ):
            FlatButton(buttons, t, text, command, width=170, height=28, font_size=8).pack(
                side="left", expand=True, padx=t.px(2)
            )

        # --- terrain ----------------------------------------------------
        # Left out entirely when no height grid shipped: a switch that cannot
        # do anything is worse than no switch.
        if MAPS:
            card = self._section(body, "Terrain")
            row = Row(
                card, t, "Height correction",
                "Corrects the elevation for the climb or drop to the target, "
                "which the game never shows you",
            )
            row.pack(fill="x")
            Toggle(row, t, self.cfg.terrain_correction, self._on_terrain_toggle).pack(
                side="right"
            )
            row = Row(card, t, "Map", "Both maps use the same coordinates, so a "
                                      "reading cannot say which one it is")
            row.pack(fill="x", pady=(t.px(10), 0))
            Segmented(
                row, t, MAPS, self._terrain_key(), self._on_terrain_map,
                width=200, height=28, font_size=8,
            ).pack(side="right")

        # --- capture ----------------------------------------------------
        card = self._section(body, "Screen capture")
        row = Row(card, t, "Backend", "DXGI is only needed for exclusive fullscreen")
        row.pack(fill="x")
        Segmented(row, t, BACKENDS, self.cfg.capture_backend, self._on_backend,
                  width=150, height=28).pack(side="right")

        # --- appearance -------------------------------------------------
        card = self._section(body, "Appearance")
        row = Row(card, t, "Theme")
        row.pack(fill="x")
        Segmented(row, t, THEMES, self.cfg.theme, self._on_theme,
                  width=130, height=28).pack(side="right")

        row = Row(card, t, "Accent")
        row.pack(fill="x", pady=(t.px(10), 0))
        SwatchRow(row, t, ACCENTS, self.theme.accent_key, self._on_accent).pack(side="right")

        row = Row(card, t, "Opacity")
        row.pack(fill="x", pady=(t.px(10), 0))
        self.opacity_value = tk.Label(
            row, text=f"{int(self.cfg.opacity * 100)}%", bg=t.surface, fg=t.muted,
            font=t.mono(8), width=5, anchor="e",
        )
        self.opacity_value.pack(side="right", padx=(t.px(6), 0))
        Slider(row, t, self.cfg.opacity, self._on_opacity, minimum=0.4, maximum=1.0,
               width=130).pack(side="right")

        row = Row(card, t, "Interface scale")
        row.pack(fill="x", pady=(t.px(10), 0))
        self.scale_value = tk.Label(
            row, text=f"{self.theme.scale:.2f}×", bg=t.surface, fg=t.muted,
            font=t.mono(8), width=5, anchor="e",
        )
        self.scale_value.pack(side="right", padx=(t.px(6), 0))
        Slider(row, t, self.theme.scale, self._on_scale, minimum=0.8, maximum=1.6,
               width=130).pack(side="right")

        row = Row(card, t, "Always on top")
        row.pack(fill="x", pady=(t.px(10), 0))
        Toggle(row, t, self.cfg.always_on_top, self._on_topmost).pack(side="right")

        # --- diagnostics --------------------------------------------------
        card = self._section(body, "Diagnostics")
        row = Row(card, t, "Save snapshots", "Writes the cropped area into debug/")
        row.pack(fill="x")
        Toggle(row, t, self.cfg.debug_dumps, self._on_debug).pack(side="right")

        tk.Label(
            card,
            text=f"The log is always written: {log_path()}\nIt shows which area "
                 f"was read, what was recognised and how long it took.",
            bg=t.surface, fg=t.faint, font=t.sans(8), anchor="w", justify="left",
            wraplength=t.px(WINDOW_WIDTH - 56),
        ).pack(fill="x", pady=(t.px(8), 0))

        info = tk.Frame(card, bg=t.surface)
        info.pack(fill="x", pady=(t.px(10), 0))
        tk.Label(
            info,
            text=f"Version {__version__}    "
                 f"Templates: {len(self.reader.glyphs)}    "
                 f"Capture: {self.reader.capture.active_backend}    "
                 f"Rights: {'administrator' if is_elevated() else 'standard'}",
            bg=t.surface, fg=t.faint, font=t.mono(8), anchor="w",
        ).pack(fill="x")
        if not is_elevated():
            tk.Label(
                info,
                text="If the hotkeys do not respond in game, run this app as "
                     "administrator: a hook gets no input from an elevated game.",
                bg=t.surface, fg=t.faint, font=t.sans(8), anchor="w", justify="left",
                wraplength=t.px(WINDOW_WIDTH - 56),
            ).pack(fill="x", pady=(t.px(4), 0))
        tk.Label(
            info,
            text="Elevation figures are community measurements, never published "
                 "officially — worth re-checking after a game patch.",
            bg=t.surface, fg=t.faint, font=t.sans(8), anchor="w", justify="left",
            wraplength=t.px(WINDOW_WIDTH - 56),
        ).pack(fill="x", pady=(t.px(4), 0))
        FlatButton(card, t, "Reset settings", self._reset_settings,
                   kind="danger", width=160, height=28, font_size=8).pack(
            anchor="w", pady=(t.px(10), 0)
        )

        FlatButton(self.content, t, "Done", self._toggle_settings, kind="primary",
                   height=32).pack(fill="x", padx=pad, pady=pad)

    def _section(self, parent: tk.Frame, title: str, hint: str | None = None) -> tk.Frame:
        t = self.theme
        tk.Label(
            parent, text=title.upper(), bg=t.bg, fg=t.muted, font=t.sans(8, "bold"), anchor="w",
        ).pack(fill="x", pady=(t.px(8), t.px(5)))
        if hint:
            tk.Label(
                parent, text=hint, bg=t.bg, fg=t.faint, font=t.sans(8), anchor="w",
                justify="left", wraplength=t.px(WINDOW_WIDTH - 40),
            ).pack(fill="x", pady=(0, t.px(5)))
        card = Card(parent, t, padding=12)
        card.pack(fill="x")
        return card.body

    # ==================================================================
    # hotkeys
    # ==================================================================
    def _start_capture(self, callback: Callable[[str | None], None]) -> None:
        """Route the listener's capture result back onto the Tk thread."""
        self.listener.capture_next(lambda spec: self.events.put(lambda: callback(spec)))

    def _cancel_recording(self) -> None:
        self.listener.cancel_capture()
        for recorder in getattr(self, "recorders", {}).values():
            recorder._disarm()
            recorder.repaint()

    def _on_hotkey_recorded(self, key: str, spec: str) -> None:
        clash = next(
            (
                other
                for other in ("hotkey_gun", "hotkey_target", "hotkey_reset")
                if other != key and getattr(self.cfg, other) == spec
            ),
            None,
        )
        if clash:
            self.recorders[key].set_spec(getattr(self.cfg, key))
            self._set_status(f"{pretty_hotkey(spec)} is already bound elsewhere", "danger")
            return
        setattr(self.cfg, key, spec)
        self._rebind_hotkeys()
        if edits_text(spec):
            self._set_status(
                f"Bound {pretty_hotkey(spec)} — but this key types into the chat "
                f"input, which is the very line being read. It would corrupt the "
                f"coordinates. Pick a function key instead.",
                "danger",
            )
            return
        self._set_status(f"Bound {pretty_hotkey(spec)}", "accent")

    def _rebind_hotkeys(self, initial: bool = False) -> None:
        specs = {
            "hotkey_gun": self.cfg.hotkey_gun,
            "hotkey_target": self.cfg.hotkey_target,
            "hotkey_reset": self.cfg.hotkey_reset,
        }
        try:
            for spec in specs.values():
                parse_hotkey(spec)
        except HotkeyError as exc:
            self._set_status(f"Hotkey rejected: {exc}", "danger")
            return
        self.listener.clear()
        self.listener.bind(
            specs["hotkey_gun"], lambda: self.events.put(lambda: self._capture("gun"))
        )
        self.listener.bind(
            specs["hotkey_target"], lambda: self.events.put(lambda: self._capture("target"))
        )
        self.listener.bind(specs["hotkey_reset"], lambda: self.events.put(self._reset))
        if not initial:
            self.cfg.save()
            if self.page == "main" and not self.cfg.compact:
                self._paint_key_chip(self.gun_row["chip"], specs["hotkey_gun"])
                self._paint_key_chip(self.target_row["chip"], specs["hotkey_target"])

    def _pump(self) -> None:
        """Drain hotkey and capture callbacks onto the Tk thread."""
        try:
            while True:
                self.events.get_nowait()()
        except queue.Empty:
            pass
        except Exception as exc:  # a bad handler must not stop the loop
            self._set_status(f"Error: {exc}", "danger")
        self.after(40, self._pump)

    # ==================================================================
    # actions
    # ==================================================================
    def _capture(self, slot: str) -> None:
        log(f"hotkey: {slot}")
        result = self.reader.read()
        if not result.ok:
            self._set_status(
                (result.error or "no coordinates found") + " — " + self._read_hint(),
                "warn",
            )
            return
        reading = result.reading
        point = reading.point

        # Every press overwrites its slot, so the hotkey can be pressed again
        # after marking a new point without resetting anything first.
        other = self.target if slot == "gun" else self.gun
        if slot == "gun":
            self.gun = point
        else:
            self.target = point

        name = "Gun" if slot == "gun" else "Target"
        # The chat line stays put until the message is sent, so a press without
        # a fresh mark reads the same coordinates again. The point is still
        # stored — it is what the screen says — but a range of zero deserves an
        # explanation rather than a confident-looking dash.
        if other is not None and (other.x, other.y) == (point.x, point.y):
            self._set_status(
                f"{name}: {point} — same as the other point. The chat line has not "
                f"changed: mark a new point in game and press again.",
                "warn",
            )
        else:
            self._set_status(f"{name}: {point}  · {result.elapsed_ms:.0f} ms", "accent")
        self._refresh()

    def _read_hint(self) -> str:
        """What to try next when a read comes back empty."""
        if self.reader.region_ignored:
            return (
                "the saved area is far too large for one line of text, "
                "set it again with Area"
            )
        if not self.reader.trained:
            return "press Train"
        if self._holds_readout():
            return (
                "the line is visible but the font does not read cleanly: "
                "press Train and label the characters, once"
            )
        return "mark coordinates in game; the line has to stay in the chat"

    def _reset(self) -> None:
        self.gun = None
        self.target = None
        self._set_status("Points cleared", "muted")
        self._refresh()

    def _pick_region(self) -> None:
        was_topmost = bool(self.attributes("-topmost"))
        self.attributes("-topmost", False)
        self.withdraw()
        self.update_idletasks()
        try:
            region = select_region(
                self,
                "Mark coordinates in game so the “x.., y..” line sits in "
                "the chat,\nthen drag a box around it. Esc cancels.",
            )
        finally:
            self.deiconify()
            self.attributes("-topmost", was_topmost)
        if region is None:
            return

        previous = self.cfg.readout_region
        self.cfg.readout_region = list(region)
        log(f"area selected {tuple(region)}, verifying")
        result = self.reader.read()
        if result.ok:
            self.cfg.save()
            log("  area accepted and saved")
            self._set_status(f"Area set, reading: {result.reading.point}", "accent")
            return

        # A correct box can still fail to parse: on a font the bundled bank has
        # never seen, single glyphs come back as "?" and one "?" invalidates the
        # whole number. Refusing the box then is the worst possible answer —
        # the box is right, and training the font reads through this very box,
        # so it has to be saved first.
        if self._holds_readout():
            self.cfg.save()
            log("  area saved: line visible, font does not read cleanly")
            self._set_status(
                "Area saved — the line is visible in it, but the font does not read "
                "cleanly. Press Train and label the characters: it is a one-off, "
                "after which reading is exact.",
                "warn",
            )
            return

        self.cfg.readout_region = previous
        log(f"  area rejected, restored previous {previous}")
        self._set_status(
            f"No text at all in the selected {region[2]}×{region[3]} px — the area "
            f"was not saved. Check that the “x.., y..” line is visible in "
            f"the chat and select it again.",
            "warn",
        )

    def _holds_readout(self) -> bool:
        """Whether the crop plainly contains the readout, parsed or not.

        A label letter next to a couple of digits is enough: that cannot be
        terrain, so the box is pointing at the right place.
        """
        crop = self._grab_region_image(quiet=True)
        if crop is None:
            return False
        for text, _confidence in recognise_text(crop, self.reader.glyphs):
            lowered = text.lower()
            digits = sum(character.isdigit() for character in text)
            if digits >= 2 and ("x" in lowered or "y" in lowered):
                log(f"  area shows the line {text!r}")
                return True
        return False

    def _grab_region_image(self, quiet: bool = False):
        region = self.reader._region()
        if region is None:
            if not quiet:
                messagebox.showinfo(
                    "Area needed",
                    "Select the coordinate line with the Area button.",
                    parent=self,
                )
            return None
        try:
            return self.reader.capture.grab(Region(*region.as_tuple()))
        except Exception as exc:
            if not quiet:
                messagebox.showerror("Capture failed", str(exc), parent=self)
            return None

    def _train(self) -> None:
        crop = self._grab_region_image()
        if crop is None:
            return
        if train_from_crop(self, crop, base_dir(), self.theme):
            self.reader.reload_glyphs()
            self._set_status(f"Font trained: {len(self.reader.glyphs)} templates", "accent")

    def _reset_settings(self) -> None:
        if not messagebox.askyesno(
            "Reset settings",
            "Restore every setting to its default?\n"
            "The trained font (glyphs.json) is left in place.",
            parent=self,
        ):
            return
        fresh = Config()
        fresh.window_pos = self.cfg.window_pos
        self.cfg = fresh
        self.cfg.save()
        self.reader.config = self.cfg
        self.theme = Theme(dark=True, accent_key="green", scale=1.0)
        self._apply_window_prefs()
        self._rebind_hotkeys()
        self._build()
        self._set_status("Settings reset", "muted")

    # ==================================================================
    # settings callbacks
    # ==================================================================
    def _on_weapon(self, key: str) -> None:
        self.cfg.weapon = key
        self.cfg.save()
        self._refresh()

    def _terrain_key(self) -> str:
        if MAPS and self.cfg.terrain_map not in dict(MAPS):
            self.cfg.terrain_map = MAPS[0][0]
        return self.cfg.terrain_map

    def _on_terrain_map(self, key: str) -> None:
        self.cfg.terrain_map = key
        self.cfg.save()
        self._refresh()

    def _on_terrain_toggle(self, value: bool) -> None:
        self.cfg.terrain_correction = value
        self.cfg.save()
        self._set_status(
            "Height correction on" if value
            else "Height correction off — level-ground tables only",
            "muted",
        )
        self._refresh()

    def _strictness_key(self) -> str:
        current = self.cfg.match_margin
        return min(STRICTNESS_VALUES, key=lambda k: abs(STRICTNESS_VALUES[k] - current))

    def _on_strictness(self, key: str) -> None:
        self.reader.set_strictness(STRICTNESS_VALUES[key])
        self.cfg.save()
        self._set_status(
            "Loose: reads more often but can misread — Train the font instead"
            if key == "loose"
            else "Strictness changed",
            "warn" if key == "loose" else "muted",
        )

    def _on_backend(self, key: str) -> None:
        self.cfg.capture_backend = key
        self.cfg.save()
        self.reader.capture.close()
        self.reader.capture = ScreenCapture(key)
        self._set_status("Capture backend switched", "muted")

    def _on_theme(self, key: str) -> None:
        self.cfg.theme = key
        self.cfg.save()
        self.theme = Theme(
            dark=key != "light", accent_key=self.theme.accent_key, scale=self.theme.scale
        )
        self._build()

    def _on_accent(self, key: str) -> None:
        self.cfg.accent = key
        self.cfg.save()
        self.theme = Theme(dark=self.theme.dark, accent_key=key, scale=self.theme.scale)
        self._build()

    def _on_opacity(self, value: float) -> None:
        self.cfg.opacity = round(value, 2)
        self.opacity_value.config(text=f"{int(self.cfg.opacity * 100)}%")
        try:
            self.attributes("-alpha", max(0.4, min(1.0, self.cfg.opacity)))
        except tk.TclError:
            pass
        self.cfg.save()

    def _on_scale(self, value: float) -> None:
        rounded = round(value * 20) / 20  # snap to 0.05 steps
        self.scale_value.config(text=f"{rounded:.2f}×")
        if abs(rounded - self.theme.scale) < 0.01:
            return
        self.cfg.ui_scale = rounded
        self.cfg.save()
        self.theme = Theme(
            dark=self.theme.dark, accent_key=self.theme.accent_key, scale=rounded
        )
        self._build()

    def _on_topmost(self, value: bool) -> None:
        self.cfg.always_on_top = value
        self.attributes("-topmost", value)
        self.cfg.save()

    def _on_debug(self, value: bool) -> None:
        self.cfg.debug_dumps = value
        self.cfg.save()

    def _toggle_settings(self) -> None:
        self._cancel_recording()
        self.page = "main" if self.page == "settings" else "settings"
        self._build()

    def _toggle_compact(self) -> None:
        self.cfg.compact = not self.cfg.compact
        self.cfg.save()
        self.page = "main"
        self._build()

    # ==================================================================
    # rendering
    # ==================================================================
    def _apply_window_prefs(self) -> None:
        self.title("WARDOGS Artillery")
        try:
            self.iconbitmap(str(resource_dir() / "icon.ico"))
        except tk.TclError:
            pass
        self.attributes("-topmost", self.cfg.always_on_top)
        try:
            self.attributes("-alpha", max(0.4, min(1.0, self.cfg.opacity)))
        except tk.TclError:
            pass
        if self.cfg.window_pos:
            x, y = self.cfg.window_pos
        else:
            # Frameless windows get no placement from the window manager;
            # park it on the right edge, vertically centred.
            x = self.winfo_screenwidth() - self.theme.px(WINDOW_WIDTH) - 40
            y = max(40, self.winfo_screenheight() // 2 - 220)
        self.geometry(f"+{int(x)}+{int(y)}")

    def _announce_readiness(self) -> None:
        if not self.reader.trained:
            self._set_status("No font templates — press Train.", "warn")
            return
        # A config carried over from an earlier version can still hold a digit
        # hotkey, which now lands in the chat line instead of triggering us.
        typing = [
            pretty_hotkey(spec)
            for spec in (self.cfg.hotkey_gun, self.cfg.hotkey_target, self.cfg.hotkey_reset)
            if edits_text(spec)
        ]
        if typing:
            self._set_status(
                f"{', '.join(typing)} type a character into the chat input and "
                f"would corrupt the coordinate line. Rebind them in settings.",
                "danger",
            )
            return
        self._set_status(
            f"Ready. {pretty_hotkey(self.cfg.hotkey_gun)} — gun, "
            f"{pretty_hotkey(self.cfg.hotkey_target)} — target.",
            "muted",
        )

    def _set_status(self, text: str, tone: str = "muted") -> None:
        self._status = (text, tone)
        self._apply_status()

    def _apply_status(self) -> None:
        label = getattr(self, "status_label", None)
        if label is None or not label.winfo_exists():
            return
        text, tone = self._status
        colours = {
            "muted": self.theme.muted,
            "accent": self.theme.accent,
            "warn": self.theme.warn,
            "danger": self.theme.danger,
        }
        label.config(text=text, fg=colours.get(tone, self.theme.muted))

    def _paint_solutions(self, mission=None) -> None:
        """The elevation figures, side by side when the weapon has two arcs."""
        t = self.theme
        for child in self.solution_frame.winfo_children():
            child.destroy()

        if mission is None:
            tk.Label(self.solution_frame, text="—", bg=t.surface, fg=t.muted,
                     font=t.mono(30, "bold")).pack(anchor="w")
            return

        solutions = mission.solutions
        # Two figures have to share one row, so they shrink to fit beside
        # each other rather than pushing the window wider.
        single = len(solutions) == 1
        for index, sol in enumerate(solutions):
            column = tk.Frame(self.solution_frame, bg=t.surface)
            column.pack(side="left", padx=(0 if not index else t.px(20), 0))
            tk.Label(column, text=sol.arc.upper(), bg=t.surface, fg=t.faint,
                     font=t.sans(7, "bold"), anchor="w").pack(fill="x")
            if not sol.in_range:
                tk.Label(
                    column, text=sol.note, bg=t.surface, fg=t.danger, font=t.sans(8),
                    anchor="w", justify="left", wraplength=t.px(330 if single else 160),
                ).pack(fill="x", pady=(t.px(4), t.px(10)))
                continue
            figure = tk.Frame(column, bg=t.surface)
            figure.pack(anchor="w")
            tk.Label(figure, text=f"{sol.elevation_mil:.0f}", bg=t.surface, fg=t.accent,
                     font=t.mono(30 if single else 22, "bold")).pack(side="left")
            tk.Label(figure, text="mil", bg=t.surface, fg=t.muted, font=t.sans(11)).pack(
                side="left", anchor="s", padx=(t.px(5), 0), pady=(0, t.px(6))
            )
            # Where the table was read once the slope moved it. Each arc gets
            # its own figure -- the two trajectories climb differently -- and a
            # level shot reads at its own range, so there is nothing to add.
            equivalent = sol.equivalent_range_m
            if equivalent is not None and abs(equivalent - mission.range_m) >= 1.0:
                shown = f"{equivalent:,.0f}".replace(",", " ")
                tk.Label(
                    column, text=f"dialled as {shown} m", bg=t.surface, fg=t.faint,
                    font=t.sans(8), anchor="w",
                ).pack(fill="x")

    def _refresh(self) -> None:
        t = self.theme
        if self.page != "main":
            return

        if not self.cfg.compact:
            for row, point in ((self.gun_row, self.gun), (self.target_row, self.target)):
                row["value"].config(
                    text=str(point) if point else "not set",
                    fg=t.text if point else t.faint,
                )
                self._paint_dot(row["dot"], point is not None)

        if not (self.gun and self.target):
            self._paint_solutions(None)
            self.meta_label.config(text="range —   ·   azimuth —", fg=t.muted)
            self._fit()
            return

        height_gain, height_note = self._height_gain()
        mission = solve(
            self.gun, self.target, self.weapons[self.cfg.weapon],
            self.cfg.metres_per_unit, height_gain,
        )
        self._paint_solutions(mission)
        distance = f"{mission.range_m:,.0f}".replace(",", " ")
        lines = [
            f"{distance} m   ·   azimuth {mission.azimuth_deg:.2f}°"
            f"   ·   {mission.azimuth_mil:.0f} mil"
        ]
        if height_gain is not None:
            if abs(height_gain) < 0.5:
                lines.append("target level with the gun")
            else:
                lines.append(
                    f"target {abs(height_gain):,.0f} m "
                    f"{'above' if height_gain > 0 else 'below'} the gun".replace(",", " ")
                )
        elif height_note:
            lines.append(height_note)
        self.meta_label.config(text="\n".join(lines), fg=t.text)
        self._fit()

    def _height_gain(self) -> tuple[float | None, str]:
        """Target height above the gun, or None with a reason it is missing.

        None means the solution falls back to level ground, which the caller
        shows rather than passing off as a corrected one.
        """
        if not (MAPS and self.cfg.terrain_correction):
            return None, ""
        if not (self.gun and self.target):
            return None, ""
        grid = load_terrain(self._terrain_key())
        if grid is None:
            return None, "height data for this map is missing — level ground assumed"
        gun_h = grid.height_at(self.gun.x, self.gun.y)
        target_h = grid.height_at(self.target.x, self.target.y)
        if gun_h is None or target_h is None:
            off = "gun" if gun_h is None else "target"
            return None, (
                f"the {off} is outside {grid.label} — wrong map picked in ⚙?"
            )
        return target_h - gun_h, ""

    # ==================================================================
    def close(self) -> None:
        self.cfg.window_pos = (self.winfo_x(), self.winfo_y())
        self.cfg.save()
        self.listener.stop()
        self.reader.close()
        self.destroy()


def run() -> None:
    App(Config.load()).mainloop()
