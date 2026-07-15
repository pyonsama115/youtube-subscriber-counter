"""YouTube Subscriber Counter - lightweight desktop widget (pywebview / WebView2)."""
import ctypes
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import webview

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4


def apply_taskbar_visibility(window, show):
    """Show or hide the window's taskbar button (WS_EX_TOOLWINDOW trick)."""
    try:
        hwnd = window.native.Handle.ToInt32()
    except Exception:
        return
    u = ctypes.windll.user32
    style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if show:
        style = (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
    else:
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    u.ShowWindow(hwnd, SW_HIDE)
    u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

# When frozen by PyInstaller, config lives next to the exe and the UI is
# unpacked to the temporary _MEIPASS resource directory.
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
UI_PATH = os.path.join(RES_DIR, "ui", "index.html")

DEFAULT_CONFIG = {
    "apiKey": "",
    "channel": "",          # @handle or UC... channel ID
    "intervalSec": 30,
    "alwaysOnTop": True,
    "x": None,
    "y": None,
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


class Tray:
    """Taskbar notification-area icon that displays the live subscriber count."""

    def __init__(self, window):
        self.window = window
        self._tray = None
        self._hicon = None

    def start(self):
        try:
            from System import Action
            self.window.native.Invoke(Action(self._build))
        except Exception:
            pass

    def _build(self):
        import System.Windows.Forms as WinForms

        self._tray = WinForms.NotifyIcon()
        menu = WinForms.ContextMenuStrip()
        mi_toggle = WinForms.ToolStripMenuItem("ウィジェットを表示 / 非表示")
        mi_toggle.Click += lambda s, e: self._toggle()
        mi_exit = WinForms.ToolStripMenuItem("終了")
        mi_exit.Click += lambda s, e: self._exit()
        menu.Items.Add(mi_toggle)
        menu.Items.Add(mi_exit)
        self._tray.ContextMenuStrip = menu
        self._tray.Text = "YouTube Subscriber Counter"
        self._tray.MouseUp += self._on_mouse
        self._set_icon("···")
        self._tray.Visible = True

    def _on_mouse(self, sender, e):
        import System.Windows.Forms as WinForms
        if e.Button == WinForms.MouseButtons.Left:
            self._toggle()

    def _toggle(self):
        form = self.window.native
        if form.Visible:
            form.Hide()
        else:
            form.Show()

    def _exit(self):
        self.dispose()
        self.window.destroy()

    def _set_icon(self, text):
        from System.Drawing import (Brushes, Bitmap, Color, Font, FontStyle,
                                    Graphics, GraphicsUnit, Icon, RectangleF,
                                    SolidBrush, StringAlignment, StringFormat)
        from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
        from System.Drawing.Text import TextRenderingHint

        bmp = Bitmap(32, 32)
        g = Graphics.FromImage(bmp)
        g.Clear(Color.Transparent)
        g.SmoothingMode = SmoothingMode.AntiAlias
        g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit

        # YouTube-red rounded badge so the number reads on light and dark taskbars
        path = GraphicsPath()
        r, w = 10, 32
        path.AddArc(0, 0, r * 2, r * 2, 180, 90)
        path.AddArc(w - r * 2, 0, r * 2, r * 2, 270, 90)
        path.AddArc(w - r * 2, w - r * 2, r * 2, r * 2, 0, 90)
        path.AddArc(0, w - r * 2, r * 2, r * 2, 90, 90)
        path.CloseFigure()
        g.FillPath(SolidBrush(Color.FromArgb(255, 230, 33, 23)), path)

        sizes = {1: 22.0, 2: 19.0, 3: 14.0, 4: 11.0}
        font = Font("Segoe UI", sizes.get(len(text), 9.5), FontStyle.Bold, GraphicsUnit.Pixel)
        fmt = StringFormat()
        fmt.Alignment = StringAlignment.Center
        fmt.LineAlignment = StringAlignment.Center
        g.DrawString(text, font, Brushes.White, RectangleF(0, 1, 32, 30), fmt)
        g.Dispose()

        hicon = bmp.GetHicon()
        self._tray.Icon = Icon.FromHandle(hicon)
        if self._hicon:
            ctypes.windll.user32.DestroyIcon(self._hicon)
        self._hicon = hicon
        bmp.Dispose()

    def update(self, subscribers, title):
        if not self._tray:
            return
        try:
            from System import Action
            label = tray_label(subscribers)
            tip = ("%s\n登録者 %s人" % (title, format(subscribers, ",")))[:63]

            def apply():
                self._set_icon(label)
                self._tray.Text = tip

            self.window.native.BeginInvoke(Action(apply))
        except Exception:
            pass

    def dispose(self):
        try:
            if self._tray:
                self._tray.Visible = False
                self._tray.Dispose()
                self._tray = None
        except Exception:
            pass


class Api:
    def __init__(self):
        self._resolved_id = None
        self._resolved_for = None
        self._channel_url = None
        self._window = None
        self._tray = None

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
            self._window.on_top = on_top
            apply_taskbar_visibility(self._window, show=not on_top)
        return cfg

    def get_config(self):
        return load_config()

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
        if self._tray:
            self._tray.dispose()
        for w in webview.windows:
            w.destroy()

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
        if self._tray:
            self._tray.update(
                int(item["statistics"].get("subscriberCount", 0)),
                item["snippet"].get("title", ""),
            )
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
        width=360,
        height=195,
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
        save_config(c)

    tray = Tray(window)
    api._tray = tray

    def on_shown():
        apply_taskbar_visibility(window, show=not load_config().get("alwaysOnTop", True))
        tray.start()

    def on_closing():
        remember_position()
        tray.dispose()

    window.events.closing += on_closing
    window.events.shown += on_shown
    webview.start()


if __name__ == "__main__":
    main()
