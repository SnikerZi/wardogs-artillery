"""Full-screen overlay for dragging out a screen rectangle."""

from __future__ import annotations

import ctypes
import tkinter as tk

from ..capture import ensure_dpi_awareness
from .theme import Theme


def virtual_screen() -> tuple[int, int, int, int]:
    """(left, top, width, height) covering every monitor."""
    ensure_dpi_awareness()
    m = ctypes.windll.user32.GetSystemMetrics
    return int(m(76)), int(m(77)), int(m(78)), int(m(79))


def select_region(
    parent: tk.Misc, hint: str, theme: Theme | None = None
) -> tuple[int, int, int, int] | None:
    """Dim the screen and let the user drag a rectangle. None if cancelled."""
    left, top, width, height = virtual_screen()

    theme = theme or Theme()

    overlay = tk.Toplevel(parent)
    overlay.overrideredirect(True)
    overlay.geometry(f"{width}x{height}+{left}+{top}")
    overlay.attributes("-topmost", True)
    overlay.attributes("-alpha", 0.35)
    overlay.configure(bg="black")
    overlay.config(cursor="crosshair")

    canvas = tk.Canvas(overlay, bg="black", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        width // 2, 62, text=hint, fill="#ffffff",
        font=theme.sans(15, "bold"), justify="center",
    )

    state: dict[str, object] = {"start": None, "rect": None, "result": None}

    def on_press(event: tk.Event) -> None:
        state["start"] = (event.x, event.y)
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline=theme.accent, width=2
        )

    def on_drag(event: tk.Event) -> None:
        if not state["start"]:
            return
        x0, y0 = state["start"]
        canvas.coords(state["rect"], x0, y0, event.x, event.y)

    def on_release(event: tk.Event) -> None:
        if not state["start"]:
            return
        x0, y0 = state["start"]
        x1, y1 = event.x, event.y
        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)
        if rw >= 8 and rh >= 6:
            state["result"] = (left + rx, top + ry, rw, rh)
        overlay.destroy()

    def cancel(_event: tk.Event | None = None) -> None:
        overlay.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    overlay.bind("<Escape>", cancel)
    overlay.focus_force()
    overlay.grab_set()
    parent.wait_window(overlay)
    return state["result"]  # type: ignore[return-value]
