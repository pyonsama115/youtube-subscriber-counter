"""YouTube Subscriber Counter - lightweight desktop widget (pywebview / WebView2)."""
import ctypes
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import webview
from PIL import Image

_KEEPALIVE_TIMERS = []
_FOREGROUND_HOOKS = []
_WEBVIEW_DRAG_HOOKS = []
_MOUSE_DRAG_HOOKS = []
_DRAG_TIMERS = []
_CLOSE_HANDLERS = []
_ACCENT_CACHE = {}
_INSTANCE_MUTEX = None


def acquire_single_instance():
    """Allow one running app instance and foreground it on repeated launch."""
    global _INSTANCE_MUTEX
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p
    ]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel32.CreateMutexW(
        None, False, r"Local\YouTubeSubscriberCounter_8D56C8B1"
    )
    if not mutex:
        return True
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
        hwnd = ctypes.windll.user32.FindWindowW(
            None, "YouTube Subscriber Counter"
        )
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        kernel32.CloseHandle(mutex)
        return False
    _INSTANCE_MUTEX = mutex
    return True


def thumbnail_accent(url):
    """Return a vivid representative RGB color from a channel thumbnail."""
    if not url:
        return None
    if url in _ACCENT_CACHE:
        return _ACCENT_CACHE[url]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "yt-sub-widget"})
        with urllib.request.urlopen(req, timeout=5) as response:
            image = Image.open(io.BytesIO(response.read())).convert("RGB")
        image.thumbnail((48, 48))
        colors = image.quantize(colors=8).convert("RGB").getcolors(48 * 48) or []

        def color_score(item):
            count, (r, g, b) = item
            spread = max(r, g, b) - min(r, g, b)
            brightness = (r + g + b) / 3
            usable = 0.25 if brightness < 28 or brightness > 238 else 1
            return count * (spread + 18) * usable

        _, (r, g, b) = max(colors, key=color_score)
        # Lift very dark source colors enough to remain visible on black.
        peak = max(r, g, b)
        if peak < 115:
            scale = 115 / max(1, peak)
            r, g, b = (min(255, round(v * scale)) for v in (r, g, b))
        accent = "#%02x%02x%02x" % (r, g, b)
    except Exception:
        accent = None
    _ACCENT_CACHE[url] = accent
    return accent


def round_corners(window):
    """Ask DWM to round + antialias the window corners (Windows 11).

    Runs on the UI thread so it reads the CURRENT window handle — ShowInTaskbar
    recreates the handle and drops the corner preference, so this must be
    (re-)applied after any queued handle recreation.
    """
    def apply():
        hwnd = window.native.Handle.ToInt32()
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
    _on_ui_thread(window, apply)


def round_small_corners(window):
    """Use Windows 11's antialiased small corner style for the mini pill."""
    def apply():
        hwnd = ctypes.c_void_p(window.native.Handle.ToInt64())
        pref = ctypes.c_int(3)  # DWMWCP_ROUNDSMALL
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
        )
    _on_ui_thread(window, apply)


def _on_ui_thread(window, fn):
    """Run fn on the WinForms UI thread; never let an exception escape into .NET."""
    def safe():
        try:
            fn()
        except Exception:
            pass
    try:
        from System import Action
        form = window.native
        if form.InvokeRequired:
            form.BeginInvoke(Action(safe))
        else:
            safe()
    except Exception:
        pass


def detach_noactivate_handler(window, synchronous=False):
    """Detach pywebview's unsafe focus=False Activated callback before disposal."""
    def detach():
        try:
            form = window.native
            if not window.focus:
                form.Activated -= form.on_activated
        except Exception:
            pass

    if not synchronous:
        _on_ui_thread(window, detach)
        return
    try:
        from System import Action
        form = window.native
        if form.IsDisposed or form.Disposing:
            return
        if form.InvokeRequired:
            form.Invoke(Action(detach))
        else:
            detach()
    except Exception:
        pass


def install_unified_close_handler(window, api):
    """Route native/taskbar closes through Api.close_app exactly once."""
    def install():
        from System import Action
        from System.Windows.Forms import FormClosingEventHandler

        form = window.native

        def on_form_closing(sender, args):
            if api._closing_all:
                return
            args.Cancel = True
            try:
                form.BeginInvoke(Action(api.close_app))
            except Exception:
                pass

        handler = FormClosingEventHandler(on_form_closing)
        form.FormClosing += handler
        _CLOSE_HANDLERS.append((form, handler))

    _on_ui_thread(window, install)


