#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""경계 위 클러스터링 채점. 예측 B1~B3 은 scripts/18-hicluster.sh 헤더에 있고 여기 없다."""
import csv
import glob
import io
import json
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

R = sys.argv[1] if len(sys.argv) > 1 else "results"
BP = os.environ.get("HC_DENSITY_BP", "1200")
LENGTHS = (os.environ.get("HC_LENGTHS") or "1 8 64 512 4096").split()
# F-009 가 경계 **아래**(d=0.5%)에서 본 값. 여기서 다시 재지 않고 비교 대상으로만 쓴다.
F009_COST_GAIN = 3.53
F009_BYTES_GAIN = 1115.0


def cell(arm, L):
    tag = "d%sL%s" % (BP, L)
    dv, sc = [], []
    for path in sorted(glob.glob(os.path.join(
            R, "profiles", "%s__hc%s_c*_r*.collapsed" % (arm, tag)))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        dv.append(a["dv_union_samples"])
        sc.append(a["scan_samples"])
    walls = []
    for path in sorted(glob.glob(os.path.join(R, "scan_%s__hc%s_r*.json" % (arm, tag)))):
        try:
            d = json.load(open(path, encoding="utf-8"))
            if d.get("median_s"):
                walls.append(float(d["median_s"]))
        except Exception:
            pass
    if not dv:
        return None
    return dict(n=len(dv), dv=st.median(dv), dv_rng=(min(dv), max(dv)),
                scan=st.median(sc) if sc else 0,
                wall=st.median(walls) if walls else None)


def containers(L):
    path = os.path.join(R, "containers_hc_d%sL%s.csv" % (BP, L))
    if not os.path.exists(path):
        return None
    rows = list(csv.DictReader(io.open(path, encoding="utf-8")))
    if not rows:
        return None
    kinds = {}
    for r in rows:
        k = r.get("container", "?")
        kinds[k] = kinds.get(k, 0) + 1
    def f(r, n):
        try: return float(r.get(n) or 0)
        except ValueError: return 0.0
    runs = [f(r, "runs") for r in rows]
    runs = [x for x in runs if x > 0]
    return dict(n=len(rows), kinds=kinds,
                runs=(sum(runs) / len(runs)) if runs else None,
                bytes=sum(f(r, "bytes") for r in rows))


p("=" * 100)
p("경계 위(d=%s bp)에서도 클러스터링이 먹히는가 — F-009 를 반대편에서 반복" % BP)
p("=" * 100)

p("")
p("─ B1  컨테이너가 bitmap → run 으로 넘어가는가 (전환은 L=4~8 사이 예측)")
p("%-8s %5s %-26s %10s %14s" % ("L", "개수", "컨테이너", "평균nRuns", "합계 바이트"))
cinfo = {}
for L in LENGTHS:
    c = containers(L)
    cinfo[L] = c
    if not c:
        p("%-8s (CSV 없음)" % ("L" + L)); continue
    p("%-8s %5d %-26s %10s %14s" %
      ("L" + L, c["n"], ", ".join("%s:%d" % kv for kv in sorted(c["kinds"].items())),
       ("%.1f" % c["runs"]) if c["runs"] else "-", f"{int(c['bytes']):,}"))
first_run = next((L for L in LENGTHS if cinfo.get(L) and
                  any(k.startswith("run") for k in cinfo[L]["kinds"])), None)
p("   run 이 처음 나타나는 L: %s   판정: %s"
  % (first_run or "없음",
     "맞음 — 예측 구간(4~8) 안" if first_run in ("8",) else
     ("빗나감 — L=%s 에서 전환" % first_run if first_run else "★빗나감 — 끝까지 run 이 안 나온다")))

p("")
p("─ B2 ★판정용★  비용 이득이 F-009 의 %.2f배보다 작은가 (2배 미만 예측)" % F009_COST_GAIN)
p("%-8s %10s %18s %10s %10s" % ("L", "DV 샘플", "범위", "DV/스캔", "wall(s)"))
base = {}
for L in LENGTHS:
    c = cell("baseline", L)
    base[L] = c
    if not c:
        p("%-8s (프로파일 없음)" % ("L" + L)); continue
    p("%-8s %10.0f %8.0f ~ %-8.0f %9.1f%% %10s"
      % ("L" + L, c["dv"], c["dv_rng"][0], c["dv_rng"][1],
         c["dv"] / c["scan"] * 100 if c["scan"] else 0,
         ("%.4f" % c["wall"]) if c["wall"] else "-"))

lo, hi = LENGTHS[0], LENGTHS[-1]
if base.get(lo) and base.get(hi):
    gain = base[lo]["dv"] / base[hi]["dv"]
    ov = not (base[lo]["dv_rng"][0] > base[hi]["dv_rng"][1] or
              base[hi]["dv_rng"][0] > base[lo]["dv_rng"][1])
    p("   L%s → L%s 이득 **%.2f배**   범위 겹침: %s" % (lo, hi, gain, "예" if ov else "아니오"))
    if ov:
        p("   판정: 판정 불가 — 범위가 겹친다. 규칙대로 차이를 주장하지 않는다.")
    else:
        p("   F-009(경계 아래) %.2f배와 비교: %s" % (F009_COST_GAIN, "더 작다" if gain < F009_COST_GAIN else "더 크다"))
        p("   판정: %s" % ("맞음 — 경계 위에서는 이득이 작다. 처방에 밀도 조건이 필요하다." if gain < 2.0
                          else "★빗나감 — %.2f배다. 경계 위에서도 이득이 크다." % gain))

p("")
p("─ B3  저장 이득이 F-009 의 %.0f배보다 큰가" % F009_BYTES_GAIN)
if cinfo.get(lo) and cinfo.get(hi) and cinfo[hi]["bytes"]:
    bg = cinfo[lo]["bytes"] / cinfo[hi]["bytes"]
    p("   %s B → %s B = **%.0f배**" % (f"{int(cinfo[lo]['bytes']):,}", f"{int(cinfo[hi]['bytes']):,}", bg))
    p("   판정: %s" % ("맞음 — 경계 위에서 저장 이득이 더 크다" if bg > F009_BYTES_GAIN
                      else "빗나감 — %.0f배로 F-009 보다 작다" % bg))
else:
    p("   컨테이너 CSV 가 모자라 판정 불가")

p("")
p("─ 덤: 패치와 겹치는가 (F-023 이 경계 아래에서 본 것)")
for L in (lo, hi):
    b, q = base.get(L), cell("patched", L)
    if b and q:
        p("   L%-5s baseline %6.0f → patched %6.0f   %5.2f배" % (L, b["dv"], q["dv"], b["dv"] / q["dv"] if q["dv"] else 0))
p("=" * 100)
