"""Global pass-through hotkeys via low-level keyboard and mouse hooks.

RegisterHotKey would *swallow* the key, which is unacceptable when the binding
is a key the game itself uses (1/2 switch weapons).  A low-level hook sees the
key first and, by chaining to the next hook, lets it through untouched.

The same hook powers hotkey *recording*: while a capture is armed the next
real key is swallowed and reported back instead of being dispatched, so
binding a key never fires it into the game.

A parallel WH_MOUSE_LL hook makes the extra mouse buttons bindable, which is
what most people actually want for a spotting hotkey.  The left button is
deliberately not bindable: it is how the app itself is operated, so recording
would capture the very click that armed the recorder.

The hook callbacks must return fast — Windows silently removes hooks that blow
past LowLevelHooksTimeout — so they only push onto a queue that a worker
thread drains.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import threading
from typing import Callable

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012

WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_XBUTTONDOWN = 0x020B
WM_MOUSEWHEEL = 0x020A

#: Real virtual-key codes exist for the mouse buttons, so they share one
#: binding table with the keyboard. The wheel has none: 0x0E and 0x0F are
#: unassigned in the VK space and no keyboard ever produces them.
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06
VK_WHEEL_UP = 0x0E
VK_WHEEL_DOWN = 0x0F

_MOUSE_VKS = frozenset(
    {VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2, VK_WHEEL_UP, VK_WHEEL_DOWN}
)

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_ESCAPE = 0x1B

#: Keys that only ever qualify another key, never a binding on their own.
_MODIFIER_VKS = frozenset(
    {VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN}
    | set(range(0xA0, 0xA6))  # L/R shift, control, alt
)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)


def is_elevated() -> bool:
    """Whether this process runs as administrator.

    A low-level hook is never handed input from a process running at a higher
    integrity level.  So an elevated game next to a normal overlay produces
    the worst kind of failure: hotkeys that are installed, report no error,
    and never fire.  Worth telling the user rather than leaving them guessing.
    """
    TOKEN_QUERY = 0x0008
    TOKEN_ELEVATION = 20
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        return False
    try:
        elevated = wt.DWORD()
        returned = wt.DWORD()
        ok = advapi32.GetTokenInformation(
            token,
            TOKEN_ELEVATION,
            ctypes.byref(elevated),
            ctypes.sizeof(elevated),
            ctypes.byref(returned),
        )
        return bool(ok and elevated.value)
    finally:
        kernel32.CloseHandle(token)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wt.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT)
)
MOUSEPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wt.WPARAM, ctypes.POINTER(MSLLHOOKSTRUCT)
)

# The callback is typed loosely for the same reason as CallNextHookEx below:
# one call installs both HOOKPROC (keyboard) and MOUSEPROC (mouse), and a
# strict type silently rejects the mouse hook so no mouse binding ever fires.
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
# The last argument is typed loosely on purpose: the same call chains both the
# keyboard hook (KBDLLHOOKSTRUCT) and the mouse hook (MSLLHOOKSTRUCT), and a
# strict pointer type rejects one of them at every single event.
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, ctypes.c_void_p]
user32.CallNextHookEx.restype = ctypes.c_long
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


def _build_vk_table() -> dict[str, int]:
    table: dict[str, int] = {}
    for ch in "0123456789":
        table[ch] = ord(ch)
    for ch in "abcdefghijklmnopqrstuvwxyz":
        table[ch] = ord(ch.upper())
    for n in range(1, 25):
        table[f"f{n}"] = 0x70 + (n - 1)
    for n in range(10):
        table[f"numpad{n}"] = 0x60 + n
    table.update(
        {
            "space": 0x20,
            "tab": 0x09,
            "enter": 0x0D,
            "esc": 0x1B,
            "escape": 0x1B,
            "backspace": 0x08,
            "insert": 0x2D,
            "delete": 0x2E,
            "home": 0x24,
            "end": 0x23,
            "pageup": 0x21,
            "pagedown": 0x22,
            "left": 0x25,
            "up": 0x26,
            "right": 0x27,
            "down": 0x28,
            "multiply": 0x6A,
            "add": 0x6B,
            "subtract": 0x6D,
            "decimal": 0x6E,
            "divide": 0x6F,
            "`": 0xC0,
            "-": 0xBD,
            "=": 0xBB,
            "[": 0xDB,
            "]": 0xDD,
            ";": 0xBA,
            "'": 0xDE,
            ",": 0xBC,
            ".": 0xBE,
            "/": 0xBF,
            "\\": 0xDC,
        }
    )
    table.update(
        {
            "mouse_right": VK_RBUTTON,
            "mouse_middle": VK_MBUTTON,
            "mouse4": VK_XBUTTON1,
            "mouse5": VK_XBUTTON2,
            "wheel_up": VK_WHEEL_UP,
            "wheel_down": VK_WHEEL_DOWN,
        }
    )
    return table


VK_BY_NAME = _build_vk_table()
#: Ordered so a recorded combo always renders the same way.
_MODIFIERS = {"ctrl": VK_CONTROL, "shift": VK_SHIFT, "alt": VK_MENU, "win": VK_LWIN}

#: Reverse lookup for recording. Built first-wins, so aliases such as
#: "escape" never displace the canonical "esc".
NAME_BY_VK: dict[int, str] = {}
for _name, _vk in VK_BY_NAME.items():
    NAME_BY_VK.setdefault(_vk, _name)

_PRETTY = {
    "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win",
    "esc": "Esc", "space": "Space", "enter": "Enter", "tab": "Tab",
    "backspace": "Backspace", "insert": "Insert", "delete": "Delete",
    "home": "Home", "end": "End", "pageup": "PgUp", "pagedown": "PgDn",
    "left": "←", "up": "↑", "right": "→", "down": "↓",
    "multiply": "Num *", "add": "Num +", "subtract": "Num −",
    "decimal": "Num .", "divide": "Num /",
    "mouse_right": "RMB", "mouse_middle": "MMB",
    "mouse4": "Mouse 4", "mouse5": "Mouse 5",
    "wheel_up": "Wheel ↑", "wheel_down": "Wheel ↓",
}


def pretty_hotkey(spec: str) -> str:
    """Human-facing rendering of a spec: ``ctrl+f1`` -> ``Ctrl + F1``."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    out = []
    for part in parts:
        if part in _PRETTY:
            out.append(_PRETTY[part])
        elif part.startswith("numpad") and part[6:].isdigit():
            out.append(f"Num {part[6:]}")
        elif part.startswith("f") and part[1:].isdigit():
            out.append(part.upper())
        else:
            out.append(part.upper())
    return " + ".join(out) if out else "—"


