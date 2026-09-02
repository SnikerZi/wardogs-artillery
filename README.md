# WARDOGS Artillery Calculator

A portable Windows overlay for WARDOGS. Press a hotkey and it reads the map
coordinates off your screen, then shows the range, azimuth and the elevation
in mils to dial on the gun.

Separate process, no injection into the game: it reads the screen and listens
for a global hotkey, the same things OBS does.

## Install

1. Download `wardogs-calc.exe` from [Releases](../../releases/latest).
2. Run it. That's it — no installer, no Python, no admin rights.

Settings live in a `config.json` next to the exe, so the whole thing stays
portable: copy the exe anywhere, delete it and nothing is left behind.

## Use

1. In game, point at a spot on the map and press your mark-coordinates key.
   The chat opens with a line like `x98.49, y110.30` in the input field.
2. **F1** — gun position, **F2** — target, **F3** — clear both.

The line stays in the chat until the message is sent, so there is no rush.
Each press overwrites its own point: to re-aim, mark a new spot and press
**F2** again — nothing needs resetting.

The big number is the **elevation in mils** — what you dial on the weapon.
The SPH-2 has two arcs, so it shows two. Range and azimuth sit below: those
you check, not enter. If an arc cannot reach, it says why instead of inventing
a number.

If the coordinates do not read on your setup, press **Area** once and drag a
box around the chat line. Font templates for the HUD are already built in;
**Train** is only there if your resolution renders them differently.

## Settings

The `⚙` button opens five sections: **Hotkeys**, **Coordinates** (strictness,
readout area, font training), **Screen capture**, **Appearance** (theme, five
accents, opacity, 0.8–1.6× scale, always-on-top) and **Diagnostics**.
Everything applies immediately.

**Rebinding:** click the button showing the current key and press what you
want. Mouse buttons work too — side, middle, right, wheel. Hold
`Ctrl`/`Shift`/`Alt`/`Win` for a combination, `Esc` cancels. The left button
is reserved, since that is how you operate the app.

**Why function keys:** the chat input holds keyboard focus while the line is
being read, so a digit or letter would be typed straight into it. The app
flags a hotkey that does this. Avoid `F12` (Steam screenshot) and
`Shift+Tab` (Steam overlay); if F1–F3 are taken in game, F9–F11 are usually
free.

## How it works

A map position is an X/Y pair on a `0 … 163.84` scale spanning the full
16.384 × 16.384 km terrain, so one unit is 100 m and `0.01` is one metre.

```
dx = (x2 - x1) * 100                       # metres
dy = (y2 - y1) * 100
range     = hypot(dx, dy)
azimuth   = atan2(dx, dy)                  # 0° = north (+Y), clockwise
mil       = azimuth * 6400 / 360           # NATO mils
elevation = interpolate(firing table, range, weapon, arc)
```

| Weapon | Working range | Elevation | Arcs |
|---|---|---|---|
| Mortar (L81) | 132 – 684 m | 850 → 150 mil | one high angle, 84 points |
| SPH-2 | 780 – 2629 m | low 20 → 600 mil, high 1390 → 620 mil | low arc from 1181 m (59 points), high 80 points |

Elevation figures are community measurements — nothing official was ever
published, so re-check them after a game patch and edit
[`firing_tables.json`](src/wardogs_calc/firing_tables.json) to suit. The
format is `[range_m, mil]`, in any order.

### Reading the coordinates

The readout area is captured, binarised at five thresholds, split into
connected blobs, matched against a bank of glyph templates and parsed. The
whole read takes about 20 ms. Recognition anchors on the `x` and `y` labels,
so clutter beside the line does not matter, and a miss is retried once on a
box 2.2× wider around the same centre.

Template matching rather than a general OCR engine: the HUD font is fixed, it
keeps 50 MB of third-party binaries out of the exe, and a font that reads
badly can be retrained in a dozen keystrokes.

Five guards stand between a bad crop and a wrong firing solution:

