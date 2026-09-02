"""Hand-drawn Tk widgets.

Tk's stock widgets (and ttk's themes) look like 1998 and cannot do rounded
corners, hover states or a switch.  Everything here is a Canvas that paints
itself from a Theme, which is both better looking and simpler than fighting
ttk styles.

Every widget repaints on ``set_theme`` so a palette change needs no rebuild of
the widget tree.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Sequence

from .theme import Theme, mix


def round_rect(canvas: tk.Canvas, x0, y0, x1, y1, r, **kwargs):
    """A rounded rectangle. Tk has no such primitive; a smoothed polygon is the
    standard trick and antialiases nicely at these sizes."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    points = [
        x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
        x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
        x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=16, **kwargs)


class _Painted(tk.Canvas):
    """Base for a canvas widget that repaints itself on state changes."""

    def __init__(self, parent: tk.Misc, theme: Theme, width: int, height: int) -> None:
        super().__init__(
            parent,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=parent.cget("bg"),
        )
        self.theme = theme
        self._hover = False
        self._active = False
        # A Canvas packed with fill="x" is wider than its requested width, so
        # painting against cget("width") would draw a stub button.
        self.bind("<Configure>", lambda _e: self.repaint(), add="+")

    def size(self) -> tuple[int, int]:
        """Actual on-screen size, falling back to the requested one."""
        return (
            self.winfo_width() if self.winfo_width() > 1 else int(self.cget("width")),
            self.winfo_height() if self.winfo_height() > 1 else int(self.cget("height")),
        )

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(bg=self.master.cget("bg"))
        self.repaint()

    def repaint(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


# ---------------------------------------------------------------------------
class Card(tk.Frame):
    """A padded surface panel with a hairline border."""

    def __init__(self, parent: tk.Misc, theme: Theme, padding: int = 12, **kwargs) -> None:
        super().__init__(
            parent,
            bg=theme.surface,
            highlightthickness=1,
            highlightbackground=theme.border,
            highlightcolor=theme.border,
            **kwargs,
        )
        self.theme = theme
        self._padding = padding
        self.body = tk.Frame(self, bg=theme.surface)
        self.body.pack(fill="both", expand=True, padx=theme.px(padding), pady=theme.px(padding))

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(bg=theme.surface, highlightbackground=theme.border)
        self.body.configure(bg=theme.surface)


# ---------------------------------------------------------------------------
class FlatButton(_Painted):
    KINDS = ("primary", "ghost", "quiet", "danger")

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        text: str,
        command: Callable[[], None],
        kind: str = "ghost",
        width: int | None = None,
        height: int = 32,
        radius: int = 8,
        font_size: int = 9,
        tooltip: str | None = None,
    ) -> None:
        self._text = text
        self._kind = kind
        self._radius = radius
        self._font_size = font_size
        self._fixed_width = width
        self._base_height = height
        self._command = command
        self.tooltip = tooltip
        self._enabled = True
        super().__init__(parent, theme, self._measure(theme), theme.px(height))
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(cursor="hand2")
        self.repaint()

    def _measure(self, theme: Theme) -> int:
        if self._fixed_width is not None:
            return theme.px(self._fixed_width)
        font = tkfont.Font(font=theme.sans(self._font_size, "bold"))
        return font.measure(self._text) + theme.px(26)

    def set_text(self, text: str) -> None:
        self._text = text
        self.configure(width=self._measure(self.theme))
        self.repaint()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self.repaint()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(
            bg=self.master.cget("bg"),
            width=self._measure(theme),
            height=theme.px(self._base_height),
        )
        self.repaint()

    # -- events ---------------------------------------------------------
    def _on_enter(self, _e=None):
        self._hover = True
        self.repaint()

    def _on_leave(self, _e=None):
        self._hover = self._active = False
        self.repaint()

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._active = True
        self.repaint()

    def _on_release(self, _e=None):
        was_active = self._active
        self._active = False
        self.repaint()
        if was_active and self._enabled:
            self._command()

    # -- painting -------------------------------------------------------
    def _colours(self) -> tuple[str, str, str | None]:
        t = self.theme
        if not self._enabled:
            return t.surface_hi, t.faint, t.border
        if self._kind == "primary":
            fill = t.accent
            if self._active:
                fill = mix(fill, "#000000", 0.22)
            elif self._hover:
                fill = mix(fill, "#ffffff", 0.12)
            return fill, t.accent_text, None
        if self._kind == "danger":
            base = t.danger
            fill = mix(t.surface_hi, base, 0.9 if self._active else (0.7 if self._hover else 0.0))
            ink = t.accent_text if (self._hover or self._active) else base
            return fill, ink, base
        if self._kind == "quiet":
            fill = t.surface_hi if (self._hover or self._active) else t.surface
            return fill, t.text if self._hover else t.muted, None
        fill = t.surface_hi
        if self._active:
            fill = mix(fill, t.bg, 0.4)
        elif self._hover:
            fill = mix(fill, t.text, 0.10)
        return fill, t.text, t.border

    def repaint(self) -> None:
        self.delete("all")
        t = self.theme
        w, h = self.size()
        fill, ink, border = self._colours()
        round_rect(self, 1, 1, w - 1, h - 1, t.px(self._radius), fill=fill,
                   outline=border or fill, width=1)
        self.create_text(
            w / 2, h / 2 + 1, text=self._text, fill=ink,
            font=t.sans(self._font_size, "bold"),
        )


