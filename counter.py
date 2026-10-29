#!/usr/bin/env python3
"""Numpad Cycle Counter (macOS only).

A small window (pinned on top by default) that counts the digit keys 0-9
pressed in ANY application (global key listener) and appends one CSV row per
time cycle.

Two dimensions are counted:
    * Dimension 1: press a digit key (0-9).
    * Dimension 2: hold the ` key (left of the 1 key) and press a digit key.

While "Start" is on, every press increments the matching counter.
"Export CSV" appends one row to counts_survey.csv (creating the file with a
header if needed), resets the counters and moves to the next cycle.

Both dimensions share the same notes. Only keys that have a note are exported,
and the TOTAL shown in the window is the sum of those same keys, so it always
matches the "Total" / "Total (2)" CSV columns.

The "Ghim" button pins the window on top of other windows (or unpins it).

Every launch starts fresh: Start Time 00:00, cycle 15 minutes, empty notes.
Nothing is stored except the CSV file you export.

Usage:
    python3 counter.py
    python3 counter.py --out ~/Documents/counts_survey.csv
    python3 counter.py --keypad-only
"""
import argparse
import csv
import math
import os
import queue
import sys

ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0]  # display and CSV order


# --- macOS number pad key codes (pynput "vk") -> digit ----------------------
KEYPAD_VK = {82: 0, 83: 1, 84: 2, 85: 3, 86: 4, 87: 5, 88: 6, 89: 7, 91: 8, 92: 9}

# Key that switches counting to dimension 2 while it is held: the ` key left of
# the 1 key (key code 50 on ANSI/US keyboards). Change it here if your keyboard
# has a different layout.
GRAVE_VK = 50
GRAVE_CHARS = ("`", "~")


