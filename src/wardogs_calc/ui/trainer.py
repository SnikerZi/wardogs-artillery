"""Glyph training: label the characters of the HUD readout once.

The bundled bank already holds the game's font, so this is the repair path —
a different resolution or UI scale can render glyphs the bank has not seen.
A real crop is segmented and the user types what each blob is; about a dozen
keystrokes, after which recognition is exact on that machine.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import numpy as np

from .theme import Theme
from .widgets import FlatButton, round_rect
from ..vision.glyphs import GLYPH_FILE, USER_GROUP, GlyphSet
from ..vision.segment import (
    Glyph,
    binarize_variants,
    connected_components,
    drop_oversized,
    merge_stacked,
)

_MAX_SCALE = 5
_MAX_VIEW_WIDTH = 760


def _mask_to_photo(
    mask: np.ndarray, highlight: Glyph | None, scale: int, theme: Theme
) -> tk.PhotoImage:
    h, w = mask.shape
    photo = tk.PhotoImage(width=w * scale, height=h * scale)
    hi = np.zeros_like(mask)
    if highlight is not None:
        hi[highlight.y : highlight.bottom, highlight.x : highlight.right] = highlight.mask
    bg, fg, accent = theme.bg, theme.muted, theme.accent
    rows: list[str] = []
    for y in range(h):
        row: list[str] = []
        for x in range(w):
            colour = accent if hi[y, x] else (fg if mask[y, x] else bg)
            row.extend([colour] * scale)
        line = "{" + " ".join(row) + "}"
        rows.extend([line] * scale)
    photo.put(" ".join(rows))
    return photo


class TrainerDialog(tk.Toplevel):
    """Walks through every blob in a crop asking for its character."""

    def __init__(self, parent: tk.Misc, crop: np.ndarray, glyph_path: Path, theme: Theme) -> None:
        super().__init__(parent)
        self.theme = theme
        self.title("Train font")
        self.configure(bg=theme.bg)
        self.resizable(False, False)
        self.attributes("-topmost", True)

        self.glyph_path = glyph_path
        self.glyph_set = GlyphSet.load(glyph_path)
        self.saved = False
        self._photo: tk.PhotoImage | None = None

        # Train on exactly the masks recognition will use, and record each
        # character in every one of them.  Training on a single binarisation
        # while recognising on another is the classic way to end up with
        # templates that never match in the field.
        self.variants = binarize_variants(crop)
        self.mask = self.variants[0][1] if self.variants else np.zeros(crop.shape[:2], bool)
        # min_pixels=2 matches the reader: a decimal point in a 19 px
        # readout is barely 2x2 px and must not be filtered away here.
        self.glyphs = drop_oversized(
            merge_stacked(connected_components(self.mask, min_pixels=2))
        )
        self.alternates = [
            drop_oversized(merge_stacked(connected_components(mask, min_pixels=2)))
            for _name, mask in self.variants[1:]
        ]
        self.index = 0
        #: Templates added per accepted glyph, so Back can undo exactly.
        self._added: list[int] = []

        if not self.glyphs:
            messagebox.showwarning(
                "Nothing found",
                "No characters were found in the selected area.\n"
                "Check that it covers the coordinate line.",
                parent=parent,
            )
            self.after(0, self.destroy)
            return

        self._scale = max(
            1, min(_MAX_SCALE, _MAX_VIEW_WIDTH // max(self.mask.shape[1], 1))
        )
        self._build()
        self._show()
        self.entry.focus_set()
        self.grab_set()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        t = self.theme
        wrap = tk.Frame(self, bg=t.bg, padx=t.px(16), pady=t.px(14))
        wrap.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            wrap,
            width=self.mask.shape[1] * self._scale,
            height=self.mask.shape[0] * self._scale,
            bg=t.bg,
            highlightthickness=1,
            highlightbackground=t.border,
            bd=0,
        )
        self.canvas.pack()

        self.progress = tk.Canvas(
            wrap, height=t.px(4), bg=t.bg, highlightthickness=0, bd=0
        )
        self.progress.pack(fill="x", pady=(t.px(10), t.px(8)))

        self.prompt = tk.Label(wrap, text="", bg=t.bg, fg=t.text, font=t.sans(10))
        self.prompt.pack()

        self.entry = tk.Entry(
            wrap,
            width=4,
            justify="center",
            font=t.mono(20, "bold"),
            bg=t.surface_hi,
            fg=t.text,
            insertbackground=t.accent,
            relief="flat",
            highlightthickness=2,
            highlightbackground=t.border,
            highlightcolor=t.accent,
        )
        self.entry.pack(pady=t.px(10), ipady=t.px(6))
        self.entry.bind("<Return>", self._accept)
        self.entry.bind("<Escape>", lambda _e: self.destroy())

        row = tk.Frame(wrap, bg=t.bg)
        row.pack(pady=(t.px(2), t.px(10)))
        self.back_button = FlatButton(row, t, "← Back", self._back, width=90, height=30)
        self.back_button.pack(side="left", padx=t.px(3))
        FlatButton(row, t, "Skip", self._skip, width=110, height=30).pack(
            side="left", padx=t.px(3)
        )
        FlatButton(row, t, "Save", self._finish, kind="primary", width=110, height=30).pack(
            side="left", padx=t.px(3)
        )

        tk.Label(
            wrap,
            text="Type the character (a digit, a dot, x or y) and press Enter.\n"
            "Use Skip for icons and anything else.",
            bg=t.bg,
            fg=t.faint,
            font=t.sans(8),
            justify="center",
        ).pack()

    def _paint_progress(self) -> None:
        t = self.theme
        self.progress.delete("all")
        width = self.progress.winfo_width() or self.mask.shape[1] * self._scale
        h = t.px(4)
        round_rect(self.progress, 0, 0, width, h, h / 2, fill=t.surface_hi, outline=t.surface_hi)
        done = self.index / max(1, len(self.glyphs))
        if done > 0:
            round_rect(
                self.progress, 0, 0, max(h, width * done), h, h / 2,
                fill=t.accent, outline=t.accent,
            )

    def _show(self) -> None:
        if self.index >= len(self.glyphs):
            self._finish()
            return
        glyph = self.glyphs[self.index]
        self._photo = _mask_to_photo(self.mask, glyph, self._scale, self.theme)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.prompt.config(
            text=f"Character {self.index + 1} of {len(self.glyphs)}  ·  "
            f"{glyph.width}×{glyph.height} px"
        )
        self.entry.delete(0, "end")
        self.back_button.set_enabled(self.index > 0)
        self.update_idletasks()
        self._paint_progress()

    # ------------------------------------------------------------------
    @staticmethod
    def _same_glyph(a: Glyph, b: Glyph) -> bool:
        """Whether two blobs from different binarisations are the same mark."""
        overlap_x = min(a.right, b.right) - max(a.x, b.x)
        overlap_y = min(a.bottom, b.bottom) - max(a.y, b.y)
        if overlap_x <= 0 or overlap_y <= 0:
            return False
        inter = overlap_x * overlap_y
        smaller = min(a.width * a.height, b.width * b.height)
        return inter >= 0.55 * smaller

    def _accept(self, _event: tk.Event | None = None) -> None:
        label = self.entry.get().strip()
        if not label:
            return
        if len(label) > 1:
            messagebox.showinfo(
                "One character", "Type exactly one character.", parent=self
            )
            return
        before = len(self.glyph_set)
        primary = self.glyphs[self.index]
        # One group for everything the user trains: decoding commits to a
        # single group per line, and the user's own font is one font.
        self.glyph_set.add(label, primary, group=USER_GROUP)
        for blobs in self.alternates:
            for candidate in blobs:
                if self._same_glyph(primary, candidate):
                    self.glyph_set.add(label, candidate, group=USER_GROUP)
                    break
        self._added.append(len(self.glyph_set) - before)
        self.index += 1
        self._show()

    def _skip(self) -> None:
        self._added.append(0)
        self.index += 1
        self._show()

    def _back(self) -> None:
        if self.index == 0:
            return
        self.index -= 1
        added = self._added.pop() if self._added else 0
        if added:
            del self.glyph_set.templates[-added:]
            self.glyph_set._vectors = None
        self._show()

    #: Everything the readout can contain. Recognition commits to one template
    #: group per line, so a character missing from the user's own group is not
    #: rescued by the bundled bank — it comes back as "?" and invalidates the
    #: number. One chat line only ever shows some of the digits, which is why
    #: the gap is named here rather than left to be discovered in the field.
    NEEDED = "0123456789xy"

    def _finish(self) -> None:
        if len(self.glyph_set):
            self.glyph_set.save(self.glyph_path)
            self.saved = True
            known = self.glyph_set.labels
            missing = [character for character in self.NEEDED if character not in known]
            advice = (
                "Done: every digit and both labels are covered."
                if not missing
                else "Still missing: " + " ".join(missing) + ".\n\n"
                "Quickest way to cover them in one go: open the chat and\n"
                "type " + "".join(missing) + " into the input — it is the same\n"
                "font. Then Area over that line and Train again.\n"
                "Templates accumulate, nothing is lost."
            )
            messagebox.showinfo(
                "Saved",
                f"Templates trained: {len(self.glyph_set)}\n"
                f"Characters: {' '.join(sorted(known))}\n\n{advice}",
                parent=self,
            )
        self.destroy()


def train_from_crop(parent: tk.Misc, crop: np.ndarray, user_dir: Path, theme: Theme) -> bool:
    dialog = TrainerDialog(parent, crop, user_dir / GLYPH_FILE, theme)
    parent.wait_window(dialog)
    return getattr(dialog, "saved", False)