# ---------------------------------------------------------------------------
class IconButton(_Painted):
    """Small square button for the title bar; ``text`` is a single glyph."""

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        text: str,
        command: Callable[[], None],
        size: int = 26,
        danger: bool = False,
        font_size: int = 11,
    ) -> None:
        self._text = text
        self._command = command
        self._size = size
        self._danger = danger
        self._font_size = font_size
        super().__init__(parent, theme, theme.px(size), theme.px(size))
        self.bind("<Enter>", lambda _e: (setattr(self, "_hover", True), self.repaint()))
        self.bind("<Leave>", lambda _e: (setattr(self, "_hover", False), self.repaint()))
        self.bind("<Button-1>", lambda _e: self._command())
        self.configure(cursor="hand2")
        self.repaint()

    def set_text(self, text: str) -> None:
        self._text = text
        self.repaint()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(
            bg=self.master.cget("bg"),
            width=theme.px(self._size),
            height=theme.px(self._size),
        )
        self.repaint()

    def repaint(self) -> None:
        self.delete("all")
        t = self.theme
        w, h = self.size()
        if self._hover:
            fill = t.danger if self._danger else t.surface_hi
            round_rect(self, 0, 0, w, h, t.px(6), fill=fill, outline=fill)
            ink = "#ffffff" if self._danger else t.text
        else:
            ink = t.muted
        self.create_text(w / 2, h / 2, text=self._text, fill=ink, font=t.sans(self._font_size))