def apply_window_state(window, on_top):
    """Set TopMost on the WinForms UI thread."""
    _on_ui_thread(window, lambda: setattr(window.native, "TopMost", bool(on_top)))


def install_native_drag_regions(window, api):
    """Install native drag handling for display-only areas in the WebView."""
    def install():
        form = window.native
        user32 = ctypes.windll.user32

        # WebView2 can consume pointer input before its child HWND receives a
        # normal mouse message. A low-level mouse hook reliably observes the
        # pointer over the card and moves the form from display-only regions.
        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class _MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", _POINT),
                ("mouseData", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        mouse_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p,
        )
        call_next_hook = user32.CallNextHookEx
        call_next_hook.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p
        ]
        call_next_hook.restype = ctypes.c_ssize_t
        set_window_pos = user32.SetWindowPos
        set_window_pos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]
        drag = {"active": False, "px": 0, "py": 0, "wx": 0, "wy": 0}

        @mouse_proc_type
        def mouse_proc(code, message, data):
            if code >= 0:
                info = ctypes.cast(data, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                if message == 0x0201:  # WM_LBUTTONDOWN
                    rect = _RECT()
                    user32.GetWindowRect(
                        ctypes.c_void_p(form.Handle.ToInt64()),
                        ctypes.byref(rect),
                    )
                    inside = (
                        rect.l <= info.pt.x < rect.r
                        and rect.t <= info.pt.y < rect.b
                    )
                    local_x = info.pt.x - rect.l
                    local_y = info.pt.y - rect.t
                    normal_drag = (
                        not api._settings_open and local_y >= 52
                    )
                    settings_drag = (
                        api._settings_open
                        and local_y < 34
                        and local_x < (rect.r - rect.l) - 95
                    )
                    if inside and (normal_drag or settings_drag):
                        drag.update(
                            active=True,
                            px=info.pt.x,
                            py=info.pt.y,
                            wx=rect.l,
                            wy=rect.t,
                        )
                elif message == 0x0200 and drag["active"]:  # WM_MOUSEMOVE
                    set_window_pos(
                        ctypes.c_void_p(form.Handle.ToInt64()), None,
                        drag["wx"] + info.pt.x - drag["px"],
                        drag["wy"] + info.pt.y - drag["py"],
                        0, 0, 0x0001 | 0x0004 | 0x0010,
                    )
                elif message == 0x0202:  # WM_LBUTTONUP
                    drag["active"] = False
            return call_next_hook(None, code, message, data)

        set_windows_hook = user32.SetWindowsHookExW
        set_windows_hook.argtypes = [
            ctypes.c_int, mouse_proc_type, ctypes.c_void_p, ctypes.c_uint
        ]
        set_windows_hook.restype = ctypes.c_void_p
        mouse_hook = set_windows_hook(14, mouse_proc, None, 0)  # WH_MOUSE_LL
        if mouse_hook:
            _MOUSE_DRAG_HOOKS.append((mouse_hook, mouse_proc, drag))

        # Most clicks land on WebView2's child HWND rather than the form.
        # Subclass that child so non-interactive card areas start a native
        # caption drag synchronously, without a JavaScript round trip.
        wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
        )
        get_window_long = user32.GetWindowLongPtrW
        get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_window_long.restype = ctypes.c_void_p
        set_window_long = user32.SetWindowLongPtrW
        set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        set_window_long.restype = ctypes.c_void_p
        call_window_proc = user32.CallWindowProcW
        call_window_proc.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        call_window_proc.restype = ctypes.c_ssize_t

        child_handles = []
        enum_proc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t
        )

        @enum_proc
        def collect_child(child, param):
            class_name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, class_name, len(class_name))
            if class_name.value == "Chrome_RenderWidgetHostHWND":
                child_handles.append(child)
            return True

        user32.EnumChildWindows(
            ctypes.c_void_p(form.Handle.ToInt64()), collect_child, 0
        )

        for child in child_handles:
            original = get_window_long(child, -4)  # GWLP_WNDPROC

            @wndproc_type
            def webview_wndproc(handle, message, wparam, lparam, original=original):
                if message == 0x0201 and api._main_drag_expanded:  # WM_LBUTTONDOWN
                    y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                    # The header contains the channel link and control buttons.
                    # Everything below it is display-only and safe to drag.
                    if y >= 52:
                        user32.ReleaseCapture()
                        user32.SendMessageW(
                            ctypes.c_void_p(form.Handle.ToInt64()), 0x00A1, 2, 0
                        )
                        return 0
                return call_window_proc(original, handle, message, wparam, lparam)

            set_window_long(
                child, -4, ctypes.cast(webview_wndproc, ctypes.c_void_p)
            )
            _WEBVIEW_DRAG_HOOKS.append(
                (child, original, webview_wndproc, collect_child)
            )

    _on_ui_thread(window, install)


