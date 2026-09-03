"""Builds the real window and exercises the guards that live in the UI.

The rest of the suite never constructs a widget, so a layout mistake or a
stale attribute would otherwise surface only when the app is launched. Skipped
where Tk cannot open a display.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

tk = pytest.importorskip("tkinter")

from wardogs_calc.ballistics import Point  # noqa: E402
from wardogs_calc.config import Config  # noqa: E402
from wardogs_calc.reader import ReadResult  # noqa: E402
from wardogs_calc.ui.widgets import ChipRow, ScrollArea  # noqa: E402
from wardogs_calc.vision.ocr import Reading  # noqa: E402


@pytest.fixture(scope="module")
def window():
    """One window for the module.

    Tk does not take kindly to a fresh root per test — building and tearing
    down four in a row skips at random — so the window is shared and each test
    puts back what it changed.
    """
    from wardogs_calc.ui.app import App

    config = Config()
    config.save = lambda *a, **k: None  # keep the test off the user's config
    config.always_on_top = False
    config.window_pos = (60, 60)
    try:
        app = App(config)
    except tk.TclError as exc:
        pytest.skip(f"no display for Tk: {exc}")
    yield app
    app.listener.stop()
    app.destroy()


@pytest.fixture
def app(window):
    pristine = window.reader.read
    yield window
    window.reader.read = pristine
    window.gun = window.target = None
    window.cfg.hotkey_reset = Config().hotkey_reset
    window.cfg.readout_region = None
    for name in vars(Config()):
        if name.startswith("hud_show_"):
            setattr(window.cfg, name, True)
    if window.cfg.hud:
        window._toggle_hud()
    if window.page != "main":
        window._toggle_settings()
    window.update()


def test_both_pages_build(app):
    app.update()
    app._toggle_settings()
    app.update()
    assert app.page == "settings"
    app._toggle_settings()
    app.update()
    assert app.page == "main"


def test_a_fresh_start_reports_the_hotkeys(app):
    app.update()
    text, tone = app._status
    assert tone == "muted"
    assert "F1" in text and "F2" in text


def test_a_hotkey_can_be_pressed_again_to_update_its_point(app):
    first = Reading(Point(98.49, 110.32), "x98.49, y110.32", 1.0)
    second = Reading(Point(101.20, 112.75), "x101.20, y112.75", 1.0)

    app.reader.read = lambda: ReadResult(first, None, None, 12.0)
    app._capture("target")
    assert app.target == Point(98.49, 110.32)

    app.reader.read = lambda: ReadResult(second, None, None, 12.0)
    app._capture("target")
    assert app.target == Point(101.20, 112.75)
    assert app._status[1] == "accent"


def test_an_unchanged_chat_line_is_stored_but_called_out(app):
    """The line sits in the chat until sent, so it can be read twice.

    The point is still taken — it is what the screen says — but gun and target
    landing on one spot would otherwise show as a confident range of zero.
    """
    reading = Reading(Point(98.49, 110.32), "x98.49, y110.32", 1.0)
    app.reader.read = lambda: ReadResult(reading, None, None, 12.0)

    app._capture("gun")
    assert app._status[1] == "accent"

    app._capture("target")
    assert app.target == Point(98.49, 110.32)
    assert app._status[1] == "warn"
    assert "same as the other point" in app._status[0]


def test_a_carried_over_digit_hotkey_is_called_out(app):
    # What the user's own config held: a digit that now lands in the chat line.
    app.cfg.hotkey_reset = "3"
    app._announce_readiness()
    text, tone = app._status
    assert tone == "danger"
    assert "3" in text


# --- picking the region ----------------------------------------------------
# The box can be right while the reading still fails: on a font the bundled
# bank has never seen, single glyphs come back as "?" and one "?" invalidates
# the number. Refusing the box then leaves no way forward, because training
# the font reads through that very box.


def _pick(app, monkeypatch, box, holds_readout):
    import wardogs_calc.ui.app as app_module

    monkeypatch.setattr(app_module, "select_region", lambda *a, **k: box)
    monkeypatch.setattr(type(app), "_holds_readout", lambda self: holds_readout)
    app.reader.read = lambda: ReadResult(None, "no coordinates found", None, 50.0)
    app._pick_region()


def test_a_box_showing_the_readout_is_kept_when_the_font_reads_badly(app, monkeypatch):
    _pick(app, monkeypatch, (10, 20, 200, 60), holds_readout=True)
    assert app.cfg.readout_region == [10, 20, 200, 60]
    assert app._status[1] == "warn"
    assert "Train" in app._status[0]


def test_a_box_with_no_text_at_all_is_refused(app, monkeypatch):
    _pick(app, monkeypatch, (10, 20, 200, 60), holds_readout=False)
    assert app.cfg.readout_region is None
    assert app._status[1] == "warn"


def test_a_box_that_reads_is_kept_without_complaint(app, monkeypatch):
    import wardogs_calc.ui.app as app_module

    monkeypatch.setattr(app_module, "select_region", lambda *a, **k: (10, 20, 200, 60))
    reading = Reading(Point(98.49, 110.30), "x98.49, y110.30", 1.0)
    app.reader.read = lambda: ReadResult(reading, None, None, 50.0)
    app._pick_region()
    assert app.cfg.readout_region == [10, 20, 200, 60]
    assert app._status[1] == "accent"


# --- the height correction in the window -----------------------------------


def test_the_meta_line_reports_the_height_difference(app):
    app.gun = Point(60.0, 60.0)
    app.target = Point(70.0, 70.0)
    app.cfg.terrain_correction = True
    app.cfg.terrain_map = "bakurani"
    app._refresh()
    app.update()
    text = app.meta_label.cget("text")
    assert "above the gun" in text or "below the gun" in text
    assert "azimuth" in text


def test_turning_the_correction_off_drops_the_height_line(app):
    app.gun = Point(60.0, 60.0)
    app.target = Point(70.0, 70.0)
    app.cfg.terrain_correction = False
    app._refresh()
    app.update()
    text = app.meta_label.cget("text")
    assert "the gun" not in text
    app.cfg.terrain_correction = True


def test_a_point_off_the_chosen_map_says_which_and_offers_the_setting(app):
    """Ozeti does not reach X 25, so picking it there has to be visible."""
    app.gun = Point(25.0, 60.0)
    app.target = Point(30.0, 60.0)
    app.cfg.terrain_correction = True
    app.cfg.terrain_map = "ozeti"
    app._refresh()
    app.update()
    text = app.meta_label.cget("text")
    assert "outside Ozeti" in text
    assert "⚙" in text
    app.cfg.terrain_map = "bakurani"


def test_the_dialled_range_is_shown_when_the_slope_moves_it(app):
    app.gun = Point(60.0, 60.0)
    app.target = Point(70.0, 70.0)
    app.cfg.terrain_correction = True
    app.cfg.terrain_map = "bakurani"
    app.cfg.weapon = "sph2"
    app._refresh()
    app.update()
    labels = _all_text(app.solution_frame)
    assert any("dialled as" in text for text in labels)
    app.cfg.weapon = Config().weapon


def test_the_terrain_switch_is_in_the_settings_page(app):
    app._toggle_settings()
    app.update()
    assert any("Height correction" in text for text in _all_text(app))
    assert any("TERRAIN" in text for text in _all_text(app))


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def _all_text(widget):
    out = []
    for child in widget.winfo_children():
        try:
            out.append(str(child.cget("text")))
        except tk.TclError:
            pass
        out.extend(_all_text(child))
    return out


def test_escape_on_the_main_page_after_visiting_settings(app):
    """Esc cancels a recording; it used to reach for the destroyed recorders."""
    app._toggle_settings()
    app.update()
    app._toggle_settings()
    app.update()
    app._cancel_recording()


# --- the transparent overlay -----------------------------------------------


def _hud(app):
    """Switch to the overlay with a solved mission in it."""
    app.gun = Point(60.0, 60.0)
    app.target = Point(63.0, 62.0)
    if not app.cfg.hud:
        app._toggle_hud()
    app._refresh()
    app.update()
    return app.hud


def test_the_overlay_is_a_fraction_of_the_panel(app):
    app._refresh()
    app.update()
    panel = (app.winfo_width(), app.winfo_height())
    hud = _hud(app).wanted()
    assert hud[0] < panel[0] / 2 and hud[1] < panel[1] / 3


def test_the_overlay_shows_the_elevation_and_the_measurements(app):
    _hud(app)
    mission = app._mission()[0]
    figure = f"{mission.solutions[0].elevation_mil:.0f}"
    assert any(figure == value for _label, value, _unit, _note in app._hud_cells(mission))
    assert "m" in app._hud_meta(mission, app._mission()[1])


def test_the_measurements_move_up_when_the_elevation_is_off(app):
    """Range and azimuth alone are a legitimate -- and shorter -- overlay."""
    tall = _hud(app).wanted()
    app.cfg.hud_show_elevation = False
    app.cfg.hud_show_height = False
    app._refresh()
    app.update()
    short = app.hud.wanted()
    assert short[1] < tall[1]

    mission = app._mission()[0]
    values = [value for _label, value, _unit, _note in app._hud_cells(mission)]
    assert any(value and "." in value for value in values)  # the azimuth
    assert app._hud_meta(mission, app._mission()[1]) == ""  # nothing left over


def test_an_out_of_range_shot_stays_narrow(app):
    """The panel's note is a sentence; here it has to be two words."""
    app.gun = Point(60.0, 60.0)
    app.target = Point(140.0, 140.0)
    if not app.cfg.hud:
        app._toggle_hud()
    app._refresh()
    app.update()
    mission = app._mission()[0]
    notes = [note for _l, value, _u, note in app._hud_cells(mission) if value is None]
    assert notes and all(len(note) <= 12 for note in notes), notes
    assert app.hud.wanted()[0] < 320