1. **Both `x` and `y` labels are required.** No "take the first two numbers"
   fallback — that would silently swap the axes one day, which is plausible,
   in range and completely wrong.
2. **Each axis is bounded separately.** The playable strip is much narrower
   than the terrain, so a clipped `y99.07` read as `y9.07` lands off the map
   and is thrown away.
3. **Binarisation thresholds must agree.** Taking the first threshold that
   merely parses is unsafe: an aggressive one clips the tail off a 9, reads it
   as a 0 and hands back a well-formed wrong number. Mangling thresholds
   disagree with each other; correct ones agree.
4. **An unrecognised glyph comes back as `?`** and invalidates any number it
   touches — `x83.?2` never degrades into `83`.
5. **The second point cannot equal the first.** The chat line stays put until
   sent, so a press without a fresh mark would otherwise report a confident
   range of zero.

When a read fails the stored point is left alone and the status line says what
to try next.

### Screen capture

| Backend | When it is needed |
|---|---|
| `bitblt` (mss) | windowed and borderless fullscreen |
| `dxgi` (dxcam) | exclusive fullscreen, where GDI returns a black frame |
| `auto` | starts on bitblt, switches to dxgi once if a frame comes back blank |

`dxcam` is optional. Without it, exclusive fullscreen reports a blank frame
and suggests switching the game to borderless.

## If something does not work

1. **Hotkeys do not respond in game.** A low-level hook gets no input from a
   process running at a higher integrity level. If the game runs as
   administrator, run this as administrator too. Current rights are shown
   under Diagnostics.
2. **The hotkey is a printable key** — it types into the chat line. Use a
   function key.
3. **Same point for gun and target.** The chat line did not change; mark a new
   spot. The app catches this and says so.
4. **Capture returns a black frame.** Switch the backend to DXGI, or put the
   game in borderless windowed mode.
5. **Coordinates do not read.** Press **Area** and select the line. If that
   does not help, check `debug/wardogs.log` and turn on "Save snapshots" to
   see exactly what lands in the area.

The log is always written to `debug/wardogs.log` next to the exe and rolls
over at 512 KB. It shows whether the hotkey fired at all, which area was used,
what each threshold found, what was recognised and how long it took:

```
start: screen (2560, 1440), standard user, config ...\config.json
  hotkeys f1 / f2 / f3, weapon mortar
  font: 135 templates in ['wardogs'], characters .0123456789xy, margin 0.05
  area (358, 475, 665, 187), saved None, default [0.14, ...]
hotkey: gun
read: area (358, 475, 665, 187) - default, screen (2560, 1440)
  read X 98.49  Y 110.30 in 21 ms (text 'x98.49, y110.30', font wardogs, ...)
```

No `hotkey:` line at all means the press never reached the app — that is a
rights or binding problem, not a recognition one.

## Building from source

```bash
run.bat      # run from source
build.bat    # produce dist\wardogs-calc.exe (~30 MB)
py -3 -m pytest tests/ -q
```

```
main.py                     entry point, also PyInstaller's
src/wardogs_calc/
  ballistics.py             range, azimuth, mils, firing tables
  firing_tables.json        the tables (edit here)
  capture.py                screen capture, two backends
  hotkeys.py                pass-through WH_KEYBOARD_LL / WH_MOUSE_LL hooks
  reader.py                 capture + recognition
  config.py                 portable config.json
  vision/                   binarisation, segmentation, glyph matching
  ui/                       window, settings, theme, widgets, trainer
tests/                      165 tests
```

## Limits

- Height difference between gun and target is ignored: the game does not show
  it and the tables are keyed on horizontal range.
- The window is frameless, so it does not appear in the taskbar or Alt+Tab.
  Collapse it with `▴`.
- The left mouse button cannot be bound; it operates the app itself.

## Licence

MIT — see [`LICENSE`](LICENSE). Not affiliated with or endorsed by BULKHEAD or
the WARDOGS development team.
