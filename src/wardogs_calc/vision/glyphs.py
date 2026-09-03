"""Glyph templates and matching.

The HUD font is fixed, so template matching beats a general OCR engine here:
it is exact on digits, needs no bundled binary, and trains from a single
screenshot.  Templates are stored as plain 0/1 text rows in JSON so they stay
diffable and need no image library at runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .segment import Glyph

#: Every template is resampled onto this grid before comparison.
CELL = 20

GLYPH_FILE = "glyphs.json"

#: A match below this raw score is noise rather than a character.
MIN_SCORE = 0.35

#: ...and it must beat the next-best label by this fraction to be trusted.
#: The bank holds one real font, so the runner-up is a *different digit* of
#: that font rather than the same digit of another face, and the natural gap
#: is small: an exact 8 beats 0 by 0.075, a 9 beats 3 by 0.13. Agreement
#: between binarisation thresholds is the other half of the guard.
MIN_MARGIN = 0.05

#: Labels whose glyph is inherently short — a full stop, a comma. Everything
#: else stands at roughly the line height.
SHORT_LABELS = frozenset({".", ","})

#: Height, in screen pixels, of the digits the bundled bank was captured from.
#: Recovered from the stored templates by inverting the nearest-neighbour
#: resampling in :func:`normalise`, which is exact while the source is smaller
#: than CELL: digits came in at 13x9, "1" at 13x3..5, "x" at 10x8, "y" at 14x8
#: and "." at 2x2.
#:
#: Matching itself is scale-free — every template is resampled onto the same
#: square — but only up to a point, and the point is measurable: the same
#: readout rendered at 13 px reads 31 times out of 40, at 11 px 27, and at
#: 10 px not once. So this number is worth reporting next to the size actually
#: on screen, because a screen drawing the HUD smaller needs the font trained
#: rather than anything else adjusted.
BUNDLED_HEIGHT_PX = 13


#: A glyph this much shorter than its line can only be a short label.
#: Normalising to a square throws absolute size away, which lets a stray
#: fragment of a digit match a full stop perfectly; height puts it back.
SHORT_MAX_HEIGHT = 0.45
TALL_MIN_HEIGHT = 0.55


def canonical_label(label: str) -> str:
    """Fold labels that no amount of matching can tell apart.

    A comma and a full stop are the same handful of pixels once resampled onto
    a square grid. Measured on a real trained font, a stop scored identically
    against both, so the two cancelled each other out and the decimal point
    started coming back as "?" — which makes the whole number unparseable.
    Both are separators to the parser, so they are one label here.
    """
    return "." if label == "," else label


def normalise(mask: np.ndarray, cell: int | None = None) -> np.ndarray:
    """Resample a glyph bitmap onto a fixed square grid (nearest neighbour).

    ``cell`` is read at call time rather than bound as a default so the grid
    size stays tunable from one place.
    """
    cell = CELL if cell is None else cell
    h, w = mask.shape
    if h == 0 or w == 0:
        return np.zeros((cell, cell), dtype=np.float32)
    ys = np.minimum((np.arange(cell) * h) // cell, h - 1)
    xs = np.minimum((np.arange(cell) * w) // cell, w - 1)
    return mask[np.ix_(ys, xs)].astype(np.float32)


def _zero_mean_unit(v: np.ndarray) -> np.ndarray:
    v = v - v.mean()
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 1e-6 else v


#: Group name for templates the user trained in-app.
USER_GROUP = "user"


@dataclass
class Template:
    label: str
    grid: np.ndarray  # float32 (CELL, CELL)
    aspect: float  # width / height of the original bitmap
    #: Which face this came from. A readout is drawn in ONE font, so decoding
    #: commits to one group per line instead of letting every face vote.
    group: str = ""

    @property
    def vector(self) -> np.ndarray:
        return _zero_mean_unit(self.grid.reshape(-1))


@dataclass(frozen=True)
class GlyphMatch:
    label: str
    score: float
    runner_up: float
    #: Threshold this match was judged against; see GlyphSet.min_margin.
    min_margin: float = MIN_MARGIN

    @property
    def margin(self) -> float:
        """How decisively the winner beat the next label, in ``[0, 1]``."""
        if self.score <= 0.0:
            return 0.0
        return max(0.0, 1.0 - self.runner_up / self.score)

    @property
    def confident(self) -> bool:
        return self.score >= MIN_SCORE and self.margin >= self.min_margin


class GlyphSet:
    """A trained alphabet: label -> one or more example bitmaps."""

    def __init__(
        self,
        templates: list[Template] | None = None,
        min_margin: float = MIN_MARGIN,
    ) -> None:
        self.templates: list[Template] = templates or []
        self._vectors: np.ndarray | None = None
        #: How decisively a template must beat the next label to be trusted.
        #: Higher refuses more and misreads less; the shipped default is
        #: deliberately strict, because a wrong range is worse than no range.
        self.min_margin = min_margin

    def __len__(self) -> int:
        return len(self.templates)

    @property
    def labels(self) -> set[str]:
        return {t.label for t in self.templates}

    def add(self, label: str, glyph: Glyph, group: str = USER_GROUP) -> None:
        aspect = glyph.width / glyph.height if glyph.height else 1.0
        self.templates.append(
            Template(canonical_label(label), normalise(glyph.mask), aspect, group)
        )
        self._invalidate()

    def _invalidate(self) -> None:
        self._vectors = None
        for key in ("_index_cache", "_label_cache"):
            self.__dict__.pop(key, None)

    def set_min_margin(self, value: float) -> None:
        self.min_margin = max(0.0, min(0.9, value))

    def extend(self, other: "GlyphSet") -> None:
        """Absorb another set's templates, keeping their group tags."""
        self.templates.extend(other.templates)
        self._invalidate()

    @property
    def groups(self) -> list[str]:
        """Every distinct template group."""
        return list(self._index()["groups"])

    def _index(self) -> dict:
        """Cached numeric view of the bank.

        Everything here used to be recomputed per glyph as Python loops and
        object-dtype comparisons over thousands of templates, which cost more
        than the actual matching.
        """
        cached = self.__dict__.get("_index_cache")
        if cached is not None and cached["size"] == len(self.templates):
            return cached

        # Sorting by group lets the per-group maximum be a plain slice max
        # instead of a boolean mask over the whole bank.
        self.templates.sort(key=lambda t: t.group)
        groups: list[str] = []
        ranges: list[tuple[int, int]] = []
        for i, template in enumerate(self.templates):
            if not groups or template.group != groups[-1]:
                if ranges:
                    ranges[-1] = (ranges[-1][0], i)
                groups.append(template.group)
                ranges.append((i, len(self.templates)))
        if ranges:
            ranges[-1] = (ranges[-1][0], len(self.templates))

        labels = sorted({t.label for t in self.templates})
        code_of = {label: i for i, label in enumerate(labels)}
        cached = {
            "size": len(self.templates),
            "groups": groups,
            "ranges": ranges,
            "label_codes": np.array(
                [code_of[t.label] for t in self.templates], dtype=np.int32
            ),
            "is_short": np.array(
                [t.label in SHORT_LABELS for t in self.templates], dtype=bool
            ),
        }
        self.__dict__["_index_cache"] = cached
        self.__dict__.pop("_vectors_cache", None)
        return cached

    def _matrices(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Cached (grids, zero-mean-unit vectors, aspects) for all templates."""
        self._index()  # settles template order before the matrices are built
        if self._vectors is None:
            if not self.templates:
                empty = np.zeros((0, CELL * CELL), dtype=np.float32)
                self._vectors = (empty, empty, np.zeros(0, dtype=np.float32))
            else:
                grids = np.stack([t.grid.reshape(-1) for t in self.templates])
                vectors = np.stack([t.vector for t in self.templates])
                aspects = np.array([t.aspect for t in self.templates], dtype=np.float32)
                self._vectors = (grids, vectors, aspects)
        return self._vectors

    def _scores(self, glyph: Glyph, height_ratio: float | None) -> np.ndarray:
        """Similarity of ``glyph`` to every template, zeroed where implausible."""
        grids, vectors, aspects = self._matrices()
        probe_grid = normalise(glyph.mask).reshape(-1)
        probe_vec = _zero_mean_unit(probe_grid)

        intersection = grids @ probe_grid
        union = np.maximum(grids.sum(axis=1) + probe_grid.sum() - intersection, 1e-6)
        jaccard = intersection / union

        correlation = np.clip(vectors @ probe_vec, 0.0, 1.0)
        probe_flat = float(np.linalg.norm(probe_vec)) < 1e-6
        template_flat = np.linalg.norm(vectors, axis=1) < 1e-6
        # Where correlation is meaningless, lean on overlap alone.
        degenerate = template_flat | probe_flat
        base = np.where(degenerate, jaccard, 0.55 * jaccard + 0.45 * correlation)

        aspect = glyph.width / glyph.height if glyph.height else 1.0
        penalty = np.minimum(aspect, aspects) / np.maximum(np.maximum(aspect, aspects), 1e-6)
        scores = base * (0.65 + 0.35 * penalty)

        if height_ratio is not None:
            allowed = self._height_allows(height_ratio)
            if allowed is not None:
                scores = np.where(allowed, scores, 0.0)
        return scores

    def _best(self, scores: np.ndarray, mask: np.ndarray | None = None) -> "GlyphMatch":
        if mask is not None:
            scores = np.where(mask, scores, 0.0)
        best = int(np.argmax(scores))
        if scores[best] <= 0.0:
            return GlyphMatch("", 0.0, 0.0, self.min_margin)
        codes = self._index()["label_codes"]
        others = scores[codes != codes[best]]
        return GlyphMatch(
            self.templates[best].label,
            float(scores[best]),
            float(others.max()) if others.size else 0.0,
            self.min_margin,
        )

    def decode(
        self, line: list[Glyph], height_ratios: list[float]
    ) -> tuple[str, list["GlyphMatch"]]:
        """Read one text line. See :meth:`decode_lines`."""
        group, per_line = self.decode_lines([line], [height_ratios])
        return group, (per_line[0] if per_line else [])

    def decode_lines(
        self,
        lines: list[list[Glyph]],
        height_ratios: list[list[float]],
    ) -> tuple[str, list[list["GlyphMatch"]]]:
        """Read several lines, committing them all to one template group.

        A HUD draws every character of a readout in one face, so the right
        question is "which font is this?" — not "which template is this
        glyph?".  Letting every face vote per glyph drives the runner-up score
        up and makes every character look ambiguous.

        Every line in the crop is decided together rather than separately:
        more glyphs is more evidence for the same one decision.
        """
        if not self.templates or not any(lines):
            return "", [[] for _ in lines]
        index = self._index()
        ranges = index["ranges"]
        groups = index["groups"]

        scores = [
            [self._scores(glyph, ratio) for glyph, ratio in zip(line, ratios)]
            for line, ratios in zip(lines, height_ratios)
        ]

        flat = [s for line in scores for s in line]
        if len(ranges) <= 1 or not flat:
            chosen_name = groups[0] if groups else ""
            return chosen_name, [[self._best(s) for s in line] for line in scores]

        stacked = np.stack(flat)  # (glyphs, templates)
        totals = [
            float(stacked[:, start:stop].max(axis=1).sum()) for start, stop in ranges
        ]
        winner = int(np.argmax(totals))
        start, stop = ranges[winner]
        chosen = np.zeros(stacked.shape[1], dtype=bool)
        chosen[start:stop] = True
        return groups[winner], [[self._best(s, chosen) for s in line] for line in scores]

    def match(self, glyph: Glyph, height_ratio: float | None = None) -> "GlyphMatch":
        """Best label for ``glyph``, with the runner-up score for context.

        ``height_ratio`` is the glyph's height over the median height of its
        text line.  It gates short labels against tall ones, which is what
        stops a broken-up digit from matching a full stop: both normalise to a
        near-solid square, and only absolute height tells them apart.

        Correlation alone breaks on solid blobs: a full stop normalises to an
        all-ones grid whose zero-mean vector is the zero vector, so every
        correlation comes out 0.  Overlap (Jaccard) handles those, correlation
        handles the structured glyphs, and an aspect-ratio factor separates
        shapes that only differ once stretched to a square — a narrow ``1``
        from a wide ``0``.

        The absolute score is a poor confidence signal: a glyph one pixel
        narrower than its template still wins by a mile but scores ~0.6.  What
        matters is the gap to the runner-up, so that is reported too.
        """
        if not self.templates:
            return GlyphMatch("", 0.0, 0.0, self.min_margin)
        return self._best(self._scores(glyph, height_ratio))

    def _height_allows(self, height_ratio: float) -> np.ndarray | None:
        """Mask of templates whose label is plausible at this glyph height.

        None means "no restriction", which saves a full-bank array op.
        """
        is_short = self._index()["is_short"]
        if height_ratio <= SHORT_MAX_HEIGHT:
            return is_short
        if height_ratio >= TALL_MIN_HEIGHT:
            return ~is_short
        return None

    # -- persistence ----------------------------------------------------
    def to_json(self) -> str:
        payload = {
            "cell": CELL,
            "templates": [
                {
                    "label": t.label,
                    "group": t.group,
                    "aspect": round(t.aspect, 4),
                    "rows": ["".join("1" if v else "0" for v in row) for row in t.grid > 0.5],
                }
                for t in self.templates
            ],
        }
        return json.dumps(payload, indent=1, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "GlyphSet":
        payload = json.loads(text)
        templates = []
        for item in payload.get("templates", []):
            grid = np.array(
                [[c == "1" for c in row] for row in item["rows"]], dtype=np.float32
            )
            if grid.shape != (CELL, CELL):
                # A file written at another grid size still loads: resample it.
                grid = normalise(grid > 0.5)
            templates.append(
                Template(
                    canonical_label(item["label"]),
                    grid,
                    float(item.get("aspect", 1.0)),
                    item.get("group", USER_GROUP),
                )
            )
        return cls(templates)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "GlyphSet":
        if not path.exists():
            return cls()
        try:
            return cls.from_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            return cls()


def user_glyph_file(user_dir: Path) -> Path:
    """Where in-app training writes; separate from the bundled bank."""
    return user_dir / GLYPH_FILE


def bundled_glyph_file(resource_dir: Path) -> Path:
    return resource_dir / "vision" / "templates" / GLYPH_FILE


def load_glyphs(
    resource_dir: Path,
    user_dir: Path,
    min_margin: float = MIN_MARGIN,
) -> GlyphSet:
    """The bundled font bank, plus whatever the user trained on top of it.

    The two stay in separate groups on purpose. Decoding commits to one group
    per line, so a font trained on this machine's own HUD wins outright where
    it fits and never has its margin collapsed by a near-identical template
    from the other group.
    """
    glyphs = GlyphSet.load(bundled_glyph_file(resource_dir))
    trained = GlyphSet.load(user_glyph_file(user_dir))
    if len(trained):
        glyphs.extend(trained)
    glyphs.set_min_margin(min_margin)
    return glyphs