def install_safe_drag_regions(window, api):
    """Poll mouse state on the UI thread; never install a system-wide hook."""
    def install():
        from System import EventHandler
        from System.Windows.Forms import Control, Cursor, MouseButtons, Timer

        form = window.native
        user32 = ctypes.windll.user32
        set_window_pos = user32.SetWindowPos
        set_window_pos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]
        drag = {"active": False, "px": 0, "py": 0, "wx": 0, "wy": 0}

        def tick(sender, event):
            try:
                if form.IsDisposed or form.Disposing:
                    sender.Stop()
                    return
                left_down = (
                    Control.MouseButtons & MouseButtons.Left
                ) == MouseButtons.Left
                point = Cursor.Position
                rect = _RECT()
                user32.GetWindowRect(
                    ctypes.c_void_p(form.Handle.ToInt64()), ctypes.byref(rect)
                )

                if left_down and not drag["active"]:
                    inside = (
                        rect.l <= point.X < rect.r
                        and rect.t <= point.Y < rect.b
                    )
                    local_x = point.X - rect.l
                    local_y = point.Y - rect.t
                    normal_drag = not api._settings_open and local_y >= 52
                    settings_drag = (
                        api._settings_open
                        and local_y < 34
                        and local_x < (rect.r - rect.l) - 95
                    )
                    if inside and (normal_drag or settings_drag):
                        drag.update(
                            active=True,
                            px=point.X,
                            py=point.Y,
                            wx=rect.l,
                            wy=rect.t,
                        )
                elif left_down and drag["active"]:
                    set_window_pos(
                        ctypes.c_void_p(form.Handle.ToInt64()), None,
                        drag["wx"] + point.X - drag["px"],
                        drag["wy"] + point.Y - drag["py"],
                        0, 0, 0x0001 | 0x0004 | 0x0010,
                    )
                elif not left_down:
                    drag["active"] = False
            except Exception:
                drag["active"] = False

        timer = Timer()
        timer.Interval = 15
        timer.Tick += EventHandler(tick)
        timer.Start()
        _DRAG_TIMERS.append((timer, tick))

    _on_ui_thread(window, install)


def keep_above_taskbar_safe(window):
    """Keep the mini above Explorer using only a process-local UI timer."""
    def install():
        from System import EventHandler
        from System.Windows.Forms import Timer

        form = window.native
        set_window_pos = ctypes.windll.user32.SetWindowPos
        set_window_pos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]

        def tick(sender, event):
            try:
                if form.IsDisposed or form.Disposing:
                    sender.Stop()
                    return
                if form.Visible:
                    set_window_pos(
                        ctypes.c_void_p(form.Handle.ToInt64()),
                        ctypes.c_void_p(-1), 0, 0, 0, 0,
                        0x0001 | 0x0002 | 0x0010,
                    )
            except Exception:
                try:
                    sender.Stop()
                except Exception:
                    pass

        timer = Timer()
        timer.Interval = 30
        timer.Tick += EventHandler(tick)
        timer.Start()
        _KEEPALIVE_TIMERS.append((timer, tick))

    _on_ui_thread(window, install)