def hhmm(total_minutes):
    m = total_minutes % 1440
    return "%02d:%02d" % (m // 60, m % 60)


def clamp_int(text, lo, hi, default):
    try:
        v = int(str(text).strip())
    except ValueError:
        return default
    return max(lo, min(hi, v))


class CounterCore:
    """UI-independent state and logic."""

    def __init__(self):
        self.counts = [[0] * 10, [0] * 10]  # [dimension 1, dimension 2]
        self.history = []  # (digit, dimension)
        self.notes = [""] * 10
        self.hour = 0
        self.minute = 0
        self.cycle_len = 15
        self.cycle = 1

    def press(self, d, dim=1):
        self.counts[dim - 1][d] += 1
        self.history.append((d, dim))

    def undo(self):
        """Undo the last press (either dimension). Returns (digit, dim) or None."""
        if not self.history:
            return None
        d, dim = self.history.pop()
        self.counts[dim - 1][d] = max(0, self.counts[dim - 1][d] - 1)
        return d, dim

    def reset(self):
        self.counts = [[0] * 10, [0] * 10]
        self.history = []

    def total(self, dim=1):
        """Sum of the keys that have a note (same as the CSV "Total" column)."""
        return sum(self.counts[dim - 1][d] for d in self.export_keys())

    def window(self):
        base = self.hour * 60 + self.minute
        start = base + (self.cycle - 1) * self.cycle_len
        return hhmm(start), hhmm(start + self.cycle_len)

    def export_keys(self):
        """Keys that have a note, in display order. Only these are exported."""
        return [d for d in ORDER if (self.notes[d] or "").strip()]

    def header(self):
        names = [self.notes[d].strip() for d in self.export_keys()]
        return (["Start Time", "End Time"] + names + ["Total"]
                + [n + " (2)" for n in names] + ["Total (2)"])

    def row(self):
        start, end = self.window()
        v1 = [self.counts[0][d] for d in self.export_keys()]
        v2 = [self.counts[1][d] for d in self.export_keys()]
        return [start, end] + v1 + [sum(v1)] + v2 + [sum(v2)]


def read_header(path):
    """Header row of an existing CSV, or None if missing/empty."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if r:
                return r
    return None


def append_row(path, header, row):
    """Create the file (with header) if needed, otherwise append one row."""
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    needs_newline = False
    if not new_file:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            needs_newline = f.read(1) not in (b"\n", b"\r")
    with open(path, "a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as f:
        if needs_newline:
            f.write("\r\n")
        w = csv.writer(f, lineterminator="\r\n")
        if new_file:
            w.writerow(header)
        w.writerow(row)


# --- Palette (dark, high contrast) ------------------------------------------
BG = "#11151B"
PANEL = "#1A212B"
PANEL_2 = "#232C39"
FIELD = "#0E1217"
BORDER = "#2C3646"
TEXT = "#EDF2F7"
MUTED = "#93A1B3"
ACCENT = "#6AA6FF"
ACCENT_DIM = "#1F3454"
AMBER = "#F5B544"  # dimension 2
GREEN = "#34C97B"
RED = "#EF5B53"
INK = "#08111D"

SANS = "Helvetica Neue"
MONO = "Menlo"

# name: (family, base size, weight). All sizes scale with the window.
FONT_SPECS = {
    "btn": (SANS, 12, "bold"),
    "caption": (SANS, 10, "bold"),
    "pill": (SANS, 11, "bold"),
    "total": (SANS, 24, "bold"),
    "time": (MONO, 15, "bold"),
    "window": (MONO, 17, "bold"),
    "badge": (SANS, 14, "bold"),
    "count": (MONO, 18, "bold"),
    "note": (SANS, 13, "normal"),
}


def run_ui(csv_path, keypad_only):
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import messagebox
    from pynput import keyboard

    core = CounterCore()

    root = tk.Tk()
    root.title("Bộ đếm")
    root.configure(bg=BG)
    root.attributes("-topmost", True)

    fonts = {k: tkfont.Font(root=root, family=f, size=s, weight=w) for k, (f, s, w) in FONT_SPECS.items()}

    running = {"on": False}
    pinned = {"on": True}
    events = queue.SimpleQueue()
    down = set()
    grave = {"down": False}

    hour_v = tk.StringVar(value="00")
    min_v = tk.StringVar(value="00")
    len_v = tk.StringVar(value="15")
    status_v = tk.StringVar(value="ĐÃ DỪNG")
    window_v = tk.StringVar()
    total_v = tk.StringVar(value="0")
    total2_v = tk.StringVar(value="0")
    count_vs = [[tk.StringVar(value="0") for _ in range(10)] for _ in range(2)]
    note_vs = [tk.StringVar(value="") for _ in range(10)]

    # ---------- custom widgets (ttk buttons ignore colors on macOS) ----------
    class Btn(tk.Label):
        def __init__(self, parent, text, command, bg, fg, hover, pady=9, width=None):
            super().__init__(parent, text=text, bg=bg, fg=fg, font=fonts["btn"],
                             padx=6, pady=pady, cursor="pointinghand")
            if width:
                self.configure(width=width)
            self._bg, self._hover, self._fg = bg, hover, fg
            self._command = command
            self.enabled = True
            self.bind("<Enter>", lambda _e: self.enabled and self.configure(bg=self._hover))
            self.bind("<Leave>", lambda _e: self.configure(bg=self._bg))
            self.bind("<Button-1>", lambda _e: self.enabled and self._command())

        def set_enabled(self, on):
            self.enabled = on
            self.configure(fg=self._fg if on else MUTED, bg=self._bg,
                           cursor="pointinghand" if on else "arrow")

        def recolor(self, bg, fg, hover):
            self._bg, self._fg, self._hover = bg, fg, hover
            self.configure(bg=bg, fg=fg if self.enabled else MUTED)

    def caption(parent, text):
        return tk.Label(parent, text=text, bg=PANEL, fg=MUTED, font=fonts["caption"])

    def entry(parent, var, width, font, center=False):
        return tk.Entry(parent, textvariable=var, width=width, bg=FIELD, fg=TEXT,
                        insertbackground=TEXT, relief="flat", bd=0,
                        highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                        justify="center" if center else "left", font=font,
                        selectbackground=ACCENT_DIM, selectforeground=TEXT)

    # ---------- state -> UI ----------
    def sync_settings(*_):
        core.hour = clamp_int(hour_v.get(), 0, 23, core.hour)
        core.minute = clamp_int(min_v.get(), 0, 59, core.minute)
        core.cycle_len = clamp_int(len_v.get(), 1, 1440, core.cycle_len)
        core.notes = [note_vs[d].get() for d in range(10)]
        refresh()

    def normalize(_e=None):
        sync_settings()
        hour_v.set("%02d" % core.hour)
        min_v.set("%02d" % core.minute)
        len_v.set(str(core.cycle_len))

    badges = [None] * 10

    def refresh(flash=None):
        """flash: (digit, dimension) of the key to highlight, or None."""
        for d in range(10):
            count_vs[0][d].set(str(core.counts[0][d]))
            count_vs[1][d].set(str(core.counts[1][d]))
        s, e = core.window()
        window_v.set("%s – %s" % (s, e))
        total_v.set(str(core.total(1)))
        total2_v.set(str(core.total(2)))
        on = running["on"]
        status_v.set("ĐANG ĐẾM" if on else "ĐÃ DỪNG")
        dot.itemconfigure(dot_id, fill=GREEN if on else MUTED)
        status_lbl.configure(fg=GREEN if on else MUTED)
        btn_start.set_enabled(not on)
        btn_stop.set_enabled(on)
        if flash is not None:
            d, dim = flash
            b = badges[d]
            b.configure(bg=ACCENT if dim == 1 else AMBER, fg=INK)
            root.after(140, lambda: b.configure(bg=ACCENT_DIM, fg=ACCENT))

    # ---------- global key listener ----------
    # Every key event reaches on_press, but a key that is neither a digit nor
    # the ` key is dropped on the first checks: it is never stored, logged or
    # sent anywhere. Only digit keys are tracked (in `down`, to ignore
    # auto-repeat while held) plus a single yes/no flag: is ` held right now.
    def is_grave(vk, ch):
        return vk == GRAVE_VK or ch in GRAVE_CHARS

    def on_press(key):
        vk = getattr(key, "vk", None)
        ch = getattr(key, "char", None)
        if is_grave(vk, ch):
            grave["down"] = True
            return
        digit = None
        if vk in KEYPAD_VK:
            digit = KEYPAD_VK[vk]
        elif not keypad_only and ch is not None and len(ch) == 1 and ch in "0123456789":
            digit = int(ch)
        if digit is None:
            return  # not a digit: ignore completely
        ident = vk if vk is not None else ch
        if ident in down:
            return  # auto-repeat while the key is held
        down.add(ident)
        events.put((digit, 2 if grave["down"] else 1))

    def on_release(key):
        # Only clears what on_press set (the ` flag, a held digit); stores nothing.
        vk = getattr(key, "vk", None)
        ch = getattr(key, "char", None)
        if is_grave(vk, ch):
            grave["down"] = False
            return
        down.discard(vk if vk is not None else ch)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()

    def poll():
        last = None
        while True:
            try:
                d, dim = events.get_nowait()
            except queue.Empty:
                break
            # Don't count while this window has focus (e.g. typing a note).
            if running["on"] and root.focus_displayof() is None:
                core.press(d, dim)
                last = (d, dim)
        if last is not None:
            refresh(flash=last)
        root.after(40, poll)

    # ---------- actions ----------
    def do_start():
        normalize()
        running["on"] = True
        refresh()

    def do_stop():
        running["on"] = False
        refresh()

    def do_undo():
        refresh(flash=core.undo())

    def do_reset():
        core.reset()
        refresh()

    def apply_pin():
        root.attributes("-topmost", pinned["on"])
        if pinned["on"]:
            btn_pin.configure(text="Ghim: Bật")
            btn_pin.recolor(ACCENT_DIM, ACCENT, "#2A4570")
            root.lift()
        else:
            btn_pin.configure(text="Ghim: Tắt")
            btn_pin.recolor(PANEL_2, MUTED, "#303B4B")

    def do_pin():
        pinned["on"] = not pinned["on"]
        apply_pin()

    def do_export():
        normalize()
        if not core.export_keys():
            messagebox.showinfo("Chưa có chú thích",
                                "Hãy nhập chú thích cho ít nhất một phím trước khi xuất file.",
                                parent=root)
            return
        header = core.header()
        old_header = read_header(csv_path)
        if old_header is not None and old_header != header:
            ok = messagebox.askyesno(
                "Tiêu đề không khớp",
                "Các cột hiện tại khác tiêu đề trong file.\n"
                "Vẫn ghi tiếp vào cuối file?",
                parent=root)
            if not ok:
                return
        try:
            append_row(csv_path, header, core.row())
        except OSError as err:
            messagebox.showerror("Không ghi được file", str(err), parent=root)
            return
        core.reset()
        core.cycle += 1
        refresh()

    def on_close():
        listener.stop()
        root.destroy()

    # ---------- layout ----------
    # Pack order matters: the buttons are packed to the bottom BEFORE the key
    # table, so when the window gets small the table is what shrinks and the
    # buttons always stay visible.
    outer = tk.Frame(root, bg=BG, padx=14, pady=12)
    outer.pack(fill="both", expand=True)

    head = tk.Frame(outer, bg=BG)
    head.pack(fill="x")
    left = tk.Frame(head, bg=BG)
    left.pack(side="left")
    pill = tk.Frame(left, bg=PANEL, padx=12, pady=6)
    pill.pack(side="left")
    dot = tk.Canvas(pill, width=12, height=12, bg=PANEL, highlightthickness=0)
    dot_id = dot.create_oval(1, 1, 11, 11, fill=MUTED, outline="")
    dot.pack(side="left", padx=(0, 8))
    status_lbl = tk.Label(pill, textvariable=status_v, bg=PANEL, fg=MUTED, font=fonts["pill"])
    status_lbl.pack(side="left")
    btn_pin = Btn(left, "Ghim: Bật", do_pin, ACCENT_DIM, ACCENT, "#2A4570", pady=5, width=9)
    btn_pin.pack(side="left", padx=(8, 0))

    tot = tk.Frame(head, bg=BG)
    tot.pack(side="right")
    for i, (label, var, color) in enumerate((("TỔNG 1", total_v, TEXT), ("TỔNG 2", total2_v, AMBER))):
        col = tk.Frame(tot, bg=BG)
        col.pack(side="left", padx=(0 if i == 0 else 12, 0))
        tk.Label(col, text=label, bg=BG, fg=MUTED, font=fonts["caption"]).pack(anchor="e")
        tk.Label(col, textvariable=var, bg=BG, fg=color, font=fonts["total"],
                 width=4, anchor="e").pack(anchor="e")

    # Settings card: Start Time | Cycle | Time window
    card = tk.Frame(outer, bg=PANEL, padx=14, pady=10)
    card.pack(fill="x", pady=(10, 0))

    b1 = tk.Frame(card, bg=PANEL)
    b1.grid(row=0, column=0, sticky="w")
    caption(b1, "START TIME").pack(anchor="w")
    trow = tk.Frame(b1, bg=PANEL)
    trow.pack(anchor="w", pady=(4, 0))
    he = entry(trow, hour_v, 3, fonts["time"], True)
    he.pack(side="left", ipady=3)
    tk.Label(trow, text=":", bg=PANEL, fg=MUTED, font=fonts["time"]).pack(side="left", padx=3)
    me = entry(trow, min_v, 3, fonts["time"], True)
    me.pack(side="left", ipady=3)

    b2 = tk.Frame(card, bg=PANEL)
    b2.grid(row=0, column=1, sticky="w", padx=(16, 0))
    caption(b2, "CHU KỲ (PHÚT)").pack(anchor="w")
    le = entry(b2, len_v, 5, fonts["time"], True)
    le.pack(anchor="w", pady=(4, 0), ipady=3)

    b3 = tk.Frame(card, bg=PANEL)
    b3.grid(row=0, column=2, sticky="e", padx=(16, 0))
    caption(b3, "KHUNG GIỜ").pack(anchor="e")
    tk.Label(b3, textvariable=window_v, bg=PANEL, fg=ACCENT, font=fonts["window"]).pack(anchor="e", pady=(4, 0))
    card.columnconfigure(2, weight=1)

    for w in (he, me, le):
        w.bind("<FocusOut>", normalize)
        w.bind("<Return>", lambda _e: root.focus_set())

    # Buttons (packed to the bottom before the table: they never get squeezed out)
    btns = tk.Frame(outer, bg=BG)
    btns.pack(side="bottom", fill="x", pady=(10, 0))
    btn_start = Btn(btns, "Start", do_start, GREEN, INK, "#5DDB9A")
    btn_stop = Btn(btns, "Stop", do_stop, RED, "#FFFFFF", "#FF7B74")
    btn_undo = Btn(btns, "Undo", do_undo, PANEL_2, TEXT, "#303B4B")
    btn_reset = Btn(btns, "Reset Counts", do_reset, PANEL_2, TEXT, "#303B4B")
    btn_export = Btn(btns, "Export CSV", do_export, ACCENT, INK, "#8FBCFF")
    for i, b in enumerate((btn_start, btn_stop, btn_undo, btn_reset, btn_export)):
        b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0 if i == 4 else 3))
        btns.columnconfigure(i, weight=1)

    # Key table: key | dimension 1 | dimension 2 | note. Rows stretch when the window grows.
    table = tk.Frame(outer, bg=PANEL, padx=14, pady=10)
    table.pack(fill="both", expand=True, pady=(10, 0))
    caption(table, "PHÍM").grid(row=0, column=0, sticky="w", pady=(0, 4))
    caption(table, "CHIỀU 1").grid(row=0, column=1, sticky="e", padx=(8, 0), pady=(0, 4))
    c2 = caption(table, "CHIỀU 2")
    c2.configure(fg=AMBER)
    c2.grid(row=0, column=2, sticky="e", padx=(8, 0), pady=(0, 4))
    caption(table, "CHÚ THÍCH").grid(row=0, column=3, sticky="w", padx=(12, 0), pady=(0, 4))
    for r, d in enumerate(ORDER, start=1):
        b = tk.Label(table, text=str(d), bg=ACCENT_DIM, fg=ACCENT, width=3, font=fonts["badge"], pady=1)
        b.grid(row=r, column=0, sticky="ns", pady=2)
        badges[d] = b
        tk.Label(table, textvariable=count_vs[0][d], bg=PANEL, fg=TEXT, width=5, anchor="e",
                 font=fonts["count"]).grid(row=r, column=1, padx=(8, 0))
        tk.Label(table, textvariable=count_vs[1][d], bg=PANEL, fg=AMBER, width=5, anchor="e",
                 font=fonts["count"]).grid(row=r, column=2, padx=(8, 0))
        ent = entry(table, note_vs[d], 18, fonts["note"])
        ent.grid(row=r, column=3, sticky="ew", padx=(12, 0), pady=2, ipady=4)
        ent.bind("<FocusOut>", sync_settings)
        ent.bind("<Return>", lambda _e: root.focus_set())
        table.rowconfigure(r, weight=1)
    hint = caption(table, "Chiều 2: giữ phím ` rồi bấm số")
    hint.configure(fg=AMBER)
    hint.grid(row=len(ORDER) + 1, column=0, columnspan=4, sticky="w", pady=(6, 0))
    table.columnconfigure(3, weight=1)

    # ---------- resizable window ----------
    # Fonts scale with the window, but never beyond what fits: for every window
    # size we pick the largest scale whose natural layout size still fits inside
    # the window, so the buttons and the time window are never cut off. The
    # smallest window is the natural size at the minimum scale.
    S_MIN, S_MAX, STEP = 0.6, 2.5, 0.02

    def apply_scale(s):
        for name, (_f, size, _w) in FONT_SPECS.items():
            fonts[name].configure(size=max(7, int(round(size * s))))
        root.update_idletasks()

    apply_scale(S_MIN)
    w0, h0 = root.winfo_reqwidth(), root.winfo_reqheight()
    apply_scale(1.0)
    w1, h1 = root.winfo_reqwidth(), root.winfo_reqheight()
    root.minsize(w0 + 4, h0 + 4)

    scale = {"v": 1.0}

    def fit(w, h):
        sw = S_MIN + (w - w0) / max(1, w1 - w0) * (1 - S_MIN)
        sh = S_MIN + (h - h0) / max(1, h1 - h0) * (1 - S_MIN)
        s = max(S_MIN, min(sw, sh, S_MAX))
        s = max(S_MIN, math.floor(s / STEP + 1e-9) * STEP)
        if abs(s - scale["v"]) < 1e-6:
            return
        while True:
            apply_scale(s)
            if s <= S_MIN + 1e-6 or (root.winfo_reqwidth() <= w and root.winfo_reqheight() <= h):
                break
            s = max(S_MIN, s - STEP)
        scale["v"] = s

    start_h = min(h1, root.winfo_screenheight() - 160)  # never taller than the screen
    start_w = w1 if start_h == h1 else max(w0 + 4, int(w1 * start_h / h1))
    start_h = max(start_h, h0 + 4)
    root.geometry("%dx%d" % (start_w, start_h))
    fit(start_w, start_h)

    def on_configure(e):
        if e.widget is not root or e.width < 100 or e.height < 100:
            return
        fit(e.width, e.height)

    root.bind("<Configure>", on_configure)

    root.protocol("WM_DELETE_WINDOW", on_close)
    apply_pin()
    refresh()
    poll()
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description="Numpad Cycle Counter")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "counts_survey.csv"),
                    help="CSV file to append to (default: counts_survey.csv next to this script)")
    ap.add_argument("--keypad-only", action="store_true", help="count only the numeric keypad, not the top digit row")
    args = ap.parse_args()
    if sys.platform != "darwin":
        sys.exit("Numpad Cycle Counter currently supports macOS only.")
    try:
        run_ui(args.out, args.keypad_only)
    except ImportError as err:
        sys.exit("Missing dependency: %s\nInstall with: pip3 install -r requirements.txt" % err)


if __name__ == "__main__":
    main()
