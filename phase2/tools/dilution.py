#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스캔에서 아낀 CPU 가 전체에서 얼마나 남는가 — 희석 계수.

F-018 은 `wall = 0.53 x 샘플` 을 냈다. 스캔 안에서 아낀 CPU 의 절반만 벽시계에 온다는 뜻이다.
outside_scan.py 가 보여준 것: 프로세스 CPU 의 **41.6% 가 JIT 컴파일**, 10.5% 가 클래스 로딩이다.
둘 다 패치가 건드릴 수 없는 고정비다. 고정비가 크면 스캔 안의 개선이 전체에서 희석된다.

여기서 직접 잰다:
    스캔 절감률  = 1 - scan(patched)/scan(baseline)
    전체 절감률  = 1 - total(patched)/total(baseline)
    희석 계수    = 전체 절감률 / 스캔 절감률
0.53 근처가 나오면 F-018 의 보정은 **고정비 희석**으로 설명된다.

⚠️ 탐색적 분해다. 예측을 걸고 잰 것이 아니라 이미 모은 프로파일을 다시 읽은 것이다.
⚠️ 그리고 이 수치는 **우리 측정 설계의 성질**이기도 하다 — JVM 을 새로 띄워 30회만
   돌리므로 JIT 이 30회에만 상각된다. 오래 사는 프로덕션 익스큐터라면 JIT 몫이 훨씬
   작고, 따라서 희석도 약하다. 그쪽에서는 보정이 1.0 에 가까울 수 있다.
"""
import glob
import io
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

R = sys.argv[1] if len(sys.argv) > 1 else "results"
EV = os.environ.get("EV", "cycles")
TAG = os.environ.get("QP_TAG", "d610")


def rounds(arm):
    out = {}
    for path in sorted(glob.glob(os.path.join(
            R, "profiles", "%s__qp%s_%s*_r*.collapsed" % (arm, TAG, EV)))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        rep = int(path.rsplit("_r", 1)[1].split(".")[0])
        out[rep] = (a["total_samples"], a["scan_samples"], a["dv_union_samples"])
    return out


b, q = rounds("baseline"), rounds("patched")
common = sorted(set(b) & set(q))
if not common:
    p("짝지을 라운드가 없다 (baseline %s / patched %s)" % (sorted(b), sorted(q)))
    sys.exit(1)

p("=" * 84)
p("고정비 희석 — 스캔에서 아낀 CPU 가 전체에서 얼마나 남는가 (%s 이벤트)" % EV)
p("=" * 84)
p("%-5s %11s %11s %11s %11s %10s %10s" %
  ("라운드", "전체(B)", "전체(P)", "스캔(B)", "스캔(P)", "스캔절감", "전체절감"))
p("-" * 78)
sc_g, tt_g = [], []
for r in common:
    tb, sb, _ = b[r]
    tq, sq, _ = q[r]
    sg = 1 - sq / sb
    tg = 1 - tq / tb
    sc_g.append(sg); tt_g.append(tg)
    p("r%-4d %11s %11s %11s %11s %9.1f%% %9.1f%%" %
      (r, f"{tb:,}", f"{tq:,}", f"{sb:,}", f"{sq:,}", sg * 100, tg * 100))

p("-" * 78)
msg, mtg = st.median(sc_g), st.median(tt_g)
p("중앙값                                                   %9.1f%% %9.1f%%" % (msg * 100, mtg * 100))
p("")
if msg > 0:
    dil = mtg / msg
    p("희석 계수 = 전체절감 / 스캔절감 = %.1f%% / %.1f%% = **%.3f**" % (mtg * 100, msg * 100, dil))
    p("라운드별: %s" % ", ".join("%.3f" % (t / s) for s, t in zip(sc_g, tt_g) if s > 0))
    p("")
    p("F-018 의 보정 0.53 과 비교: %s"
      % ("가깝다 — 보정의 정체는 고정비 희석이다" if 0.40 <= dil <= 0.70
         else "다르다 (%.3f). 고정비 희석만으로는 설명되지 않는다" % dil))
else:
    p("스캔 절감이 0 이하다 — 희석을 계산할 수 없다")
p("=" * 84)
