"""The transparent overlay: the firing solution and nothing else.

The panel spends 400 px on cards, borders and labels that organise it.  That
is money well spent next to a game and wasted on top of one, where the only
question is which number to dial.  This mode drops every surface and draws
bare text on a background Windows punches straight out of the window.

Text on unknown game footage has no surface to sit on, so each string is
drawn several times in black underneath itself.  A halo costs a handful of
canvas items and stays readable over snow as well as over shadow, which a
translucent plate -- the obvious alternative -- manages over neither.

The overlay does not decide *what* to show: the caller hands it cells and a
meta line, because which figures are wanted is a setting.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import textwrap
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

from .theme import Theme

#: The colour Windows makes invisible. Nothing else in the HUD may use it, so
#: it is a value no palette would pick, and near-black on purpose: the
#: antialiased fringe of a glyph blends towards the background and is
#: therefore never keyed away, so a dark key leaves that fringe looking like
#: part of the halo instead of a bright outline around every digit.
KEY = "#010203"
HALO = "#000000"

#: The inks are fixed instead of coming from the palette. The HUD sits on
#: game footage rather than on a surface, so "light theme" means nothing here
#: -- dark ink would simply disappear. Only the accent follows the user's
#: choice, and the warning colours are lifted towards white so they still
#: read at 60% opacity.
INK = "#f2f6fb"
INK_FAINT = "#a9b6c8"
WARN = "#ffd479"
DANGER = "#ff9b9b"

#: The eight directions a halo is drawn in.
_RING = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
GA_ROOT = 2

try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
except (AttributeError, OSError):  # pragma: no cover - not Windows
    _user32 = None


def _root_handle(window: tk.Misc) -> int:
    """The HWND of the real top-level window behind a Tk widget.

    Tk sometimes wraps a toplevel in a frame of its own, so the id a widget
    reports is not always the window the shell manages.  GA_ROOT walks up to
    the one that is -- unlike GetParent, which hands back the *owner* of a
    popup and would style the wrong window.
    """
    ident = window.winfo_id()
    if _user32 is None:  # pragma: no cover - not Windows
        return ident
    try:
        _user32.GetAncestor.restype = wt.HWND
        _user32.GetAncestor.argtypes = (wt.HWND, ctypes.c_uint)
        root = _user32.GetAncestor(wt.HWND(ident), GA_ROOT)
    except OSError:  # pragma: no cover - defensive
        return ident
    return int(root) if root else ident


def set_click_through(window: tk.Misc, enabled: bool) -> bool:
    """Let the mouse pass through the window, or take it back.

    Colour-keyed pixels are click-through by themselves, but the glyphs are
    not: without this a stray click on a digit is swallowed instead of
    reaching the game, which in the middle of a fight is unforgivable.
    Returns whether the style could be applied.
    """
    if _user32 is None:  # pragma: no cover - not Windows
        return False
    # GetWindowLongPtrW does not exist on 32-bit Windows, where the plain
    # GetWindowLongW is already wide enough; getattr picks whichever is there.
    read = getattr(_user32, "GetWindowLongPtrW", None) or _user32.GetWindowLongW
    write = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
    read.restype = ctypes.c_ssize_t
    read.argtypes = (wt.HWND, ctypes.c_int)
    write.restype = ctypes.c_ssize_t
    write.argtypes = (wt.HWND, ctypes.c_int, ctypes.c_ssize_t)
    try:
        hwnd = wt.HWND(_root_handle(window))
        style = read(hwnd, GWL_EXSTYLE)
        if enabled:
            updated = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            # WS_EX_LAYERED stays put: the colour key needs it.
            updated = style & ~WS_EX_TRANSPARENT
        if updated != style:
            write(hwnd, GWL_EXSTYLE, updated)
    except (OSError, tk.TclError):  # pragma: no cover - defensive
        return False
    return True


class Hud(tk.Canvas):
    """One canvas holding the whole overlay, sized to its own content."""

    PAD = 7
    #: Longest line of status text before it wraps, in characters. Two lines
    #: is the ceiling -- past that the overlay is a dialog box again.
    WRAP = 46

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        *,
        on_move: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg=KEY, highlightthickness=0, bd=0, width=1, height=1)
        self.theme = theme
        self._on_move = on_move
        self._on_exit = on_exit
        self._origin: tuple[int, int] | None = None
        self._fonts: dict[tuple, tkfont.Font] = {}
        self._wanted = (theme.px(140), theme.px(56))
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", lambda _e: self._on_exit())
        self.configure(cursor="fleur")

    # -- window plumbing ------------------------------------------------
    def set_keyed(self, keyed: bool) -> None:
        """Whether the background is being punched out.

        When the shell refuses the colour key there is nothing to punch, and
        the keyed background is near-black anyway -- which is the panel's own
        background colour, so it is used instead and the overlay degrades
        into a small dark plate rather than a black rectangle.
        """
        self.configure(bg=KEY if keyed else self.theme.bg)

    def wanted(self) -> tuple[int, int]:
        """Size the last render needs, for the window to grow or shrink to."""
        return self._wanted

    # -- dragging -------------------------------------------------------
    def _press(self, event: tk.Event) -> None:
        top = self.winfo_toplevel()
        self._origin = (event.x_root - top.winfo_x(), event.y_root - top.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        if not self._origin:
            return
        dx, dy = self._origin
        self.winfo_toplevel().geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _release(self, _event: tk.Event) -> None:
        if self._origin:
            self._origin = None
            self._on_move()

    # -- painting -------------------------------------------------------
    def _font(self, spec: tuple) -> tkfont.Font:
        # Cached: a Font object registers a name inside Tk that outlives the
        # object, and the HUD repaints on every hotkey press.
        cached = self._fonts.get(spec)
        if cached is None:
            cached = self._fonts[spec] = tkfont.Font(root=self, font=spec)
        return cached

    def _gap(self, size: int, floor: int) -> int:
        """A scaled gap that keeps a minimum, like the fonts.

        Spacing has to stop shrinking where the fonts do, or the separator
        ends up welded to the digit on either side of it.
        """
        return max(floor, self.theme.px(size))

    def _face(
        self, mono: bool, size: int, floor: int, weight: str = "normal"
    ) -> tkfont.Font:
        """A scaled font that stops shrinking before it stops being legible.

        The size slider is there to shrink the figures, not to dissolve the
        line underneath them: at 0.6 an 8 pt label lands on five pixels.
        Each role gets its own floor, so the elevation goes on shrinking
        long after the small print has stopped.
        """
        family = (self.theme.mono if mono else self.theme.sans)(size)[0]
        return self._font((family, max(floor, self.theme.px(size)), weight))

    def _text(
        self,
        x: float,
        y: float,
        text: str,
        font: tkfont.Font,
        fill: str,
        anchor: str = "sw",
        ring: int = 1,
    ) -> int:
        for radius in range(1, ring + 1):
            step = self.theme.px(radius)
            for dx, dy in _RING:
                self.create_text(
                    x + dx * step, y + dy * step, text=text, font=font,
                    fill=HALO, anchor=anchor, justify="left",
                )
        return self.create_text(
            x, y, text=text, font=font, fill=fill, anchor=anchor, justify="left"
        )

    def render(
        self,
        cells: list[tuple[str, str | None, str, str]],
        meta: str = "",
        status: tuple[str, str] | None = None,
        one_line: bool = False,
    ) -> None:
        """Paint the overlay and work out how big the window has to be.

        ``cells``  the big row, left to right, as
                   ``(label, value, unit, note)``.  A value of None means
                   there is no figure and ``note`` says why.  The unit is
                   drawn small and dropped in against the baseline, so two
                   arcs can share a single trailing "mil".
        ``meta``   the small line underneath, or "" for none.
        ``status`` ``(text, tone)`` -- only ever passed when something needs
                   saying, because a HUD that narrates itself is noise.
        ``one_line`` puts ``meta`` on the end of the big row instead of under
                   it: wider, but a third shorter, which is what matters for
                   something parked under a crosshair.
        """
        self.delete("all")
        t = self.theme
        pad = self._gap(self.PAD, 5)
        big = self._face(True, 22, 13, "bold")
        tag = self._face(False, 7, 6, "bold")
        # Big enough to read as a unit: at 9 pt against a 22 pt figure the
        # "m" in "442 m" looks like a subscript.
        unit_font = self._face(False, 11, 8)
        # Bold: the halo under a 1 px stroke is most of the glyph, and
        # over snow the small line then reads as an outline drawing.
        small = self._face(True, 9, 8, "bold")

        if not cells:
            cells = [("", "—", "", "")]

        # Everything in the big row shares one baseline, so labels and units
        # sit level with the bottom of the figures instead of floating.
        baseline = pad + big.metrics("ascent")
        x = pad
        for index, (label, value, unit, note) in enumerate(cells):
            if index:
                # A gap alone is not enough of a break: "442 m 56.3°" reads
                # as one number with something after it.
                x += self._gap(7, 5)
                self._text(x, baseline, "·", unit_font, INK_FAINT)
                x += unit_font.measure("·") + self._gap(7, 5)
            if label:
                self._text(x, baseline, label, tag, INK_FAINT)
                x += tag.measure(label) + self._gap(5, 4)
            if value is None:
                self._text(x, baseline, note, unit_font, WARN)
                x += unit_font.measure(note)
                continue
            self._text(x, baseline, value, big, t.accent, ring=2)
            x += big.measure(value)
            if unit:
                x += self._gap(4, 3)
                self._text(x, baseline, unit, unit_font, INK_FAINT)
                x += unit_font.measure(unit)

        right = x
        y = baseline + big.metrics("descent") + t.px(2)
        if meta and one_line:
            x += self._gap(10, 7)
            self._text(x, baseline, meta, small, INK)
            right = x + small.measure(meta)
        elif meta:
            self._text(pad, y, meta, small, INK, anchor="nw")
            right = max(right, pad + small.measure(meta))
            y += small.metrics("linespace")
        if status and status[0]:
            text, tone = status
            lines = textwrap.wrap(text, self.WRAP) or [""]
            if len(lines) > 2:
                lines = [lines[0], lines[1].rstrip(" ,.") + " …"]
            self._text(
                pad, y + t.px(2), "\n".join(lines), small,
                DANGER if tone == "danger" else WARN, anchor="nw",
            )
            right = max(right, pad + max(small.measure(line) for line in lines))
            y += t.px(2) + len(lines) * small.metrics("linespace")

        self._wanted = (int(right + pad), int(y + pad))
        self.configure(width=self._wanted[0], height=self._wanted[1])
