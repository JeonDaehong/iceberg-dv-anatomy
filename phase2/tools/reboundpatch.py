#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""반등 구간의 패치 채점. 예측 T1~T3 은 scripts/21-reboundpatch.sh 헤더에 있고 여기 없다."""
import glob
import io
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

R = sys.argv[1] if len(sys.argv) > 1 else "results"
TAGS = (os.environ.get("RP_TAGS") or "rp800 rp5000").split()
LABEL = {"rp800": "d=8%  (bitmap)", "rp5000": "d=50% (bitmap)"}
NOISE = 9.7   # F-010


def cell(arm, tag):
    dv, sc = [], []
    for path in sorted(glob.glob(os.path.join(
            R, "profiles", "%s__%s_c*_r*.collapsed" % (arm, tag)))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        dv.append(a["dv_union_samples"])
        sc.append(a["scan_samples"])
    walls = []
    for path in sorted(glob.glob(os.path.join(R, "scan_%s__%s_r*.json" % (arm, tag)))):
        try:
            d = json.load(io.open(path, encoding="utf-8"))
            if d.get("median_s"):
                walls.append(float(d["median_s"]))
        except Exception:
            pass
    if not dv:
        return None
    return dict(n=len(dv), dv=st.median(dv), dv_rng=(min(dv), max(dv)),
                scan=st.median(sc), wall=st.median(walls) if walls else None)


def overlaps(a, b):
    return not (a["dv_rng"][0] > b["dv_rng"][1] or b["dv_rng"][0] > a["dv_rng"][1])


p("=" * 100)
p("반등 구간에서 패치는 어떻게 되는가 (두 밀도 모두 bitmap — 컨테이너 축은 죽어 있다)")
p("=" * 100)
p("")
p("%-16s %-9s %3s %9s %16s %9s %10s" % ("밀도", "arm", "n", "DV", "범위", "DV/스캔", "wall(s)"))

G = {}
for tag in TAGS:
    for arm in ("baseline", "patched"):
        c = cell(arm, tag)
        G[(tag, arm)] = c
        if not c:
            p("%-16s %-9s (프로파일 없음)" % (LABEL.get(tag, tag), arm)); continue
        p("%-16s %-9s %3d %9.0f %7.0f ~ %-7.0f %8.1f%% %10s"
          % (LABEL.get(tag, tag), arm, c["n"], c["dv"], c["dv_rng"][0], c["dv_rng"][1],
             c["dv"] / c["scan"] * 100 if c["scan"] else 0,
             ("%.4f" % c["wall"]) if c["wall"] else "-"))

lo, hi = TAGS[0], TAGS[1]

p("")
p("─ T1 [설계 검증]  baseline 의 반등이 재현되는가")
a, b = G.get((lo, "baseline")), G.get((hi, "baseline"))
if a and b:
    ov = overlaps(a, b)
    p("   baseline  %.0f → %.0f  (%+.1f%%),  범위 겹침: %s"
      % (a["dv"], b["dv"], (b["dv"] / a["dv"] - 1) * 100, "예" if ov else "아니오"))
    if ov:
        p("   판정: ★빗나감 — 범위가 겹친다. 이 조건에서는 반등을 못 잰다. 아래는 판정하지 않는다.")
        p("=" * 100); sys.exit(0)
    if b["dv"] <= a["dv"]:
        p("   판정: ★빗나감 — 오히려 줄었다. 반등이 없다.")
        p("=" * 100); sys.exit(0)
    p("   판정: **맞음** — 반등이 재현된다.")
else:
    p("   baseline 프로파일이 모자라다"); p("=" * 100); sys.exit(0)

p("")
p("─ T2 ★판정용★  패치가 반등을 없애는가 (두 밀도의 patched 가 노이즈 %.1f%% 안)" % NOISE)
x, y = G.get((lo, "patched")), G.get((hi, "patched"))
if x and y:
    diff = (y["dv"] / x["dv"] - 1) * 100
    ov = overlaps(x, y)
    p("   patched   %.0f → %.0f  (%+.1f%%),  범위 겹침: %s" % (x["dv"], y["dv"], diff, "예" if ov else "아니오"))
    if abs(diff) <= NOISE or ov:
        p("   판정: **맞음** — 패치 경로에는 반등이 없다.")
        p("         F-037 이 지목한 원인(행마다의 데이터 의존 분기)이 패치에는 없기 때문이다.")
    else:
        p("   판정: ★빗나감 — patched 도 %+.1f%% 움직인다. 분기 말고 다른 원인이 남아 있다." % diff)
        p("         F-037 의 결론을 '이 분기가 전부' 에서 좁혀야 한다.")

p("")
p("─ T3  패치의 개선 배율이 고삭제율에서 작아지는가")
gains = {}
for tag in TAGS:
    u, v = G.get((tag, "baseline")), G.get((tag, "patched"))
    if u and v and v["dv"]:
        gains[tag] = u["dv"] / v["dv"]
        p("   %-16s baseline %6.0f → patched %6.0f = **%.2f배**" % (LABEL.get(tag, tag), u["dv"], v["dv"], gains[tag]))
if len(gains) == 2:
    g1, g2 = gains[lo], gains[hi]
    p("   %.2f배 → %.2f배" % (g1, g2))
    p("   판정: %s" % ("**맞음** — 삭제가 많을수록 패치의 이득이 줄어든다. 벌크의 이점이 사라진다."
                      if g2 < g1 else
                      "★빗나감 — 오히려 커진다(%.2f → %.2f). 반등으로 비싸진 만큼을 패치가 회수한다."
                      % (g1, g2)))
p("=" * 100)