def test_a_warning_reaches_the_overlay(app):
    """There is no status label to read it off, so it is painted in."""
    quiet = _hud(app).wanted()
    app._set_status("no coordinates found in the selected area", "warn")
    app.update()
    assert app.hud.wanted()[1] > quiet[1]
    app._set_status("", "muted")


def test_the_overlay_hotkey_brings_the_panel_back(app):
    _hud(app)
    assert app.hud is not None
    app._toggle_hud()
    app.update()
    assert app.hud is None
    assert app.attributes("-transparentcolor") in ("", "{}")
    assert any("ELEVATION" in text for text in _all_text(app))


def test_switching_modes_leaves_the_window_where_it_was(app):
    """Both modes share one position.

    Giving the overlay its own meant it jumped to a corner on every switch:
    with clicks passing through it cannot be dragged, so the position it
    remembered was never one anybody had chosen.
    """
    app.cfg.window_pos = (300, 200)
    app._place()
    app.update()
    assert (app.winfo_x(), app.winfo_y()) == (300, 200)

    _hud(app)
    assert (app.winfo_x(), app.winfo_y()) == (300, 200)
    assert app.hud.wanted()[0] == app.winfo_width()  # only the size changed

    app._toggle_hud()
    app.update()
    assert (app.winfo_x(), app.winfo_y()) == (300, 200)