# ---------------------------------------------------------------------------
class Toggle(_Painted):
    """An iOS-style switch. Animated, because a snapping switch feels broken."""

    WIDTH = 40
    HEIGHT = 22

    def __init__(
        self, parent: tk.Misc, theme: Theme, value: bool, command: Callable[[bool], None]
    ) -> None:
        self._value = value
        self._command = command
        self._pos = 1.0 if value else 0.0
        self._anim: str | None = None
        super().__init__(parent, theme, theme.px(self.WIDTH), theme.px(self.HEIGHT))
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda _e: (setattr(self, "_hover", True), self.repaint()))
        self.bind("<Leave>", lambda _e: (setattr(self, "_hover", False), self.repaint()))
        self.configure(cursor="hand2")
        self.repaint()

    @property
    def value(self) -> bool:
        return self._value

    def set_value(self, value: bool, notify: bool = False) -> None:
        if value == self._value:
            return
        self._value = value
        self._animate()
        if notify:
            self._command(value)

    def _toggle(self, _e=None) -> None:
        self._value = not self._value
        self._animate()
        self._command(self._value)

    def _animate(self) -> None:
        target = 1.0 if self._value else 0.0
        if self._anim:
            try:
                self.after_cancel(self._anim)
            except tk.TclError:
                pass
            self._anim = None

        def step() -> None:
            delta = target - self._pos
            if abs(delta) < 0.02:
                self._pos = target
                self._anim = None
            else:
                self._pos += delta * 0.35
                self._anim = self.after(12, step)
            self.repaint()

        step()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(
            bg=self.master.cget("bg"),
            width=theme.px(self.WIDTH),
            height=theme.px(self.HEIGHT),
        )
        self.repaint()

    def repaint(self) -> None:
        self.delete("all")
        t = self.theme
        w, h = self.size()
        off = mix(t.surface_hi, t.text, 0.18 if self._hover else 0.08)
        track = mix(off, t.accent, self._pos)
        round_rect(self, 0, 0, w, h, h / 2, fill=track, outline=track)
        pad = t.px(3)
        knob_d = h - pad * 2
        x = pad + self._pos * (w - pad * 2 - knob_d)
        knob = "#ffffff" if self._pos > 0.5 else mix(t.text, t.surface, 0.35)
        self.create_oval(x, pad, x + knob_d, pad + knob_d, fill=knob, outline=knob)


# ---------------------------------------------------------------------------
class Segmented(_Painted):
    """A row of mutually exclusive options — clearer than a dropdown for 2-4."""

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        options: Sequence[tuple[str, str]],
        value: str,
        command: Callable[[str], None],
        height: int = 30,
        font_size: int = 9,
        width: int = 230,
    ) -> None:
        # NB: not "_options" — tk.Misc._options() is what builds widget
        # configuration, and shadowing it breaks Canvas construction.
        # Not `_options`: tk.Misc._options() builds widget configuration,
        # and shadowing it makes Canvas construction fail outright.
        self._segments = list(options)
        self._value = value
        self._command = command
        self._height = height
        self._font_size = font_size
        self._base_width = width
        self._hover_index = -1
        super().__init__(parent, theme, theme.px(width), theme.px(height))
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", lambda _e: self.repaint())
        self.configure(cursor="hand2")
        self.repaint()

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self.repaint()

    def set_options(self, options: Sequence[tuple[str, str]]) -> None:
        self._segments = list(options)
        self.repaint()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(
            bg=self.master.cget("bg"),
            width=theme.px(self._base_width),
            height=theme.px(self._height),
        )
        self.repaint()

    def _index_at(self, x: int) -> int:
        if not self._segments:
            return -1
        w = self.size()[0]
        return max(0, min(len(self._segments) - 1, int(x / (w / len(self._segments)))))

    def _on_click(self, event: tk.Event) -> None:
        index = self._index_at(event.x)
        if index < 0:
            return
        key = self._segments[index][0]
        if key != self._value:
            self._value = key
            self.repaint()
            self._command(key)

    def _on_motion(self, event: tk.Event) -> None:
        index = self._index_at(event.x)
        if index != self._hover_index:
            self._hover_index = index
            self.repaint()

    def _on_leave(self, _e=None) -> None:
        self._hover_index = -1
        self.repaint()

    def repaint(self) -> None:
        self.delete("all")
        if not self._segments:
            return
        t = self.theme
        w, h = self.size()
        round_rect(self, 0, 0, w, h, t.px(8), fill=t.surface_hi, outline=t.border)
        cell = w / len(self._segments)
        pad = t.px(3)
        for i, (key, label) in enumerate(self._segments):
            x0 = i * cell
            selected = key == self._value
            if selected:
                round_rect(
                    self, x0 + pad, pad, x0 + cell - pad, h - pad, t.px(6),
                    fill=t.accent, outline=t.accent,
                )
                ink = t.accent_text
            elif i == self._hover_index:
                ink = t.text
            else:
                ink = t.muted
            self.create_text(
                x0 + cell / 2, h / 2 + 1, text=label, fill=ink,
                font=t.sans(self._font_size, "bold" if selected else "normal"),
            )


