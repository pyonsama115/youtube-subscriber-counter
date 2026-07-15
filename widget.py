"""YouTube Subscriber Counter - lightweight desktop widget (pywebview / WebView2)."""
import ctypes
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import webview

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


def apply_window_state(window, on_top):
    """Set TopMost on the WinForms UI thread."""
    _on_ui_thread(window, lambda: setattr(window.native, "TopMost", bool(on_top)))


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
        right_edge = r.r
        notify = u.FindWindowExW(tray, 0, "TrayNotifyWnd", None)
        if notify:
            nr = _RECT()
            u.GetWindowRect(notify, ctypes.byref(nr))
            if nr.l > r.l:
                right_edge = nr.l
        h_logical = max(28, int(tb_h / scale) - 12)
        x_logical = int(right_edge / scale) - mini_w_logical - 8
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
        for w in list(webview.windows):
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
        return {
            "title": snip.get("title", ""),
            "handle": snip.get("customUrl", ""),
            "thumbnail": thumb,
            "subscribers": int(stats.get("subscriberCount", 0)),
            "hiddenSubs": stats.get("hiddenSubscriberCount", False),
            "views": int(stats.get("viewCount", 0)),
            "videos": int(stats.get("videoCount", 0)),
            "intervalSec": cfg["intervalSec"],
        }


def main():
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
        easy_drag=True,
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

    # taskbar mini widget (weather-widget style pill, drag to move freely)
    slot = taskbar_slot(88)
    if slot:
        mx, my, mw, mh = slot
        if isinstance(cfg.get("miniX"), int) and isinstance(cfg.get("miniY"), int):
            mx, my = cfg["miniX"], cfg["miniY"]
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
            on_top=True,
            resizable=False,
            focus=False,
            easy_drag=True,
            background_color="#16161d",
        )
        api._mini = mini

        def mini_loaded():
            round_corners(mini)
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
        round_corners(window)  # re-apply: ShowInTaskbar recreated the handle

    window.events.loaded += on_loaded
    window.events.closing += remember_position
    window.events.shown += on_shown
    webview.start()


if __name__ == "__main__":
    main()
