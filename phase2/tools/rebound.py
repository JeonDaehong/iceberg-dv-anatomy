#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-008 반등 기전 채점. 예측 Q_P5~Q_P7 은 scripts/16-rebound.sh 헤더에 있고 여기 없다."""
import glob, io, os, re, sys, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from perfstat import collect, med, rng
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

R = sys.argv[1] if len(sys.argv) > 1 else "results"
TAGS = (os.environ.get("RB_TAGS") or "d800 d5000").split()
CYC_PER_MISS = 18.0
LO, HI = TAGS[0], TAGS[-1]

data = {t: collect(R, "qperf/rb_stat_%s_r*.txt" % t) for t in TAGS}
for t in TAGS:
    if not data[t]:
        p("결과 없음: %s" % t); sys.exit(1)

p("=" * 92)
p("F-008 bitmap 구간 반등 — 삭제 판정 분기가 범인인가")
p("=" * 92)
p("%-7s %5s %14s %14s %13s %12s %7s" %
  ("밀도", "n", "cycles", "instructions", "branches", "br-misses", "실패율"))
for t in TAGS:
    r = data[t]
    p("%-7s %5d %14.0f %14.0f %13.0f %12.0f %6.2f%%" %
      (t, len(r), med(r, "cycles"), med(r, "instructions"), med(r, "branches"),
       med(r, "branch-misses"), med(r, "branch-misses")/med(r, "branches")*100))

bm_lo, bm_hi = med(data[LO], "branch-misses"), med(data[HI], "branch-misses")
cy_lo, cy_hi = med(data[LO], "cycles"),        med(data[HI], "cycles")
in_lo, in_hi = med(data[LO], "instructions"),  med(data[HI], "instructions")
r_lo, r_hi = rng(data[LO], "branch-misses"), rng(data[HI], "branch-misses")

p("")
p("─ Q_P5 ★판정용★  d=50% 의 branch-misses 가 d=8% 보다 5% 이상 많은가")
p("   %-6s %14.0f   범위 %.0f ~ %.0f" % (LO, bm_lo, r_lo[0], r_lo[1]))
p("   %-6s %14.0f   범위 %.0f ~ %.0f" % (HI, bm_hi, r_hi[0], r_hi[1]))
gain = (bm_hi / bm_lo - 1) * 100
overlap = not (r_lo[1] < r_hi[0] or r_hi[1] < r_lo[0])
p("   차이 %+.1f%%   범위 겹침: %s" % (gain, "예" if overlap else "아니오"))
if overlap:
    p("   판정: 판정 불가 — 범위가 겹친다. 규칙대로 차이를 주장하지 않는다.")
    q5 = None
else:
    q5 = gain >= 5.0
    p("   판정: %s" % ("맞음 — 분기 실패가 실제로 늘어난다" if q5 else
                      "★빗나감 — 분기 실패가 안 는다. 반등의 범인은 분기가 아니다"))

p("")
p("─ Q_P6  그 추가 실패가 사이클 차이를 절반 이상 설명하는가")
dcyc, dmiss = cy_hi - cy_lo, bm_hi - bm_lo
if dcyc <= 0:
    p("   사이클이 오히려 줄었다 (%+.0f) — 설명할 격차가 없다." % dcyc)
    p("   판정: 해당 없음")
else:
    share = dmiss * CYC_PER_MISS / dcyc * 100
    p("   사이클 격차 %+.0f  |  추가 실패 %+.0f  |  x18 cycle = %+.0f  |  설명력 %.1f%%"
      % (dcyc, dmiss, dmiss * CYC_PER_MISS, share))
    p("   판정: %s" % ("맞음 — 분기가 주범이다" if share >= 50 else
                      "빗나감 — 분기가 늘긴 해도 비용의 주범은 아니다 (%.1f%%)" % share))

p("")
p("─ Q_P7  명령어 수는 어떻게 움직이나 (줄어드는데 사이클이 늘면 확실히 파이프라인이다)")
p("   %-6s instructions %14.0f   IPC %.2f" % (LO, in_lo, in_lo / cy_lo))
p("   %-6s instructions %14.0f   IPC %.2f" % (HI, in_hi, in_hi / cy_hi))
p("   명령어 %+.1f%%   사이클 %+.1f%%" % ((in_hi/in_lo-1)*100, (cy_hi/cy_lo-1)*100))
if in_hi < in_lo and cy_hi > cy_lo:
    p("   판정: 맞음 — 명령어는 줄었는데 사이클이 늘었다. 파이프라인 문제가 맞다.")