#: Keys that put a character into a focused text field. Ctrl, Alt and Win
#: suppress the character, so they make these safe again; Shift does not.
_TYPES_CHARACTER = frozenset(
    set("abcdefghijklmnopqrstuvwxyz0123456789`-=[];',./\\")
    | {"space"}
    | {f"numpad{digit}" for digit in range(10)}
    | {"multiply", "add", "subtract", "decimal", "divide"}
)

#: Keys that edit or submit a focused field whatever the modifiers: Ctrl+Enter
#: still sends in most chats and Ctrl+Backspace still eats a word.
_EDITS_FIELD = frozenset({"enter", "tab", "backspace", "delete"})


def edits_text(spec: str) -> bool:
    """Whether pressing this would disturb a focused text field.

    Coordinates are read off the chat input while it holds keyboard focus, so
    such a hotkey corrupts the line it is meant to read — and Enter sends it.
    """
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        return False
    key = parts[-1]
    if key in _EDITS_FIELD:
        return True
    if key in _TYPES_CHARACTER:
        return not ({p for p in parts[:-1]} & {"ctrl", "alt", "win"})
    return False


class HotkeyError(ValueError):
    pass


def parse_hotkey(spec: str) -> tuple[int, frozenset[int]]:
    """Turn ``"ctrl+f1"`` into ``(vk, {VK_CONTROL})``."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise HotkeyError(f"empty hotkey: {spec!r}")
    mods = set()
    while parts and parts[0] in _MODIFIERS:
        mods.add(_MODIFIERS[parts.pop(0)])
    if len(parts) != 1:
        raise HotkeyError(f"could not parse hotkey: {spec!r}")
    key = parts[0]
    if key not in VK_BY_NAME:
        raise HotkeyError(f"unknown key: {key!r}")
    return VK_BY_NAME[key], frozenset(mods)


#: Only these are looked at; WM_MOUSEMOVE must be rejected cheaply.
_MOUSE_MESSAGES = frozenset(
    {WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN, WM_MOUSEWHEEL}
)


def _mouse_vk(message: int, mouse_data: int) -> int | None:
    """Virtual-key code for a mouse event, or None if it is not bindable."""
    if message == WM_RBUTTONDOWN:
        return VK_RBUTTON
    if message == WM_MBUTTONDOWN:
        return VK_MBUTTON
    if message == WM_XBUTTONDOWN:
        # Which side button is in the high word of mouseData.
        return {1: VK_XBUTTON1, 2: VK_XBUTTON2}.get((mouse_data >> 16) & 0xFFFF)
    if message == WM_MOUSEWHEEL:
        delta = (mouse_data >> 16) & 0xFFFF
        if delta == 0:
            return None
        # The wheel delta is signed in the high word.
        return VK_WHEEL_UP if delta < 0x8000 else VK_WHEEL_DOWN
    return None


def _modifier_down(vk: int) -> bool:
    if vk == VK_LWIN:  # either Windows key qualifies
        return bool(
            user32.GetAsyncKeyState(VK_LWIN) & 0x8000
            or user32.GetAsyncKeyState(VK_RWIN) & 0x8000
        )
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class HotkeyListener:
    """Runs the hook on its own thread and dispatches on a worker thread."""

    def __init__(self) -> None:
        self._bindings: dict[int, list[tuple[frozenset[int], Callable[[], None]]]] = {}
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._hook: int | None = None
        self._mouse_hook: int | None = None
        self._thread_id: int | None = None
        self._hook_thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        # Keep strong references: ctypes callbacks are collectable otherwise.
        self._proc = HOOKPROC(self._on_key)
        self._mouse_proc = MOUSEPROC(self._on_mouse)
        self._lock = threading.Lock()
        self._capture: Callable[[str | None], None] | None = None

    def bind(self, spec: str, callback: Callable[[], None]) -> None:
        vk, mods = parse_hotkey(spec)
        with self._lock:
            self._bindings.setdefault(vk, []).append((mods, callback))

    def clear(self) -> None:
        with self._lock:
            self._bindings.clear()

    # -- recording ------------------------------------------------------
    def capture_next(self, callback: Callable[[str | None], None]) -> None:
        """Swallow the next real key press and report it as a hotkey spec.

        Escape cancels and reports None. Modifier keys are ignored until a
        real key arrives, so the user can hold Ctrl+Shift and then tap F1.
        Mouse buttons other than the left one count as real keys.
        """
        with self._lock:
            self._capture = callback

    def cancel_capture(self) -> None:
        with self._lock:
            self._capture = None

    @property
    def capturing(self) -> bool:
        return self._capture is not None

    def _current_modifiers(self) -> list[str]:
        return [name for name, vk in _MODIFIERS.items() if _modifier_down(vk)]

    # -- shared dispatch ------------------------------------------------
    def _handle_press(self, vk: int) -> bool:
        """Record or dispatch a pressed key/button.

        Returns True when the event must be swallowed, which happens only
        while recording: binding a key must not also fire it at the game.
        """
        with self._lock:
            capture = self._capture
        if capture is not None:
            with self._lock:
                self._capture = None
            if vk == VK_ESCAPE or vk not in NAME_BY_VK:
                self._queue.put(lambda cb=capture: cb(None))
            else:
                spec = "+".join([*self._current_modifiers(), NAME_BY_VK[vk]])
                self._queue.put(lambda cb=capture, s=spec: cb(s))
            return True

        with self._lock:
            handlers = list(self._bindings.get(vk, ()))
        for mods, callback in handlers:
            if all(_modifier_down(m) for m in mods):
                self._queue.put(callback)
        return False

    # -- hook plumbing --------------------------------------------------
    def _on_key(self, n_code, w_param, l_param):  # noqa: ANN001 - ctypes signature
        if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            vk = l_param.contents.vkCode
            # A held modifier qualifies the next key; it is never the binding.
            if not (self.capturing and vk in _MODIFIER_VKS):
                if self._handle_press(vk):
                    return 1
        # Always chain: the key must reach the game.
        return user32.CallNextHookEx(self._hook or 0, n_code, w_param, l_param)

    def _on_mouse(self, n_code, w_param, l_param):  # noqa: ANN001 - ctypes signature
        # WM_MOUSEMOVE floods this callback, so bail on it before anything else.
        if n_code >= 0 and w_param in _MOUSE_MESSAGES:
            vk = _mouse_vk(w_param, l_param.contents.mouseData)
            if vk is not None and self._handle_press(vk):
                return 1
        return user32.CallNextHookEx(self._mouse_hook or 0, n_code, w_param, l_param)

    def _pump(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            raise ctypes.WinError(ctypes.get_last_error())
        # A missing mouse hook only costs mouse bindings, so do not make it fatal.
        self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, None, 0)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                item()
            except Exception:  # a bad handler must not kill the listener
                import traceback

                traceback.print_exc()

    def start(self) -> None:
        self._worker = threading.Thread(target=self._drain, daemon=True, name="hotkey-worker")
        self._worker.start()
        self._hook_thread = threading.Thread(target=self._pump, daemon=True, name="hotkey-hook")
        self._hook_thread.start()

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._queue.put(None)
