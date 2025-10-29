#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Packaging line DES (layout as in diagram):
CaseFormer → Separator → B1 → B2 → B3 → B4 → Glue/Date → Palletizer
- 6-case buffers between bottlers (vertical lanes in the diagram)
- Bottlers: 3–5 s each (random per case)
- Glue/Date: 1 s
- Palletizer: random ≤ 3.33 s (default Uniform[2.0, 3.33])
- Break handling with INCOMPLETE/COMPLETE pallet lock
- CSV outputs: pallet_events.csv, sim_summary.csv, line_log.csv

OPTIMIZED: Removed blind 0.1s retries. Stations only retry when unblocked.
"""

from dataclasses import dataclass
import heapq, csv, math, random

# ---------------- CONFIG ----------------
CONFIG = {
    # Shift + pauses
    "start_hm": (14, 45),
    "end_hm":   (22, 45),
    "prep_min": 15,                 # production starts at 15:00
    "clean_last_min": 15,
    "breaks": [(17,0,17,15),(19,0,19,20),(21,0,21,10)],
    "downtimes": [],                # optional: [(16,30,16,40)]

    # Pallet
    "cases_per_pallet": 108,

    # Case forming path (fast so it won't bottleneck unless you want it to)
    "t_caseformer": 2.5,            # s/case
    "t_separator":  3.0,            # s/case

    # Bottlers (3–5 s each; set as Uniform[min,max])
    "bottler_range": (3.0, 5.0),    # applies to B1..B4

    # Glue/Date
    "t_glue": 1.0,                  # s/case

    # Palletizer (≤ 3.33 s; choose distribution + params)
    "palletizer_dist": "uniform",   # "uniform" or "tri"
    "palletizer_params": (2.0, 3.3333333333),   # if uniform: (lo, hi); if tri: (a,m,b) with b<=3.33

    # Buffers [CF→Sep, Sep→B1, B1→B2, B2→B3, B3→B4, B4→Glue, Glue→Pallet]
    "buffers": [32, 32, 6, 6, 6, 16, 64],  # 6 between bottlers matches the vertical lanes

    # Randomness
    "seed": 123,
    "jitter_pct": 0.00,             # extra ±% jitter (set 0 for clean tests)
}
# --------------- END CONFIG ---------------

def to_sec(h, m): return h*3600 + m*60
def hhmmss(t):
    t = int(t) % (24*3600)
    h = t//3600; m=(t%3600)//60; s=t%60
    return f"{h:02d}:{m:02d}:{s:02d}"

def windows_from_shift(cfg):
    s = to_sec(*cfg["start_hm"])
    e = to_sec(*cfg["end_hm"])
    run_start = s + cfg["prep_min"]*60
    run_end   = e - cfg["clean_last_min"]*60
    intervals = [(run_start, run_end)]
    for h1,m1,h2,m2 in cfg["breaks"]:
        bs, be = to_sec(h1,m1), to_sec(h2,m2)
        new=[]
        for a,b in intervals:
            if be<=a or bs>=b: new.append((a,b))
            else:
                if a<bs: new.append((a,bs))
                if be<b: new.append((be,b))
        intervals=new
    return intervals, run_start, e

def in_window(t, windows):
    for a,b in windows:
        if a<=t<b: return True
    return False

def tri(a,m,b):
    u=random.random()
    c=(m-a)/(b-a)
    return a + math.sqrt(u*(b-a)*(m-a)) if u<c else b - math.sqrt((1-u)*(b-a)*(b-m))

def jitter(x, pct):
    if pct<=0: return x
    j = 1 + random.uniform(-pct, pct)
    return max(0.001, x*j)

@dataclass
class ServerPool:
    name: str
    servers: int
    busy: int = 0

class Sim:
    def __init__(self, cfg):
        random.seed(cfg["seed"])
        self.cfg = cfg
        self.windows, self.run_start, self.shift_end = windows_from_shift(cfg)
        self.now   = self.run_start

        # Serial buffers after CF/Sep path through to palletizer
        # indices: 0=CF→Sep, 1=Sep→B1, 2=B1→B2, 3=B2→B3, 4=B3→B4, 5=B4→Glue, 6=Glue→Pallet
        self.bufcap = cfg["buffers"]
        self.buf = [0]*len(self.bufcap)

        # Stations (single server each; parallelism can be added if needed)
        self.cf   = ServerPool("CaseFormer", 1)
        self.sep  = ServerPool("Separator",  1)
        self.b    = [ServerPool(f"B{i+1}", 1) for i in range(4)]
        self.glue = ServerPool("GlueDate",   1)
        self.pal  = ServerPool("Palletizer", 1)

        # Events heap: (time, typ, payload)
        self.h=[]
        for name in ["CF","SEP","B1","B2","B3","B4","GLUE","PAL"]:
            heapq.heappush(self.h, (self.run_start, f"try_{name}", None))

        # schedule pauses
        for h1,m1,h2,m2 in cfg["breaks"]:
            bs, be = to_sec(h1,m1), to_sec(h2,m2)
            heapq.heappush(self.h, (bs, "pause_start", ("BREAK", bs, be)))
            heapq.heappush(self.h, (be, "pause_end",   ("BREAK", bs, be)))
        for h1,m1,h2,m2 in cfg.get("downtimes", []):
            ds, de = to_sec(h1,m1), to_sec(h2,m2)
            heapq.heappush(self.h, (ds, "pause_start", ("DOWNTIME", ds, de)))
            heapq.heappush(self.h, (de, "pause_end",   ("DOWNTIME", ds, de)))

        # Counters
        self.cases_out = 0
        self.pallets   = 0
        self.events    = []  # (pallet_seq, time_sec)
        self.log       = []  # pause log

        # Pallet lock during pause
        self.lock_active = False
        self.lock_target_cases = None

    # ----- time draws -----
    def t_cf(self):   return jitter(self.cfg["t_caseformer"], self.cfg["jitter_pct"])
    def t_sep(self):  return jitter(self.cfg["t_separator"],  self.cfg["jitter_pct"])
    def t_b(self):    # uniform [3,5]
        lo, hi = self.cfg["bottler_range"]
        return jitter(random.uniform(lo, hi), self.cfg["jitter_pct"])
    def t_glue(self): return jitter(self.cfg["t_glue"], self.cfg["jitter_pct"])
    def t_pal(self):
        if self.cfg["palletizer_dist"] == "tri":
            a,m,b = self.cfg["palletizer_params"]
            return jitter(tri(a,m,b), self.cfg["jitter_pct"])
        else:
            lo, hi = self.cfg["palletizer_params"]
            return jitter(random.uniform(lo, hi), self.cfg["jitter_pct"])

    # ----- helpers -----
    def can_run(self): return in_window(self.now, self.windows)

    # ----- CF -----
    def try_CF(self,_):
        # OPTIMIZED: No blind retries. Only retry when conditions change.
        if not self.can_run():
            return  # pause_end will schedule retry
        if self.cf.busy>=self.cf.servers:
            return  # done_CF will schedule retry
        if self.buf[0] >= self.bufcap[0]:
            return  # done_SEP will schedule retry when buffer has space
        dur = self.t_cf()
        self.cf.busy += 1
        heapq.heappush(self.h, (self.now+dur, "done_CF", None))
        
    def done_CF(self,_):
        self.cf.busy -= 1
        self.buf[0] += 1
        heapq.heappush(self.h, (self.now, "try_CF", None))   # Can immediately try again
        heapq.heappush(self.h, (self.now, "try_SEP", None))  # Notify downstream

    # ----- SEP -----
    def try_SEP(self,_):
        if not self.can_run():
            return
        if self.sep.busy>=self.sep.servers:
            return
        if self.buf[0] <= 0:
            return  # done_CF will notify us
        if self.buf[1] >= self.bufcap[1]:
            return  # done_B1 will notify us
        dur = self.t_sep()
        self.sep.busy += 1
        heapq.heappush(self.h, (self.now+dur, "done_SEP", None))
        
    def done_SEP(self,_):
        self.sep.busy -= 1
        self.buf[0] -= 1
        self.buf[1] += 1
        heapq.heappush(self.h, (self.now, "try_SEP", None))  # Can try again
        heapq.heappush(self.h, (self.now, "try_CF", None))   # Notify upstream (buffer freed)
        heapq.heappush(self.h, (self.now, "try_B1", None))   # Notify downstream

    # ----- Bottlers -----
    def try_B(self, k, name):
        pool = self.b[k]
        in_idx = 1+k
        out_idx = 2+k
        if not self.can_run():
            return
        if pool.busy>=pool.servers:
            return
        if self.buf[in_idx] <= 0:
            return  # Previous stage will notify us
        if self.buf[out_idx] >= self.bufcap[out_idx]:
            return  # Next stage will notify us
        dur = self.t_b()
        pool.busy += 1
        heapq.heappush(self.h, (self.now+dur, f"done_{name}", None))
        
    def done_B(self, k, name):
        pool = self.b[k]
        in_idx = 1+k
        out_idx = 2+k
        pool.busy -= 1
        self.buf[in_idx] -= 1
        self.buf[out_idx] += 1
        
        heapq.heappush(self.h, (self.now, f"try_{name}", None))  # Can try again
        
        # Notify upstream that buffer space freed
        prev = ["SEP","B1","B2","B3"][k]
        heapq.heappush(self.h, (self.now, f"try_{prev}", None))
        
        # Notify downstream that product available
        nxt = ["B2","B3","B4","GLUE"][k]
        heapq.heappush(self.h, (self.now, f"try_{nxt}", None))

    # ----- GLUE -----
    def try_GLUE(self,_):
        if not self.can_run():
            return
        if self.glue.busy>=self.glue.servers:
            return
        if self.buf[5] <= 0:
            return  # done_B4 will notify us
        if self.buf[6] >= self.bufcap[6]:
            return  # done_PAL will notify us
        dur = self.t_glue()
        self.glue.busy += 1
        heapq.heappush(self.h, (self.now+dur, "done_GLUE", None))
        
    def done_GLUE(self,_):
        self.glue.busy -= 1
        self.buf[5] -= 1
        self.buf[6] += 1
        heapq.heappush(self.h, (self.now, "try_GLUE", None))
        heapq.heappush(self.h, (self.now, "try_B4", None))   # Notify upstream
        heapq.heappush(self.h, (self.now, "try_PAL", None))  # Notify downstream

    # ----- PALLETIZER -----
    def try_PAL(self,_):
        if not self.can_run():
            return
        if self.pal.busy>=self.pal.servers:
            return
        if self.buf[6] <= 0:
            return  # done_GLUE will notify us
        dur = self.t_pal()
        self.pal.busy += 1
        heapq.heappush(self.h, (self.now+dur, "done_PAL", None))
        
    def done_PAL(self,_):
        self.pal.busy -= 1
        self.buf[6] -= 1
        self.cases_out += 1
        
        # lock enforcement: first pallet after pause must be the incomplete one
        if self.lock_active and self.lock_target_cases is not None and self.cases_out >= self.lock_target_cases:
            if self.cases_out % self.cfg["cases_per_pallet"] == 0:
                self.pallets += 1
                self.events.append((self.pallets, int(self.now)))
                print(f"Pallet {self.pallets} at {hhmmss(int(self.now))} [COMPLETE]")
            self.lock_active = False
            self.lock_target_cases = None
        else:
            if self.cases_out % self.cfg["cases_per_pallet"] == 0:
                self.pallets += 1
                self.events.append((self.pallets, int(self.now)))
                print(f"Pallet {self.pallets} at {hhmmss(int(self.now))}")
                
        heapq.heappush(self.h, (self.now, "try_PAL", None))   # Can try again
        heapq.heappush(self.h, (self.now, "try_GLUE", None))  # Notify upstream

    # ----- pause logic -----
    def _mark_incomplete(self, label, start_t, end_t):
        in_pallet = self.cases_out % self.cfg["cases_per_pallet"]
        if in_pallet > 0:
            current_idx = self.cases_out // self.cfg["cases_per_pallet"] + 1
            print(f"Pallet {current_idx} at {hhmmss(int(start_t))} [INCOMPLETE]")
            self.lock_active = True
            self.lock_target_cases = current_idx * self.cfg["cases_per_pallet"]
        print(f"{label}_START {hhmmss(int(start_t))} → {hhmmss(int(end_t))} | pallet_progress {in_pallet}/{self.cfg['cases_per_pallet']} (INCOMPLETE)")
        self.log.append((f"{label}_START", int(start_t), in_pallet, self.cfg["cases_per_pallet"]))
        
        # shift any done_* events that fall inside pause
        pause = end_t - start_t
        new=[]
        while self.h:
            t, typ, payload = heapq.heappop(self.h)
            if t>start_t and t<end_t and typ.startswith("done_"):
                t += pause
            new.append((t,typ,payload))
        for x in new: heapq.heappush(self.h, x)

    def _end_pause(self, label, end_t):
        print(f"{label}_END   {hhmmss(int(end_t))}")
        self.log.append((f"{label}_END", int(end_t), None, None))
        
        # Nudge all stations to check if they can run
        for name in ["try_CF","try_SEP","try_B1","try_B2","try_B3","try_B4","try_GLUE","try_PAL"]:
            heapq.heappush(self.h, (end_t, name, None))

    # ----- event loop -----
    def run(self):
        handlers_try = {
            "try_CF": self.try_CF,
            "try_SEP": self.try_SEP,
            "try_B1": lambda _ : self.try_B(0,"B1"),
            "try_B2": lambda _ : self.try_B(1,"B2"),
            "try_B3": lambda _ : self.try_B(2,"B3"),
            "try_B4": lambda _ : self.try_B(3,"B4"),
            "try_GLUE": self.try_GLUE,
            "try_PAL": self.try_PAL,
        }
        handlers_done = {
            "done_CF": self.done_CF,
            "done_SEP": self.done_SEP,
            "done_B1": lambda _ : self.done_B(0,"B1"),
            "done_B2": lambda _ : self.done_B(1,"B2"),
            "done_B3": lambda _ : self.done_B(2,"B3"),
            "done_B4": lambda _ : self.done_B(3,"B4"),
            "done_GLUE": self.done_GLUE,
            "done_PAL": self.done_PAL,
        }

        while self.h and self.now < self.shift_end:
            t, typ, payload = heapq.heappop(self.h)
            self.now = t
            if typ == "pause_start":
                label, ps, pe = payload
                self._mark_incomplete(label, ps, pe)
            elif typ == "pause_end":
                label, ps, pe = payload
                self._end_pause(label, pe)
            elif typ.startswith("try_"):
                handlers_try[typ](payload)
            elif typ.startswith("done_"):
                handlers_done[typ](payload)

        return {"cases_out": self.cases_out, "pallets_out": self.pallets,
                "events": self.events, "log": self.log}

# ----------------- run -----------------
if __name__ == "__main__":
    random.seed(CONFIG["seed"])
    sim = Sim(CONFIG)
    res = sim.run()

    with open("pallet_events.csv","w",newline="") as f:
        w=csv.writer(f); w.writerow(["pallet_seq","time_sec_from_midnight","clock_time"])
        for seq,t in res["events"]: w.writerow([seq,t,hhmmss(t)])

    with open("sim_summary.csv","w",newline="") as f:
        w=csv.writer(f)
        w.writerow(["cases_out", res["cases_out"]])
        w.writerow(["pallets_out", res["pallets_out"]])
        w.writerow(["bottler_range_s", CONFIG["bottler_range"]])
        w.writerow(["glue_time_s", CONFIG["t_glue"]])
        w.writerow(["palletizer", (CONFIG["palletizer_dist"], CONFIG["palletizer_params"])])
        w.writerow(["buffers", CONFIG["buffers"]])

    with open("line_log.csv","w",newline="") as f:
        w=csv.writer(f); w.writerow(["event","time_sec","clock","in_pallet_cases","pallet_size"])
        for e,t,a,b in res["log"]:
            w.writerow([e, t, hhmmss(t), a, b])