# ---------------------------------------------------------------------------
class Slider(_Painted):
    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        value: float,
        command: Callable[[float], None],
        minimum: float = 0.0,
        maximum: float = 1.0,
        height: int = 24,
        width: int = 150,
    ) -> None:
        self._min, self._max = minimum, maximum
        self._value = value
        self._command = command
        self._height = height
        self._base_width = width
        self._dragging = False
        super().__init__(parent, theme, theme.px(width), theme.px(height))
        self.bind("<Button-1>", self._on_drag)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda _e: (setattr(self, "_hover", True), self.repaint()))
        self.bind("<Leave>", lambda _e: (setattr(self, "_hover", False), self.repaint()))
        self.bind("<Configure>", lambda _e: self.repaint())
        self.configure(cursor="hand2")
        self.repaint()

    @property
    def value(self) -> float:
        return self._value

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(
            bg=self.master.cget("bg"),
            width=theme.px(self._base_width),
            height=theme.px(self._height),
        )
        self.repaint()

    def _on_drag(self, event: tk.Event) -> None:
        self._dragging = True
        w = self.size()[0]
        pad = self.theme.px(8)
        span = max(1, w - pad * 2)
        t = max(0.0, min(1.0, (event.x - pad) / span))
        self._value = self._min + t * (self._max - self._min)
        self.repaint()
        self._command(self._value)

    def _on_release(self, _e=None) -> None:
        self._dragging = False
        self.repaint()

    def repaint(self) -> None:
        self.delete("all")
        t = self.theme
        w, h = self.size()
        pad = t.px(8)
        cy = h / 2
        span = max(1, w - pad * 2)
        frac = 0.0 if self._max == self._min else (self._value - self._min) / (self._max - self._min)
        track_h = t.px(4)
        round_rect(self, pad, cy - track_h / 2, w - pad, cy + track_h / 2, track_h / 2,
                   fill=t.surface_hi, outline=t.surface_hi)
        filled = pad + frac * span
        if filled > pad:
            round_rect(self, pad, cy - track_h / 2, filled, cy + track_h / 2, track_h / 2,
                       fill=t.accent, outline=t.accent)
        r = t.px(8 if (self._hover or self._dragging) else 6)
        self.create_oval(filled - r, cy - r, filled + r, cy + r,
                         fill="#ffffff", outline=t.accent, width=t.px(2))