def cleanup_native_integrations(*args):
    """Release callbacks before WinForms destroys their native handles."""
    for window in list(webview.windows):
        detach_noactivate_handler(window, synchronous=True)

    for item in list(_DRAG_TIMERS) + list(_KEEPALIVE_TIMERS):
        timer = item[0] if isinstance(item, tuple) else item
        try:
            timer.Stop()
            timer.Dispose()
        except Exception:
            pass
    _DRAG_TIMERS.clear()
    _KEEPALIVE_TIMERS.clear()

    user32 = ctypes.windll.user32
    for hook, callback in list(_FOREGROUND_HOOKS):
        try:
            user32.UnhookWinEvent(hook)
        except Exception:
            pass
    _FOREGROUND_HOOKS.clear()
    for hook, callback, drag in list(_MOUSE_DRAG_HOOKS):
        try:
            user32.UnhookWindowsHookEx(hook)
        except Exception:
            pass
    _MOUSE_DRAG_HOOKS.clear()

    global _INSTANCE_MUTEX
    if _INSTANCE_MUTEX:
        try:
            ctypes.windll.kernel32.CloseHandle(_INSTANCE_MUTEX)
        except Exception:
            pass
        _INSTANCE_MUTEX = None


def keep_above_taskbar(window):
    """Keep the mini pill above Explorer's equally topmost taskbar.

    Clicking another part of the taskbar can move Explorer ahead of other
    topmost windows without actually hiding them. Reasserting HWND_TOPMOST
    while the pill is visible prevents it from appearing to disappear.
    """
    def install():
        from System import EventHandler
        from System.Windows.Forms import Timer

        form = window.native
        hwnd_topmost = ctypes.c_void_p(-1)
        flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
        set_window_pos = ctypes.windll.user32.SetWindowPos
        set_window_pos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]

        def raise_if_visible(sender=None, event=None):
            try:
                set_window_pos(
                    ctypes.c_void_p(form.Handle.ToInt64()),
                    hwnd_topmost, 0, 0, 0, 0, flags,
                )
            except Exception:
                pass

        timer = Timer()
        timer.Interval = 250
        timer.Tick += EventHandler(raise_if_visible)
        timer.Start()
        _KEEPALIVE_TIMERS.append(timer)

        # Explorer can jump ahead of other topmost windows when the taskbar is
        # clicked. React to that foreground change immediately; the timer above
        # remains as a fallback for Shell versions that omit the event.
        winevent_proc = ctypes.WINFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
            ctypes.c_long, ctypes.c_long, ctypes.c_uint, ctypes.c_uint,
        )

        @winevent_proc
        def foreground_changed(hook, event, hwnd, obj_id, child_id, thread, time):
            raise_if_visible()

        set_hook = ctypes.windll.user32.SetWinEventHook
        set_hook.argtypes = [
            ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, winevent_proc,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
        ]
        set_hook.restype = ctypes.c_void_p
        hook = set_hook(3, 3, None, foreground_changed, 0, 0, 0)
        if hook:
            _FOREGROUND_HOOKS.append((hook, foreground_changed))
        raise_if_visible()

    _on_ui_thread(window, install)


def dock_mini_left_of_start(window):
    """Place the mini pill left of Start and vertically center it in the taskbar.

    This runs after the native window exists and uses only physical Win32
    coordinates, avoiding pywebview's DPI conversion for initial x/y values.
    """
    def apply():
        u = ctypes.windll.user32
        tray = u.FindWindowW("Shell_TrayWnd", None)
        start = u.FindWindowExW(tray, 0, "Start", None) if tray else 0
        if not tray or not start:
            return

        taskbar_rect = _RECT()
        start_rect = _RECT()
        mini_rect = _RECT()
        u.GetWindowRect(tray, ctypes.byref(taskbar_rect))
        u.GetWindowRect(start, ctypes.byref(start_rect))
        u.GetWindowRect(
            ctypes.c_void_p(window.native.Handle.ToInt64()),
            ctypes.byref(mini_rect),
        )

        set_window_pos = u.SetWindowPos
        set_window_pos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]
        hwnd = ctypes.c_void_p(window.native.Handle.ToInt64())

        # WinForms and Explorer can expose different DPI coordinate spaces.
        # Re-measure the actual landing position and converge on a safe gap
        # gap instead of trusting a single converted x/y calculation.
        for _ in range(5):
            u.GetWindowRect(start, ctypes.byref(start_rect))
            u.GetWindowRect(tray, ctypes.byref(taskbar_rect))
            u.GetWindowRect(hwnd, ctypes.byref(mini_rect))

            current_gap = start_rect.l - mini_rect.r
            mini_center = (mini_rect.t + mini_rect.b) // 2
            taskbar_center = (taskbar_rect.t + taskbar_rect.b) // 2
            x = mini_rect.l + current_gap - 20
            y = mini_rect.t + taskbar_center - mini_center

            if abs(current_gap - 20) <= 1 and abs(taskbar_center - mini_center) <= 1:
                break
            set_window_pos(
                hwnd, ctypes.c_void_p(-1), x, y, 0, 0,
                0x0001 | 0x0010,  # NOSIZE | NOACTIVATE
            )

    _on_ui_thread(window, apply)


