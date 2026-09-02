"""Colour palettes and font scaling for the UI.

Tk has no styling system worth the name, so everything downstream reads its
colours and fonts from a single Theme object.  Changing theme rebuilds the
window rather than trying to re-tint widgets in place — far fewer places to
forget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Accent colours the user can pick, keyed by the name stored in config.json.
ACCENTS: dict[str, tuple[str, str]] = {
    # key: (label, hex)
    "green": ("Green", "#4ade80"),
    "blue": ("Blue", "#38bdf8"),
    "amber": ("Amber", "#fbbf24"),
    "violet": ("Violet", "#a78bfa"),
    "rose": ("Rose", "#fb7185"),
}

DEFAULT_ACCENT = "green"

_MONO_CANDIDATES = ("JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New")
_SANS_CANDIDATES = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Arial")


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface_hi: str
    border: str
    text: str
    muted: str
    faint: str
    danger: str
    warn: str
    shadow: str


DARK = Palette(
    bg="#0b0e14",
    surface="#131822",
    surface_hi="#1c2331",
    border="#242c3b",
    text="#e7edf5",
    muted="#8794a8",
    faint="#5a6577",
    danger="#f87171",
    warn="#fbbf24",
    shadow="#05070b",
)

LIGHT = Palette(
    bg="#eef1f6",
    surface="#ffffff",
    surface_hi="#f3f5f9",
    border="#dbe1ea",
    text="#0f172a",
    muted="#5c6a7f",
    faint="#94a3b8",
    danger="#dc2626",
    warn="#b45309",
    shadow="#c8cfda",
)


@dataclass
class Theme:
    dark: bool = True
    accent_key: str = DEFAULT_ACCENT
    scale: float = 1.0
    _mono: str | None = field(default=None, repr=False)
    _sans: str | None = field(default=None, repr=False)

    @property
    def palette(self) -> Palette:
        return DARK if self.dark else LIGHT

    @property
    def accent(self) -> str:
        return ACCENTS.get(self.accent_key, ACCENTS[DEFAULT_ACCENT])[1]

    @property
    def accent_text(self) -> str:
        """Readable ink on top of a filled accent surface."""
        return "#08131f" if self.dark else "#ffffff"

    # Palette passthroughs keep call sites short: theme.bg, theme.text, ...
    def __getattr__(self, name: str) -> str:
        try:
            return getattr(DARK if self.__dict__.get("dark", True) else LIGHT, name)
        except AttributeError as exc:
            raise AttributeError(name) from exc

    # -- fonts ----------------------------------------------------------
    def _resolve(self, candidates: tuple[str, ...], cache_key: str) -> str:
        cached = self.__dict__.get(cache_key)
        if cached:
            return cached
        chosen = candidates[-1]
        try:
            from tkinter import font as tkfont

            available = {name.lower() for name in tkfont.families()}
            for name in candidates:
                if name.lower() in available:
                    chosen = name
                    break
        except Exception:
            pass
        self.__dict__[cache_key] = chosen
        return chosen

    def sans(self, size: int, weight: str = "normal") -> tuple:
        return (self._resolve(_SANS_CANDIDATES, "_sans"), self.px(size), weight)

    def mono(self, size: int, weight: str = "normal") -> tuple:
        return (self._resolve(_MONO_CANDIDATES, "_mono"), self.px(size), weight)

    def px(self, value: float) -> int:
        """Scale a pixel or point measurement, never below 1."""
        return max(1, int(round(value * self.scale)))


def mix(a: str, b: str, t: float) -> str:
    """Blend two ``#rrggbb`` colours; ``t=0`` gives ``a``, ``t=1`` gives ``b``."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = (int(a[i : i + 2], 16) for i in (1, 3, 5))
    br, bg_, bb = (int(b[i : i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(
        round(ar + (br - ar) * t),
        round(ag + (bg_ - ag) * t),
        round(ab + (bb - ab) * t),
    )
