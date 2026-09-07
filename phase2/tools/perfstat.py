#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`perf stat -x,` 출력 파서. qperf.py 와 rebound.py 가 같이 쓴다."""
import io, os, re, glob, statistics as st

def parse(path):
    """{event: value} 반환. 못 읽으면 {}."""
    out = {}
    try:
        for line in io.open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line or line.startswith("#"): continue
            f = line.split(",")
            if len(f) < 3: continue
            v, ev = f[0], f[2]
            if v in ("<not counted>", "<not supported>", ""): continue
            try: out[ev] = float(v)
            except ValueError: pass
    except IOError:
        pass
    return out

def collect(results, pattern):
    """pattern 은 glob. 라운드별 dict 리스트를 돌려준다."""
    rows = []
    for p in sorted(glob.glob(os.path.join(results, pattern))):
        d = parse(p)
        if d.get("cycles"):
            m = re.search(r"_r(\d+)\.txt$", p)
            d["_rep"] = int(m.group(1)) if m else 0
            rows.append(d)
    return rows

def med(rows, ev):
    vals = [r[ev] for r in rows if ev in r]
    return st.median(vals) if vals else None

def rng(rows, ev):
    vals = [r[ev] for r in rows if ev in r]
    return (min(vals), max(vals)) if vals else (None, None)