def hide_taskbar_button(window):
    """Permanently hide the app's taskbar button (the mini pill is the restore UI).

    Must run after WebView2 finishes initializing: ShowInTaskbar recreates the
    window handle, which aborts an in-flight WebView2 init.
    """
    _on_ui_thread(window, lambda: setattr(window.native, "ShowInTaskbar", False))

# When frozen by PyInstaller, config lives next to the exe and the UI is
# unpacked to the temporary _MEIPASS resource directory.
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
UI_PATH = os.path.join(RES_DIR, "ui", "index.html")
MINI_UI_PATH = os.path.join(RES_DIR, "ui", "mini.html")
STARTUP_LNK = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs\Startup",
    "YouTube Counter.lnk",
)
WINDOW_W, WINDOW_H, SETUP_H = 320, 165, 215


class _RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


def taskbar_slot(mini_w_logical):
    """Compute logical (x, y, w, h) for a mini window docked left of the tray clock.

    pywebview treats x/y/width/height as logical pixels and multiplies them by
    the DPI scale itself, so everything returned here is logical. Returns None
    if there is no horizontal taskbar to dock onto.
    """
    u = ctypes.windll.user32
    try:
        u.SetProcessDPIAware()
        tray = u.FindWindowW("Shell_TrayWnd", None)
        if not tray:
            return None
        r = _RECT()
        u.GetWindowRect(tray, ctypes.byref(r))
        tb_h = r.b - r.t
        if tb_h <= 0 or tb_h >= (r.r - r.l):  # vertical taskbar — skip
            return None
        try:
            dpi = u.GetDpiForSystem()
        except AttributeError:
            dpi = 96
        scale = (dpi or 96) / 96
        # Windows 11 exposes the Start button as a direct "Start" child.
        # Prefer its left edge, then fall back to the notification area.
        anchor = r.r
        start = u.FindWindowExW(tray, 0, "Start", None)
        if start:
            sr = _RECT()
            u.GetWindowRect(start, ctypes.byref(sr))
            if r.l < sr.l < r.r:
                anchor = sr.l
        else:
            notify = u.FindWindowExW(tray, 0, "TrayNotifyWnd", None)
            if notify:
                nr = _RECT()
                u.GetWindowRect(notify, ctypes.byref(nr))
                if nr.l > r.l:
                    anchor = nr.l
        h_logical = max(28, int(tb_h / scale) - 12)
        x_logical = int(anchor / scale) - mini_w_logical - 8
        y_logical = int((r.t + tb_h / 2) / scale - h_logical / 2)
        return x_logical, y_logical, mini_w_logical, h_logical
    except Exception:
        return None

