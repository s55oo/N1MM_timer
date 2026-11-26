"""Tk window for N1MM_timer."""
from __future__ import annotations

import os
import sys
import tkinter as tk
import tkinter.simpledialog  # noqa: F401  (tk.simpledialog)
from tkinter import filedialog, messagebox, ttk

from . import __version__, config, rules
from .core import Watcher
from .n1mm import Listeners, parse_ports
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
        self.listener = Listeners(parse_ports(cp.get("udp", "port")), self.watcher.handle_packet,
                                  cp.get("udp", "bind")).start()
        self.db_thread = self.watcher.start_db_poller()
        self.sender = None
        if cp.getboolean("pingpong_alert", "enabled"):
            from .pingpong import Sender
            self.sender = Sender(cp.get("pingpong_alert", "host"), cp.getint("pingpong_alert", "port"),
                                 cp.getint("pingpong_alert", "ttl_s"))
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
        r.geometry("1180x800")
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
        ttk.Label(top, text="Signal from").pack(side="left", padx=(10, 0))
        self.v_src = tk.StringVar(value=self.watcher.settings.signal_source)
        cb3 = ttk.Combobox(top, textvariable=self.v_src, width=9, state="readonly",
                           values=["run1run2", "radionr", "station"])
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

        ttk.Label(r, text="Stations (N1MM radios on the network and PCs in the log)", padding=(8, 2)).pack(anchor="w")
        scols = ("station", "radio", "op", "band", "mode", "freq", "runsp", "tx", "signal", "rule",
                 "last", "qsos", "seen")
        heads = ("Station", "R", "Op", "Band", "Mode", "Freq kHz", "Run/S&P", "TX", "Signal", "Band rule",
                 "Last QSO", "QSOs", "Seen")
        widths = (110, 30, 80, 50, 55, 85, 65, 140, 60, 300, 130, 50, 55)
        self.st_tree = ttk.Treeview(r, columns=scols, show="headings", height=6)
        for c, h, w in zip(scols, heads, widths):
            self.st_tree.heading(c, text=h)
            self.st_tree.column(c, width=w, stretch=(c == "rule"), anchor="w")
        self.st_tree.tag_configure("tx", background="#fde293")
        self.st_tree.tag_configure("hold", foreground=AMBER)
        self.st_tree.tag_configure("last", foreground=AMBER)
        self.st_tree.tag_configure("full", foreground=RED)
        self.st_tree.tag_configure("warn", background="#fce8e6", foreground=RED)
        self.st_tree.tag_configure("stale", foreground=GREY)
        self.st_tree.pack(fill="x", padx=6)

        ttk.Label(r, text="INFO – a radio is on a band where a QSO now would break the rule (nothing logged yet)", padding=(8, 2)).pack(anchor="w")
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
        ttk.Button(bar, text="Follow N1MM log", command=self.follow_db).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="N1MM database…", command=self.choose_db).pack(side="left", padx=4)
        ttk.Button(bar, text="Import Cabrillo…", command=self.import_cbr).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear ledger…", command=self.clear).pack(side="right")

    # ---- actions --------------------------------------------------------------
    def on_settings(self, _evt=None):
        s = self.watcher.settings
        s.profile, s.category, s.signal_source = self.v_profile.get(), self.v_cat.get(), self.v_src.get()
        self.cp.set("contest", "profile", s.profile)
        self.cp.set("contest", "category", s.category)
        self.cp.set("contest", "signal_source", s.signal_source)
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
        self.watcher.select_db(p, pick.nr)
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

    def follow_db(self):
        """Follow whatever log N1MM has open on this PC (App Info packet, else newest log)."""
        self.watcher.select_db()
        self.cp.set("n1mm_db", "path", "auto")
        self.cp.set("n1mm_db", "contest_nr", "")
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
        errs = self.listener.errors()
        err = ("  ⚠ " + " | ".join(errs)) if errs else ""
        self.l_info.config(text=f"N1MM contest: {snap.contest_name or '?'}  →  {prof.title if prof else 'no profile'}"
                                f"   |   UDP {self.listener.describe()}: {snap.packets} packets, {age}"
                                f"   |   {snap.qso_count} QSOs"
                                f"   |   {self.sender.describe() if self.sender else 'PingPong alerts off'}{err}")
        s = self.watcher.settings
        if self.watcher.db_status:
            dbtxt = self.watcher.db_status
        elif s.db_follow:
            dbtxt = ("Following N1MM log: waiting – no N1MM database found yet (check N1MM's Broadcast "
                     "Data > Application Info line, or use 'N1MM database…')")
        else:
            dbtxt = "N1MM database: not used (UDP only)"
        self.l_db.config(text=dbtxt)
        open_faults = [f for f, a in snap.faults if not a]
        self.open_faults = len(open_faults)
        if prof is None and snap.lamp_state and snap.lamp_state["category"] == "activity":
            self.banner.config(text="CQ WW – no band-change limit for this category", bg=GREY)
        elif prof is None:
            self.banner.config(text="Pick a profile (or start N1MM with a CQ WW / WPX contest)", bg=GREY)
        elif open_faults:
            self.banner.config(text=f"⚠ {len(open_faults)} BAND-CHANGE FAULT(S) – check and acknowledge", bg=RED)
        elif snap.warnings:
            self.banner.config(text="INFO – a radio is on a band where a QSO now would break the rule", bg=AMBER)
        else:
            self.banner.config(text="OK – no band-change faults", bg=GREEN)
        if snap.new_faults and self.cp.getboolean("alerts", "sound"):
            beep()

        self._tiles(snap, now)
        self._stations(snap, now)

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
        if self.sender:
            self.sender.tick(snap)

    def _stations(self, snap, now):
        existing = set(self.st_tree.get_children())
        want = set()
        for i, r in enumerate(snap.stations):
            iid = f"{r.station}|{r.radionr}"
            want.add(iid)
            runsp = {True: "Run", False: "S&P"}.get(r.running, "")
            tx = ("TX " + r.tx_caption).strip() if r.transmitting else ""
            seen = "log only" if r.seen_s is None else f"{r.seen_s:.0f}s"
            last = f"{r.last_qso:%H:%M} {r.last_call}" if r.last_qso else ""
            rule = r.warning or r.rule_text
            vals = (r.station, r.radionr, r.op, f"{r.band} m" if r.band else "", r.mode,
                    f"{r.freq_khz:.2f}" if r.freq_khz else "", runsp, tx, r.signal, rule, last, r.qsos, seen)
            tags = []
            if r.stale:
                tags.append("stale")
            elif r.warning:
                tags.append("warn")
            elif r.transmitting:
                tags.append("tx")
            elif r.rule_level in ("hold", "last", "full"):
                tags.append(r.rule_level)
            if iid in existing:
                self.st_tree.item(iid, values=vals, tags=tags)
                self.st_tree.move(iid, "", i)
            else:
                self.st_tree.insert("", i, iid=iid, values=vals, tags=tags)
        for iid in existing - want:
            self.st_tree.delete(iid)

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