def test_the_overlay_settings_are_on_the_settings_page(app):
    app._toggle_settings()
    app.update()
    text = _all_text(app)
    assert any("Transparent overlay" in line for line in text)
    assert any("Clicks pass through" in line for line in text)
    assert any("Text size" in line for line in text)
    # The fields are chips on a canvas, so they are not in the label text.
    chips = next(c for c in _descendants(app) if isinstance(c, ChipRow))
    assert [label for _key, label in chips._chips] == [
        "Elevation", "Range", "Azimuth", "Height"
    ]
    # Proportional cells: an equal share clips the longest label.
    edges = chips._bounds()
    assert edges[0][1] - edges[0][0] > edges[1][1] - edges[1][0]


def test_the_text_size_shrinks_the_whole_overlay(app):
    """One slider, because "smaller" is the request an overlay attracts."""
    _hud(app)
    before = app.hud.wanted()
    app._on_hud_size(0.6)
    app.update()
    after = app.hud.wanted()
    assert after[0] < before[0] and after[1] < before[1]
    app._on_hud_size(1.0)


def test_one_line_trades_height_for_width(app):
    _hud(app)
    two_rows = app.hud.wanted()
    app._on_hud_one_line(True)
    app.update()
    one_row = app.hud.wanted()
    assert one_row[1] < two_rows[1]
    assert one_row[0] > two_rows[0]
    app._on_hud_one_line(False)


def test_the_settings_page_fits_more_than_a_quarter_of_itself(app):
    """The overlay switches were buried in a 440 px viewport."""
    app._toggle_settings()
    app.update()
    app.update_idletasks()
    area = next(c for c in _descendants(app) if isinstance(c, ScrollArea))
    assert area.canvas.winfo_height() >= 380
    # And the fields are above the fold now, not 70% of the way down.
    chips = next(c for c in _descendants(app) if isinstance(c, ChipRow))
    offset = chips.winfo_rooty() - area.body.winfo_rooty()
    assert offset < area.canvas.winfo_height()
