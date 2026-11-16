"""Tk window for N1MM_timer."""
from __future__ import annotations

import os
import sys
import tkinter as tk
import tkinter.simpledialog  # noqa: F401  (tk.simpledialog)
from tkinter import filedialog, messagebox, ttk

from . import __version__, config, rules
from .core import Watcher
from .n1mm import UdpListener
from .rules import utcnow
from .store import Store

GREEN, AMBER, RED, GREY = "#1e8e3e", "#e37400", "#d93025", "#5f6368"
FLASH_A, FLASH_B = "#d93025", "#3c0d0d"


def beep():
    if sys.platform == "win32":
        try:
            import winsound
            winsound.Beep(1200, 250)
            winsound.Beep(900, 250)
            return
        except Exception:
            pass
    print("\a", end="", flush=True)


class App:
    def __init__(self, root: tk.Tk, cp):
        self.root, self.cp = root, cp
        self.store = Store(os.path.join(config.app_dir(), "timer.db"))
        self.watcher = Watcher(self.store, config.settings(cp))
        self.listener = UdpListener(cp.getint("udp", "port"), self.watcher.handle_packet, cp.get("udp", "bind"))
        self.listener.start()
        self.db_thread = self.watcher.start_db_poller()
        self.sender = None
        if cp.getboolean("pingpong", "enabled"):
            from .pingpong import Sender
            self.sender = Sender(cp.get("pingpong", "host"), cp.getint("pingpong", "port"))
        self.flash_on = False
        self.open_faults = 0
        self.tick_n = 0
        self.tiles: dict = {}
        self._build()
        self.root.after(200, self.tick)
        self.root.after(500, self.flash)

    # ---- layout -------------------------------------------------------------
    def _build(self):
        r = self.root
        r.title(f"N1MM_timer {__version__} – band-change watcher")
        r.geometry("980x640")
        r.minsize(760, 480)
        self.base_bg = r.cget("bg")

        top = ttk.Frame(r, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Profile").pack(side="left")
        self.v_profile = tk.StringVar(value=self.watcher.settings.profile)
        cb = ttk.Combobox(top, textvariable=self.v_profile, width=16, state="readonly",
                          values=["auto"] + list(rules.PROFILES))
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", self.on_settings)
        ttk.Label(top, text="Category").pack(side="left", padx=(10, 0))
        self.v_cat = tk.StringVar(value=self.watcher.settings.category)
        cb2 = ttk.Combobox(top, textvariable=self.v_cat, width=4, state="readonly", values=["MS", "M2"])
        cb2.pack(side="left", padx=4)
        cb2.bind("<<ComboboxSelected>>", self.on_settings)
        ttk.Label(top, text="M2 TX from").pack(side="left", padx=(10, 0))
        self.v_src = tk.StringVar(value=self.watcher.settings.m2_signal_source)
        cb3 = ttk.Combobox(top, textvariable=self.v_src, width=9, state="readonly",
                           values=["run1run2", "station", "radionr"])
        cb3.pack(side="left", padx=4)
        cb3.bind("<<ComboboxSelected>>", self.on_settings)
        self.l_clock = ttk.Label(top, font=("Consolas", 14, "bold"))
        self.l_clock.pack(side="right")

        self.l_info = ttk.Label(r, padding=(8, 0), foreground=GREY)
        self.l_info.pack(fill="x")
        self.l_db = ttk.Label(r, padding=(8, 0), foreground=GREY)
        self.l_db.pack(fill="x")

        self.banner = tk.Label(r, text="", font=("Segoe UI", 16, "bold"), fg="white", bg=GREEN, pady=6)
        self.banner.pack(fill="x", padx=6, pady=4)

        self.tile_frame = ttk.Frame(r, padding=6)
        self.tile_frame.pack(fill="x")

        ttk.Label(r, text="Live warnings (radio band vs. rules – nothing logged yet)", padding=(8, 2)).pack(anchor="w")
        self.warn = tk.Listbox(r, height=3, fg=AMBER, font=("Segoe UI", 10, "bold"))
        self.warn.pack(fill="x", padx=6)

        ttk.Label(r, text="Faults from logged QSOs", padding=(8, 2)).pack(anchor="w")
        cols = ("time", "kind", "signal", "station", "text", "ack")
        self.tree = ttk.Treeview(r, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (130, 110, 70, 100, 480, 50)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, stretch=(c == "text"))
        self.tree.tag_configure("open", foreground=RED)
        self.tree.tag_configure("acked", foreground=GREY)
        self.tree.pack(fill="both", expand=True, padx=6)

        bar = ttk.Frame(r, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="Acknowledge selected", command=self.ack_sel).pack(side="left")
        ttk.Button(bar, text="Acknowledge all", command=self.ack_all).pack(side="left", padx=4)
        ttk.Button(bar, text="N1MM database…", command=self.choose_db).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Import Cabrillo…", command=self.import_cbr).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear ledger…", command=self.clear).pack(side="right")

    # ---- actions --------------------------------------------------------------
    def on_settings(self, _evt=None):
        s = self.watcher.settings
        s.profile, s.category, s.m2_signal_source = self.v_profile.get(), self.v_cat.get(), self.v_src.get()
        self.cp.set("contest", "profile", s.profile)
        self.cp.set("contest", "category", s.category)
        self.cp.set("contest", "m2_signal_source", s.m2_signal_source)
        try:
            config.save(self.cp)
        except OSError:
            pass
        self.watcher.dirty.set()

    def ack_sel(self):
        ids = list(self.tree.selection())
        if ids:
            self.watcher.ack(ids)

    def ack_all(self):
        self.watcher.ack_all()

    def choose_db(self):
        start = os.path.join(os.path.expanduser("~"), "Documents", "N1MM Logger+", "Databases")
        p = filedialog.askopenfilename(title="N1MM Logger+ database (read-only)", initialdir=start,
                                       filetypes=[("N1MM database", "*.s3db"), ("All", "*.*")])
        if not p:
            return
        from . import n1mmdb
        try:
            cs = [c for c in n1mmdb.contests(n1mmdb.connect(p))]
        except Exception as e:
            messagebox.showerror("N1MM database", f"Cannot read {p}:\n{e}")
            return
        with_q = [c for c in cs if c.qsos] or cs
        if not with_q:
            messagebox.showerror("N1MM database", "No contest found in this database.")
            return
        pick = with_q[-1]
        if len(with_q) > 1:
            txt = "\n".join(c.label() for c in with_q)
            nr = tk.simpledialog.askinteger("Contest", f"Contests in this database:\n{txt}\n\nContestNR to watch:",
                                            initialvalue=pick.nr, parent=self.root)
            pick = next((c for c in with_q if c.nr == nr), pick)
        s = self.watcher.settings
        s.db_path, s.db_contest_nr = p, pick.nr
        self.watcher._db, self.watcher.db_info, self.watcher._db_seen, self.watcher._db_version = None, None, {}, None
        self.cp.set("n1mm_db", "path", p)
        self.cp.set("n1mm_db", "contest_nr", str(pick.nr))
        try:
            config.save(self.cp)
        except OSError:
            pass
        if self.db_thread is None:
            self.db_thread = self.watcher.start_db_poller()
        else:
            self.watcher.sync_db()

    def import_cbr(self):
        p = filedialog.askopenfilename(title="Cabrillo log", filetypes=[("Cabrillo", "*.cbr *.log *.txt"), ("All", "*.*")])
        if p:
            messagebox.showinfo("Import", self.watcher.import_cabrillo(p))

    def clear(self):
        if messagebox.askyesno("Clear ledger", "Delete all QSOs and acknowledgements from the watcher's "
                               "ledger?\n(N1MM's own log is not touched.)"):
            self.store.clear()
            self.watcher.seen_faults.clear()
            self.watcher.dirty.set()

    # ---- refresh --------------------------------------------------------------
    def tick(self):
        try:
            self.refresh()
        finally:
            self.root.after(1000, self.tick)

    def refresh(self):
        now = utcnow()
        snap = self.watcher.snapshot(now)
        self.l_clock.config(text=f"{now:%H:%M:%S}z")
        prof = snap.profile
        age = "no packets yet" if snap.last_packet_age is None else f"last packet {snap.last_packet_age:.0f}s ago"
        err = f"  ⚠ {self.listener.error}" if self.listener.error else ""
        self.l_info.config(text=f"N1MM contest: {snap.contest_name or '?'}  →  {prof.title if prof else 'no profile'}"
                                f"   |   UDP {self.listener.port}: {snap.packets} packets, {age}"
                                f"   |   {snap.qso_count} QSOs{err}")
        self.l_db.config(text=(self.watcher.db_status or "N1MM database: not used (UDP only)"))
        open_faults = [f for f, a in snap.faults if not a]
        self.open_faults = len(open_faults)
        if prof is None:
            self.banner.config(text="Pick a profile (or start N1MM with a CQ WW / WPX contest)", bg=GREY)
        elif open_faults:
            self.banner.config(text=f"⚠ {len(open_faults)} BAND-CHANGE FAULT(S) – check and acknowledge", bg=RED)
        elif snap.warnings:
            self.banner.config(text="Careful – a radio is on a band where a QSO now would break the rule", bg=AMBER)
        else:
            self.banner.config(text="OK – no band-change faults", bg=GREEN)
        if snap.new_faults and self.cp.getboolean("alerts", "sound"):
            beep()

        self._tiles(snap, now)

        self.warn.delete(0, "end")
        for st, sig, text in snap.warnings:
            self.warn.insert("end", f"{st} [{sig}]  {text}")

        existing = set(self.tree.get_children())
        want = set()
        for f, acked in sorted(snap.faults, key=lambda x: x[0].qso.ts, reverse=True):
            want.add(f.id)
            vals = (f"{f.qso.ts:%m-%d %H:%M:%S}", f.kind, f.signal, f.qso.station, f.text, "✓" if acked else "")
            tag = ("acked",) if acked else ("open",)
            if f.id in existing:
                self.tree.item(f.id, values=vals, tags=tag)
            else:
                self.tree.insert("", "end", iid=f.id, values=vals, tags=tag)
        for iid in existing - want:
            self.tree.delete(iid)

        self.tick_n += 1
        if self.sender and (self.tick_n % 2 == 0 or snap.new_faults):
            self.sender.send(snap)

    def _tiles(self, snap, now):
        status = snap.result.status
        for key in list(self.tiles):
            if key not in status:
                self.tiles.pop(key)[0].destroy()
        for key in sorted(status):
            s = status[key]
            if key not in self.tiles:
                fr = tk.Frame(self.tile_frame, bd=2, relief="groove", padx=10, pady=6)
                fr.pack(side="left", padx=4, fill="y")
                l1 = tk.Label(fr, font=("Segoe UI", 18, "bold"))
                l1.pack(anchor="w")
                l2 = tk.Label(fr, font=("Segoe UI", 11))
                l2.pack(anchor="w")
                self.tiles[key] = (fr, l1, l2)
            fr, l1, l2 = self.tiles[key]
            l1.config(text=f"{key}  {s.band or '–'} m")
            prof = snap.profile
            if prof.rule == rules.TEN_MIN:
                if s.free_at and now < s.free_at:
                    left = int((s.free_at - now).total_seconds())
                    l2.config(text=f"held until {s.free_at:%H:%M:%S}  ({left // 60}:{left % 60:02d})", fg=AMBER)
                else:
                    l2.config(text="free to change band", fg=GREEN)
            else:
                rem = s.remaining()
                color = GREEN if rem > 2 else AMBER if rem > 0 else RED
                l2.config(text=f"{s.used_this_hour}/{s.limit} changes in {now:%H}z – {rem} left", fg=color)

    def flash(self):
        try:
            if self.open_faults:
                self.flash_on = not self.flash_on
                bg = FLASH_A if self.flash_on else FLASH_B
                self.root.configure(bg=bg)
                self.banner.config(bg=bg)
            elif self.root.cget("bg") != self.base_bg:
                self.root.configure(bg=self.base_bg)
        finally:
            self.root.after(500, self.flash)


def run(cp) -> int:
    root = tk.Tk()
    App(root, cp)
    root.mainloop()
    return 0