elif in_hi > in_lo and cy_hi > cy_lo:
    p("   판정: 빗나감 — 명령어도 같이 늘었다. 파이프라인이라고 단정할 수 없다.")
else:
    p("   판정: 예측한 모양이 아니다 (명령어 %+.1f%%, 사이클 %+.1f%%)"
      % ((in_hi/in_lo-1)*100, (cy_hi/cy_lo-1)*100))
# ── Q_P8: DV 구간 귀속 ────────────────────────────────────────────────────
def dv_branch_share(tag):
    """branch-misses 프로파일에서 DV 구간의 비중. (스캔 대비, 전체 대비) 두 쌍을 돌려준다.

    ⚠️ 분모를 섞으면 안 된다. 비중 비교는 **스캔 대비**(F-008 이 스캔 안의 이야기라서),
       perf stat 총량과 곱해 절대값을 낼 때는 **전체 대비**를 써야 한다.
    """
    of_scan, of_all = [], []
    for path in sorted(glob.glob(os.path.join(
            R, "profiles", "baseline__rb%s_branch_misses_r*.collapsed" % tag))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        if a.get("scan_samples") and a.get("total_samples"):
            of_scan.append(a["dv_pct_of_scan"])
            of_all.append(a["dv_pct_of_all"])
    return of_scan, of_all

p("")
p("─ Q_P8 ★귀속★  늘어난 분기 실패가 DV 체크 구간에서 났는가")
sh = {t: dv_branch_share(t) for t in TAGS}
if not sh[LO][0] or not sh[HI][0]:
    p("   프로파일이 없다 (LO=%d, HI=%d개) — 2단계를 안 돌렸거나 실패했다."
      % (len(sh[LO][0]), len(sh[HI][0])))
else:
    for t in TAGS:
        v, va = sh[t]
        p("   %-6s DV/스캔 %6.2f%%  [%.2f ~ %.2f]   DV/전체 %5.2f%%   n=%d"
          % (t, st.median(v), min(v), max(v), st.median(va), len(v)))
    m_lo, m_hi = st.median(sh[LO][0]), st.median(sh[HI][0])
    a_lo, a_hi = st.median(sh[LO][1]), st.median(sh[HI][1])
    ov = not (max(sh[LO][0]) < min(sh[HI][0]) or max(sh[HI][0]) < min(sh[LO][0]))
    p("   스캔 대비 차이 %+.2f%%p   범위 겹침: %s" % (m_hi - m_lo, "예" if ov else "아니오"))
    # 절대값 환산은 **전체 대비** 비중으로만 한다 (perf stat 과 분모를 맞춘다).
    dv_lo_abs, dv_hi_abs = bm_lo * a_lo / 100.0, bm_hi * a_hi / 100.0
    d_total, d_dv = bm_hi - bm_lo, dv_hi_abs - dv_lo_abs
    p("   DV 구간 절대 실패 %.0f → %.0f  (%+.0f)" % (dv_lo_abs, dv_hi_abs, d_dv))
    p("   총 추가 실패 %+.0f 중 DV 몫 %+.0f  (%.1f%%)"
      % (d_total, d_dv, d_dv / d_total * 100 if d_total else float("nan")))
    frac = d_dv / d_total if d_total else 0.0
    if ov:
        p("   판정: 판정 불가 — 비중의 범위가 겹친다.")
    elif m_hi > m_lo and frac >= 0.5:
        p("   판정: 맞음 — 반등의 기전이 확정된다. 삭제 판정 분기가 d=50% 에서 동전 던지기가 된다.")
    elif m_hi > m_lo:
        p("   판정: 절반만 맞음 — DV 비중은 올랐지만 추가 실패의 %.1f%% 만 DV 몫이다." % (frac * 100))
    else:
        p("   판정: ★빗나감 — DV 비중이 안 올랐다. 늘어난 실패는 DV 밖에서 났다.")
p("=" * 92)