# ---------------------------------------------------------------------------
class SwatchRow(tk.Frame):
    """Accent colour picker."""

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        accents: dict[str, tuple[str, str]],
        value: str,
        command: Callable[[str], None],
    ) -> None:
        super().__init__(parent, bg=parent.cget("bg"))
        self.theme = theme
        self._accents = accents
        self._value = value
        self._command = command
        self._dots: dict[str, tk.Canvas] = {}
        self._build()

    def _build(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        self._dots.clear()
        size = self.theme.px(22)
        for key, (label, colour) in self._accents.items():
            dot = tk.Canvas(self, width=size, height=size, highlightthickness=0, bd=0,
                            bg=self.cget("bg"), cursor="hand2")
            dot.pack(side="left", padx=(0, self.theme.px(6)))
            dot.bind("<Button-1>", lambda _e, k=key: self._pick(k))
            self._dots[key] = dot
        self._paint()

    def _pick(self, key: str) -> None:
        self._value = key
        self._paint()
        self._command(key)

    def _paint(self) -> None:
        size = self.theme.px(22)
        for key, canvas in self._dots.items():
            canvas.delete("all")
            colour = self._accents[key][1]
            pad = self.theme.px(3)
            canvas.create_oval(pad, pad, size - pad, size - pad, fill=colour, outline=colour)
            if key == self._value:
                canvas.create_oval(0, 0, size - 1, size - 1, outline=colour, width=self.theme.px(2))

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(bg=self.master.cget("bg"))
        for canvas in self._dots.values():
            canvas.configure(bg=self.cget("bg"))
        self._build()


# ---------------------------------------------------------------------------
class HotkeyRecorder(_Painted):
    """Click, then press a combination — the key you press becomes the binding.

    Recording goes through the same low-level hook that dispatches hotkeys, so
    what gets recorded is exactly what will later be matched, and the key is
    swallowed while recording instead of leaking into the game.
    """

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        spec: str,
        on_record: Callable[[str], None],
        start_capture: Callable[[Callable[[str | None], None]], None],
        cancel_capture: Callable[[], None],
        pretty: Callable[[str], str],
        width: int = 132,
        height: int = 30,
    ) -> None:
        self._spec = spec
        self._on_record = on_record
        self._start_capture = start_capture
        self._cancel_capture = cancel_capture
        self._pretty = pretty
        self._armed = False
        self._dash_phase = 0
        self._anim: str | None = None
        self._base_width = width
        self._base_height = height
        super().__init__(parent, theme, theme.px(width), theme.px(height))
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda _e: (setattr(self, "_hover", True), self.repaint()))
        self.bind("<Leave>", lambda _e: (setattr(self, "_hover", False), self.repaint()))
        self.configure(cursor="hand2")
        self.repaint()

    @property
    def spec(self) -> str:
        return self._spec

    def set_spec(self, spec: str) -> None:
        self._spec = spec
        self.repaint()

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.configure(
            bg=self.master.cget("bg"),
            width=theme.px(self._base_width),
            height=theme.px(self._base_height),
        )
        self.repaint()

    # -- recording ------------------------------------------------------
    def _on_click(self, _e=None) -> None:
        if self._armed:
            self._disarm()
            self._cancel_capture()
            return
        self._armed = True
        self._pulse()
        self._start_capture(self._finish)

    def _finish(self, spec: str | None) -> None:
        self._disarm()
        if spec:
            self._spec = spec
            self._on_record(spec)
        self.repaint()

    def _disarm(self) -> None:
        self._armed = False
        if self._anim:
            try:
                self.after_cancel(self._anim)
            except tk.TclError:
                pass
            self._anim = None

    def _pulse(self) -> None:
        if not self._armed:
            return
        self._dash_phase = (self._dash_phase + 1) % 20
        self.repaint()
        self._anim = self.after(60, self._pulse)

    def repaint(self) -> None:
        self.delete("all")
        t = self.theme
        w, h = self.size()
        if self._armed:
            glow = mix(t.surface_hi, t.accent, 0.22)
            round_rect(self, 1, 1, w - 1, h - 1, t.px(8), fill=glow, outline=t.accent, width=t.px(2))
            dots = "·" * (1 + self._dash_phase % 3)
            self.create_text(
                w / 2, h / 2 + 1, text=f"press{dots}", fill=t.accent,
                font=t.sans(9, "bold"),
            )
            return
        fill = mix(t.surface_hi, t.text, 0.10) if self._hover else t.surface_hi
        round_rect(self, 1, 1, w - 1, h - 1, t.px(8), fill=fill, outline=t.border)
        self.create_text(
            w / 2, h / 2 + 1, text=self._pretty(self._spec), fill=t.text, font=t.mono(9, "bold")
        )