DEFAULT_CONFIG = {
    "apiKey": "",
    "channel": "",          # @handle or UC... channel ID
    "intervalSec": 30,
    "alwaysOnTop": True,
    "x": None,
    "y": None,
    "miniX": None,
    "miniY": None,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def tray_label(n):
    """Compact label that fits a 16px tray icon: 95, 9500, 1.2万, 12万, 1.2億."""
    if n >= 100_000_000:
        v = n / 100_000_000
        return ("%.1f億" % v) if v < 10 else ("%d億" % v)
    if n >= 10_000:
        v = n / 10_000
        return ("%.1f万" % v) if v < 10 else ("%d万" % v)
    return str(n)


class Api:
    def __init__(self):
        self._resolved_id = None
        self._resolved_for = None
        self._channel_url = None
        self._window = None
        self._mini = None
        self._main_drag_expanded = True
        self._settings_open = False
        self._closing_all = False

    def toggle_main(self):
        if not self._window:
            return
        try:
            from System import Action
            form = self._window.native

            def toggle():
                try:
                    if form.Visible:
                        form.Hide()
                    else:
                        form.Show()
                        from System.Windows.Forms import FormWindowState
                        form.WindowState = FormWindowState.Normal
                        hwnd = ctypes.c_void_p(form.Handle.ToInt64())
                        flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE|NOMOVE|SHOWWINDOW
                        ctypes.windll.user32.SetWindowPos(
                            hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, flags
                        )
                        ctypes.windll.user32.SetWindowPos(
                            hwnd, ctypes.c_void_p(-2), 0, 0, 0, 0, flags
                        )
                        form.BringToFront()
                        form.Activate()
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass

            form.BeginInvoke(Action(toggle))
        except Exception:
            pass

    def open_channel(self):
        url = self._channel_url
        if not url:
            ch = load_config()["channel"]
            if not ch:
                return
            if ch.startswith("UC"):
                url = "https://www.youtube.com/channel/" + ch
            else:
                url = "https://www.youtube.com/@" + ch.lstrip("@")
        webbrowser.open(url)

    def set_drag_expanded(self, expanded):
        self._main_drag_expanded = bool(expanded)
        self._settings_open = not bool(expanded)

    def set_on_top(self, on_top):
        on_top = bool(on_top)
        cfg = load_config()
        cfg["alwaysOnTop"] = on_top
        save_config(cfg)
        if self._window:
            apply_window_state(self._window, on_top)
        # the taskbar pill is only useful when the card is not pinned on top
        if self._mini:
            try:
                if on_top:
                    self._mini.hide()
                else:
                    self._mini.show()
            except Exception:
                pass
        return cfg

    def get_config(self):
        return load_config()

    def resize_window(self, height):
        try:
            if self._window:
                self._window.resize(WINDOW_W, int(height))
        except Exception:
            pass

    def get_autostart(self):
        return os.path.exists(STARTUP_LNK)

    def set_autostart(self, enabled):
        try:
            if enabled:
                if FROZEN:
                    target = sys.executable
                else:
                    target = os.path.join(APP_DIR, "YouTube Counter.vbs")
                script = (
                    "$ws = New-Object -ComObject WScript.Shell;"
                    "$s = $ws.CreateShortcut('%s');"
                    "$s.TargetPath = '%s';"
                    "$s.WorkingDirectory = '%s';"
                    "$s.Save()" % (STARTUP_LNK, target, APP_DIR)
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", script],
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                    timeout=20,
                )
            elif os.path.exists(STARTUP_LNK):
                os.remove(STARTUP_LNK)
        except Exception:
            pass
        return self.get_autostart()

    def save_settings(self, api_key, channel, interval_sec):
        cfg = load_config()
        cfg["apiKey"] = api_key.strip()
        cfg["channel"] = channel.strip()
        try:
            cfg["intervalSec"] = max(15, int(interval_sec))
        except (TypeError, ValueError):
            cfg["intervalSec"] = DEFAULT_CONFIG["intervalSec"]
        save_config(cfg)
        self._resolved_id = None
        return cfg

    def close_app(self):
        if self._closing_all:
            return
        self._closing_all = True
        cleanup_native_integrations()
        windows_to_close = list(webview.windows)
        # Destroy auxiliary windows first and the master form last.
        windows_to_close.sort(key=lambda w: w is self._window)
        for w in windows_to_close:
            try:
                w.destroy()
            except Exception:
                pass

    def _request(self, params):
        url = "https://www.googleapis.com/youtube/v3/channels?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "yt-sub-widget"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def fetch_stats(self):
        cfg = load_config()
        key, channel = cfg["apiKey"], cfg["channel"]
        if not key or not channel:
            return {"error": "not_configured"}

        params = {"part": "snippet,statistics", "key": key}
        if self._resolved_id and self._resolved_for == channel:
            params["id"] = self._resolved_id
        elif len(channel) == 24 and channel.startswith("UC"):
            params["id"] = channel
        else:
            params["forHandle"] = channel.lstrip("@")

        try:
            data = self._request(params)
        except urllib.error.HTTPError as e:
            try:
                reason = json.load(e)["error"]["errors"][0]["reason"]
            except Exception:
                reason = "http_%d" % e.code
            return {"error": reason}
        except (urllib.error.URLError, OSError, TimeoutError):
            return {"error": "network"}

        items = data.get("items") or []
        if not items:
            return {"error": "channel_not_found"}

        item = items[0]
        subs = int(item["statistics"].get("subscriberCount", 0))
        if self._mini:
            try:
                self._mini.evaluate_js('setCount("%s")' % tray_label(subs))
            except Exception:
                pass
        self._resolved_id = item["id"]
        self._resolved_for = channel
        snip, stats = item["snippet"], item["statistics"]
        custom = snip.get("customUrl", "")
        self._channel_url = (
            "https://www.youtube.com/" + custom if custom
            else "https://www.youtube.com/channel/" + item["id"]
        )
        thumbs = snip.get("thumbnails", {})
        thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        accent = thumbnail_accent(thumb)
        if self._mini and thumb:
            try:
                self._mini.evaluate_js(
                    "setAvatar(%s)" % json.dumps(thumb, ensure_ascii=False)
                )
                if accent:
                    self._mini.evaluate_js(
                        "setAccent(%s)" % json.dumps(accent)
                    )
            except Exception:
                pass
        return {
            "title": snip.get("title", ""),
            "handle": snip.get("customUrl", ""),
            "thumbnail": thumb,
            "accent": accent,
            "subscribers": int(stats.get("subscriberCount", 0)),
            "hiddenSubs": stats.get("hiddenSubscriberCount", False),
            "views": int(stats.get("viewCount", 0)),
            "videos": int(stats.get("videoCount", 0)),
            "intervalSec": cfg["intervalSec"],
        }


def main():
    if not acquire_single_instance():
        return
    cfg = load_config()
    api = Api()
    kwargs = {}
    if isinstance(cfg.get("x"), int) and isinstance(cfg.get("y"), int):
        kwargs["x"], kwargs["y"] = cfg["x"], cfg["y"]

    window = webview.create_window(
        "YouTube Subscriber Counter",
        UI_PATH,
        js_api=api,
        width=320,
        height=165,
        frameless=True,
        easy_drag=False,
        on_top=cfg.get("alwaysOnTop", True),
        background_color="#101016",
        resizable=False,
        **kwargs,
    )

    api._window = window

    def remember_position():
        c = load_config()
        c["x"], c["y"] = window.x, window.y
        if api._mini:
            try:
                c["miniX"], c["miniY"] = api._mini.x, api._mini.y
            except Exception:
                pass
        save_config(c)

    def close_sibling_windows(*args):
        """A native/taskbar close of the main form must close the mini too."""
        cleanup_native_integrations()
        for other in list(webview.windows):
            if other is not window:
                try:
                    other.destroy()
                except Exception:
                    pass

    # taskbar mini widget (weather-widget style pill, drag to move freely)
    slot = taskbar_slot(88)
    if slot:
        mx, my, mw, mh = slot
        mini = webview.create_window(
            "YT Sub Mini",
            MINI_UI_PATH,
            js_api=api,
            width=mw,
            height=mh,
            x=mx,
            y=my,
            min_size=(mw, mh),
            frameless=True,
            shadow=False,
            transparent=True,
            on_top=True,
            resizable=False,
            focus=False,
            easy_drag=True,
            background_color="#16161d",
        )
        api._mini = mini

        def mini_loaded():
            hide_taskbar_button(mini)
            install_unified_close_handler(mini, api)
            detach_noactivate_handler(mini)
            dock_mini_left_of_start(mini)
            round_small_corners(mini)
            keep_above_taskbar_safe(mini)
            if load_config().get("alwaysOnTop", True):
                try:
                    mini.hide()
                except Exception:
                    pass

        mini.events.loaded += mini_loaded

    def on_shown():
        apply_window_state(window, load_config().get("alwaysOnTop", True))
        round_corners(window)

    def on_loaded():
        hide_taskbar_button(window)
        install_unified_close_handler(window, api)
        install_safe_drag_regions(window, api)
        round_corners(window)  # re-apply: ShowInTaskbar recreated the handle

    window.events.loaded += on_loaded
    window.events.closing += remember_position
    window.events.closing += cleanup_native_integrations
    window.events.shown += on_shown
    webview.start()


if __name__ == "__main__":
    main()
