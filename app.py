"""Codex Usage Tray — a local, read-only Windows tray companion."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
import ctypes
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

import pystray
import customtkinter as ctk
from PIL import Image, ImageDraw


APP_NAME = "Codex Usage Tray"
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "CodexUsageTray"
SETTINGS_PATH = DATA_DIR / "settings.json"
LOG_PATH = DATA_DIR / "codex-usage-tray.log"
DEFAULT_SETTINGS = {
    "refresh_minutes": 15,
    "remaining_alert_percent": 20,
    "window_width": 360,
    "window_height": 330,
}
GREEN = "#35D39E"
RED = "#FF647C"
YELLOW = "#FACC15"
ORANGE = "#FB923C"
ICON_BG = "#D1D5DB"
MUTED = "#94A3B8"
BG = "#0B1220"
CARD = "#172235"
TRACK = "#334155"
TEXT = "#F8FAFC"
BLUE = "#5B8CFF"
ICON_PATH = Path(__file__).with_name("assets") / "codex-usage.ico"

if os.name == "nt":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def configure_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8",
    )


def load_settings() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.write_text(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_SETTINGS.copy()
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return {**DEFAULT_SETTINGS, **saved}
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SETTINGS.copy()


def make_icon(windows: list[dict[str, Any]] | None = None, error: bool = False) -> Image.Image:
    """Create a compact tray gauge: outer ring + inner usage pie.

    The outer ring is the first (short-term) window. The inner pie is the
    second (long-term) window, so its filled sector directly represents usage.
    """
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    outer_used = windows[0]["used"] if windows else 0
    inner_used = windows[1]["used"] if windows and len(windows) > 1 else 0

    def ring(bounds: tuple[int, int, int, int], used: int, base_color: str, full_color: str, width: int) -> None:
        color = RED if error else (full_color if used >= 100 else base_color)
        if used:
            draw.arc(bounds, start=-90, end=-90 + (360 * used / 100), fill=color, width=width)

    # A flat, light-gray base keeps the icon visible on either taskbar theme;
    # there is deliberately no dark progress track for unused capacity.
    draw.ellipse((5, 5, 59, 59), fill=ICON_BG)
    ring((5, 5, 59, 59), outer_used, GREEN, ORANGE, 8)
    inner_color = RED if error or inner_used >= 100 else YELLOW
    if inner_used:
        draw.pieslice((18, 18, 46, 46), start=-90, end=-90 + (360 * inner_used / 100), fill=inner_color)
    return image


def call_app_server() -> dict[str, Any]:
    """Ask the already-authenticated local Codex app-server for rate limits.

    This starts a short-lived local `codex app-server` JSON-RPC session; no web
    scraping or account credential storage is involved. Avoiding the daemon
    proxy also makes this resilient to a stale Desktop app control socket.
    """
    initialize = {
        "id": 0,
        "method": "initialize",
        "params": {"clientInfo": {"name": "codex_usage_tray", "title": APP_NAME, "version": "0.1.0"}},
    }
    initialized = {"method": "initialized", "params": {}}
    read_limits = {"id": 1, "method": "account/rateLimits/read"}
    cli = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    node = shutil.which("node.exe") or shutil.which("node")
    if not cli.is_file() or not node:
        raise RuntimeError("Codex CLI was not found. Confirm that `codex --version` works in a terminal.")

    # Invoke Node and the CLI entrypoint directly. This avoids a PowerShell
    # pipeline, which closes stdin before the app-server can send its response.
    proc = subprocess.Popen(
        [node, str(cli), "app-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    incoming: queue.Queue[str] = queue.Queue()

    def read_stdout() -> None:
        for line in proc.stdout:
            incoming.put(line)

    threading.Thread(target=read_stdout, daemon=True).start()
    try:
        proc.stdin.write("\n".join((json.dumps(initialize), json.dumps(initialized), json.dumps(read_limits))) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                line = incoming.get(timeout=0.25)
                message = json.loads(line)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            except json.JSONDecodeError:
                continue
            if message.get("id") == 1:
                if "error" in message:
                    detail = message["error"].get("message", "Codex returned an error while reading usage.")
                    if "authentication required" in detail.lower():
                        raise RuntimeError("Codex CLI is not signed in. Run `codex login` in a terminal, then refresh.")
                    raise RuntimeError(detail)
                return message.get("result", {})
        raise RuntimeError("No usage data was received from Codex.")
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def normalise_windows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = payload.get("rateLimits") or {}
    buckets = payload.get("rateLimitsByLimitId") or {"Codex": snapshot}
    windows: list[dict[str, Any]] = []
    for bucket_name, bucket in buckets.items():
        for key, fallback in (("primary", "Short-term limit"), ("secondary", "Long-term limit")):
            item = (bucket or {}).get(key)
            if not item or "usedPercent" not in item:
                continue
            minutes = item.get("windowDurationMins")
            label = fallback if not minutes else f"{minutes / 60:g}-hour limit"
            if len(buckets) > 1:
                label = f"{bucket_name} · {label}"
            windows.append({
                "label": label,
                "used": max(0, min(100, int(item["usedPercent"]))),
                "resets_at": item.get("resetsAt"),
            })
    return windows


class UsageTray:
    def __init__(self, show_panel_on_start: bool = True) -> None:
        self.settings = load_settings()
        self.root = ctk.CTk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        if ICON_PATH.is_file():
            self.root.iconbitmap(default=str(ICON_PATH))
        self.root.configure(fg_color=BG)
        self.panel: ctk.CTkToplevel | None = None
        self.rows: list[dict[str, Any]] = []
        self.updated_at: datetime | None = None
        self.error: str | None = None
        self.alerted = False
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.icon = pystray.Icon(APP_NAME, make_icon(), APP_NAME, menu=self.make_menu())
        self.root.after(200, self.drain_events)
        if show_panel_on_start:
            self.root.after(250, self.open_panel)
        self.root.after(800, self.refresh)
        self.schedule_next_refresh()

    def make_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Open usage panel", lambda: self.root.after(0, self.toggle_panel), default=True),
            pystray.MenuItem("Refresh now", lambda: self.root.after(0, self.refresh)),
            pystray.MenuItem("Reload settings", lambda: self.root.after(0, self.reload_settings)),
            pystray.MenuItem("Open settings file", lambda: os.startfile(SETTINGS_PATH)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.root.after(0, self.quit)),
        )

    def run(self) -> None:
        threading.Thread(target=self.icon.run, daemon=True).start()
        self.root.mainloop()

    def reload_settings(self) -> None:
        self.settings = load_settings()
        self.render_panel()
        self.icon.notify("Settings reloaded", APP_NAME)

    def refresh(self) -> None:
        if getattr(self, "refreshing", False):
            return
        self.refreshing = True
        self.error = None
        self.render_panel()
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self) -> None:
        try:
            self.events.put(("data", normalise_windows(call_app_server())))
        except Exception as exc:  # error is deliberately surfaced in the panel
            self.events.put(("error", str(exc)))

    def drain_events(self) -> None:
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            self.refreshing = False
            if kind == "data":
                self.rows, self.updated_at, self.error = value, datetime.now(), None
                if not self.rows:
                    self.error = "Codex returned no usage windows to display."
                self.update_tray()
                self.maybe_alert()
            else:
                self.error = value
                self.icon.icon = make_icon(error=True)
                self.icon.title = f"{APP_NAME}: read failed"
            self.render_panel()
        self.root.after(200, self.drain_events)

    def update_tray(self) -> None:
        self.icon.icon = make_icon(self.rows)
        def compact_label(row: dict[str, Any]) -> str:
            label = row["label"].replace("-hour limit", "h").replace("Short-term limit", "short").replace("Long-term limit", "long")
            if label == "168h":
                label = "7d"
            return f"{label} {row['used']}%"

        summary = "  ·  ".join(compact_label(row) for row in self.rows[:2])
        self.icon.title = f"Codex usage (used)\n{summary}" if summary else APP_NAME

    def maybe_alert(self) -> None:
        threshold = int(self.settings["remaining_alert_percent"])
        low = [row for row in self.rows if 100 - row["used"] <= threshold]
        if low and not self.alerted:
            self.icon.notify("; ".join(f"{x['label']} has {100-x['used']}% remaining" for x in low), "Codex usage alert")
            self.alerted = True
        elif not low:
            self.alerted = False

    def schedule_next_refresh(self) -> None:
        minutes = max(0, int(self.settings["refresh_minutes"]))
        if minutes:
            self.root.after(minutes * 60_000, self.auto_refresh)

    def auto_refresh(self) -> None:
        self.refresh()
        self.schedule_next_refresh()

    def toggle_panel(self) -> None:
        if self.panel and self.panel.winfo_exists():
            self.panel.destroy()
            self.panel = None
            return
        self.open_panel()

    def open_panel(self) -> None:
        if self.panel and self.panel.winfo_exists():
            self.panel.deiconify()
            self.panel.lift()
            self.panel.focus_force()
            return
        self.panel = ctk.CTkToplevel(self.root, fg_color=BG)
        self.panel.title(APP_NAME)
        if ICON_PATH.is_file():
            self.panel.iconbitmap(default=str(ICON_PATH))
        self.panel.resizable(False, False)
        self.panel.attributes("-topmost", True)
        self.panel.protocol("WM_DELETE_WINDOW", self.toggle_panel)
        self.render_panel()

    @staticmethod
    def reset_text(timestamp: Any) -> str:
        if not timestamp:
            return "Reset time unavailable"
        try:
            return "Resets " + datetime.fromtimestamp(int(timestamp)).strftime("%b %d, %H:%M")
        except (ValueError, OSError, TypeError):
            return "Reset time unavailable"

    def render_panel(self) -> None:
        if not self.panel or not self.panel.winfo_exists():
            return
        for child in self.panel.winfo_children():
            child.destroy()
        box = ctk.CTkFrame(self.panel, fg_color=BG, corner_radius=0)
        box.pack(fill="both", expand=True)
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text="CODEX  ·  USAGE", text_color=TEXT,
                     font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold")).pack(anchor="w", padx=24, pady=(22, 2))
        status = "Refreshing…" if getattr(self, "refreshing", False) else (
            f"Updated {self.updated_at.strftime('%H:%M:%S')}" if self.updated_at else "Not updated yet"
        )
        ctk.CTkLabel(box, text=status, text_color=MUTED,
                     font=ctk.CTkFont(family="Segoe UI", size=12)).pack(anchor="w", padx=24, pady=(0, 18))
        if self.error:
            error_card = ctk.CTkFrame(box, fg_color="#38202A", corner_radius=12)
            error_card.pack(fill="x", padx=24, pady=(0, 14))
            ctk.CTkLabel(error_card, text="Could not refresh", text_color="#FF9AAD",
                         font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")).pack(anchor="w", padx=14, pady=(11, 1))
            ctk.CTkLabel(error_card, text=self.error, text_color="#FFC2CC", justify="left",
                         wraplength=330, font=ctk.CTkFont(family="Segoe UI", size=12)).pack(anchor="w", padx=14, pady=(0, 11))
        for row in self.rows:
            card = ctk.CTkFrame(box, fg_color=CARD, corner_radius=14)
            card.pack(fill="x", padx=24, pady=(0, 12))
            header = ctk.CTkFrame(card, fg_color="transparent")
            header.pack(fill="x")
            ctk.CTkLabel(header, text=row["label"], text_color=TEXT,
                         font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold")).pack(side="left", padx=16, pady=(14, 8))
            ctk.CTkLabel(header, text=f"{100-row['used']}% remaining", text_color=RED if row["used"] >= 70 else GREEN,
                         font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold")).pack(side="right", padx=16, pady=(14, 8))
            progress = ctk.CTkProgressBar(card, height=9, corner_radius=9, fg_color=TRACK,
                                           progress_color=RED if row["used"] >= 70 else GREEN)
            progress.pack(fill="x", padx=16, pady=(0, 9))
            progress.set(row["used"] / 100)
            footer = ctk.CTkFrame(card, fg_color="transparent")
            footer.pack(fill="x", padx=16, pady=(0, 13))
            ctk.CTkLabel(footer, text=f"{row['used']}% used", text_color=MUTED,
                         font=ctk.CTkFont(family="Segoe UI", size=12)).pack(side="left")
            ctk.CTkLabel(footer, text=self.reset_text(row["resets_at"]), text_color=MUTED,
                         font=ctk.CTkFont(family="Segoe UI", size=12)).pack(side="right")
        controls = ctk.CTkFrame(box, fg_color="transparent")
        controls.pack(fill="x", padx=24, pady=(4, 22))
        ctk.CTkButton(controls, text="↻  Refresh", command=self.refresh, height=40, corner_radius=10,
                      fg_color=BLUE, hover_color="#4777E8", text_color="white",
                      font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(controls, text="Close", command=self.toggle_panel, height=40, width=86, corner_radius=10,
                      fg_color="#243147", hover_color="#31415A", text_color=TEXT,
                      font=ctk.CTkFont(family="Segoe UI", size=14)).pack(side="right")
        self.panel.geometry(f"{self.settings['window_width']}x{max(self.settings['window_height'], 360)}")

    def quit(self) -> None:
        self.icon.stop()
        self.root.quit()


if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--minimized", action="store_true")
    args, _ = parser.parse_known_args()
    try:
        UsageTray(show_panel_on_start=not args.minimized).run()
    except Exception:
        logging.exception("Application failed to start")
        raise