# ---------------------------------------------------------------------------
class Row(tk.Frame):
    """A settings line: label on the left, control hard against the right."""

    def __init__(
        self, parent: tk.Misc, theme: Theme, label: str, hint: str | None = None
    ) -> None:
        super().__init__(parent, bg=parent.cget("bg"))
        self.theme = theme
        text_box = tk.Frame(self, bg=self.cget("bg"))
        text_box.pack(side="left", fill="x", expand=True)
        self.title = tk.Label(
            text_box, text=label, bg=self.cget("bg"), fg=theme.text,
            font=theme.sans(9), anchor="w", justify="left",
        )
        self.title.pack(anchor="w")
        self.hint: tk.Label | None = None
        if hint:
            self.hint = tk.Label(
                text_box, text=hint, bg=self.cget("bg"), fg=theme.faint,
                font=theme.sans(8), anchor="w", justify="left", wraplength=theme.px(170),
            )
            self.hint.pack(anchor="w")

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        bg = self.master.cget("bg")
        self.configure(bg=bg)
        for widget in (self.title, self.hint):
            if widget is not None:
                widget.configure(bg=bg)
        self.title.configure(fg=theme.text, font=theme.sans(9))
        if self.hint is not None:
            self.hint.configure(fg=theme.faint, font=theme.sans(8))


# ---------------------------------------------------------------------------
class ScrollArea(tk.Frame):
    """Vertically scrollable container with a slim custom scrollbar.

    ``.body`` is where content goes.
    """

    BAR_WIDTH = 6

    def __init__(self, parent: tk.Misc, theme: Theme, height: int) -> None:
        super().__init__(parent, bg=parent.cget("bg"))
        self.theme = theme
        # Pack order matters: an expanding widget packed first eats the whole
        # frame and later siblings never get mapped. The bar goes in first.
        self.bar = tk.Canvas(
            self, bg=parent.cget("bg"), highlightthickness=0, bd=0,
            width=theme.px(self.BAR_WIDTH + 4),
        )
        self.bar.pack(side="right", fill="y")
        self.canvas = tk.Canvas(
            self, bg=parent.cget("bg"), highlightthickness=0, bd=0, height=theme.px(height)
        )
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=parent.cget("bg"))
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bar.bind("<Configure>", lambda _e: self._paint_bar())
        self.bar.bind("<Button-1>", self._on_bar_click)
        self.bar.bind("<B1-Motion>", self._on_bar_click)
        # Tk delivers the wheel to the widget under the pointer, so watch it
        # globally and decide ourselves whether the pointer is over us.
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        # yview() is meaningless until the scrollregion and the real
        # widget heights exist, so paint the bar once things settle.
        self.after_idle(self._paint_bar)
        self.after(80, self._paint_bar)

    def _on_body_configure(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._paint_bar()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self._paint_bar()

    def _contains_pointer(self) -> bool:
        widget = self.winfo_containing(*self.winfo_pointerxy())
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_wheel(self, event: tk.Event) -> None:
        if not self.winfo_ismapped() or not self._contains_pointer():
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self._paint_bar()

    def _on_bar_click(self, event: tk.Event) -> None:
        height = self.bar.winfo_height() or 1
        first, last = self.canvas.yview()
        visible = max(0.05, last - first)
        self.canvas.yview_moveto(max(0.0, event.y / height - visible / 2))
        self._paint_bar()

    def _paint_bar(self) -> None:
        t = self.theme
        self.bar.delete("all")
        first, last = self.canvas.yview()
        if last - first >= 0.999:  # nothing to scroll
            return
        height = self.bar.winfo_height() or 1
        w = t.px(self.BAR_WIDTH)
        x0 = t.px(2)
        track = mix(t.bg, t.text, 0.08)
        round_rect(self.bar, x0, 0, x0 + w, height, w / 2, fill=track, outline=track)
        y0, y1 = first * height, last * height
        thumb = mix(t.surface_hi, t.text, 0.42)
        round_rect(self.bar, x0, y0, x0 + w, max(y1, y0 + w * 2), w / 2,
                   fill=thumb, outline=thumb)
