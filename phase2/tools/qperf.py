#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""실제 쿼리 경로 PMU 채점. 예측 Q_P1~Q_P4 는 scripts/15-qperf.sh 헤더에 있고 여기 없다.

⚠️ Q_P3 은 대상 밀도를 잘못 지정했으므로(스크립트 헤더 참고) 여기서 채점하지 않는다.
   그 축은 16-rebound.sh / tools/rebound.py 가 Q_P5~Q_P7 로 다시 친다.
"""
import glob, io, os, re, sys, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from perfstat import collect, med, rng
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

R = sys.argv[1] if len(sys.argv) > 1 else "results"
TAG = os.environ.get("QP_TAG", "d610")
CYC_PER_MISS = 18.0

arms = {a: collect(R, "qperf/stat_%s_%s_r*.txt" % (a, TAG)) for a in ("baseline", "patched")}
if not arms["baseline"] or not arms["patched"]:
    p("perf stat 결과 부족: baseline=%d patched=%d"
      % (len(arms["baseline"]), len(arms["patched"]))); sys.exit(1)

EVS = ["cycles", "instructions", "branches", "branch-misses", "cache-misses"]
p("=" * 96)
p("실제 쿼리 경로의 하드웨어 카운터 — F-028 을 마이크로벤치 밖으로")
p("=" * 96)
p("테이블 %s · spark-submit 전체를 감쌌다 (JIT·GC·기동 포함 → arm 간 차이만 유효)" % TAG)
p("")
p("%-10s %4s %15s %15s %14s %13s %7s %6s" %
  ("arm", "n", "cycles", "instructions", "branches", "br-misses", "실패율", "IPC"))
for a in ("baseline", "patched"):
    r = arms[a]
    p("%-10s %4d %15.0f %15.0f %14.0f %13.0f %6.2f%% %6.2f" %
      (a, len(r), med(r,"cycles"), med(r,"instructions"), med(r,"branches"),
       med(r,"branch-misses"), med(r,"branch-misses")/med(r,"branches")*100,
       med(r,"instructions")/med(r,"cycles")))

cb, cp_ = med(arms["baseline"],"cycles"), med(arms["patched"],"cycles")
mb, mp_ = med(arms["baseline"],"branch-misses"), med(arms["patched"],"branch-misses")
ib, ip_ = med(arms["baseline"],"instructions"), med(arms["patched"],"instructions")

p("")
p("─ Q_P1 ★판정용★  분기 예측 실패가 패치 전후 차이를 설명하는가 (20% 미만이면 아니다)")
dcyc, dmiss = cb - cp_, mb - mp_
if dcyc <= 0:
    p("   baseline 이 더 싸다(%+.0f cycles) — 이 축에서는 판정할 수 없다." % -dcyc)
else:
    share = dmiss * CYC_PER_MISS / dcyc * 100
    p("   사이클 격차 %.0f  |  분기실패 격차 %+.0f  |  x18 = %+.0f  |  설명력 %.1f%%"
      % (dcyc, dmiss, dmiss*CYC_PER_MISS, share))
    p("   명령어 격차 %+.0f (%.1f%%)  ← 이쪽이 사이클 격차(%.1f%%)와 맞으면 F-028 과 같은 답"
      % (ib-ip_, (ib/ip_-1)*100, (cb/cp_-1)*100))
    p("   판정: %s" % ("맞음 — 실제 경로에서도 분기는 설명하지 못한다 (%.1f%%)" % share
                      if share < 20 else
                      "★빗나감 — 분기가 %.1f%% 를 설명한다. F-028 을 마이크로벤치 한정으로 좁혀야 한다" % share))

p("")
p("─ Q_P4  실제 스캔의 분기 실패율이 마이크로벤치(0.01~0.08%)보다 훨씬 높은가 (>1%)")
rate = med(arms["baseline"],"branch-misses")/med(arms["baseline"],"branches")*100
p("   실측 %.2f%%  (마이크로벤치 SPARSE_0_5 0.016%% 의 %.0f배)" % (rate, rate/0.016))
p("   판정: %s" % ("맞음 — 마이크로벤치 숫자를 실제 경로에 그대로 옮기면 안 된다"
                  if rate > 1.0 else "빗나감 — %.2f%%" % rate))

# ── Q_P2: 이벤트별 귀속 비교 ─────────────────────────────────────────────
def share_of_scan(arm, ev):
    """collapsed 프로파일에서 DV/스캔 비중의 라운드별 중앙값."""
    # 스크립트가 `tr -c 'a-zA-Z0-9' '_'` 를 쓰는데 개행까지 바뀌어 뒤에 _ 가 하나 더 붙는다.
    # 파일명을 바꾸면 이미 찍은 프로파일이 고아가 되므로 여기서 와일드카드로 흡수한다.
    safe = re.sub(r"[^A-Za-z0-9]", "_", ev) + "*"
    vals = []
    for path in sorted(glob.glob(os.path.join(
            R, "profiles", "%s__qp%s_%s_r*.collapsed" % (arm, TAG, safe)))):
        stacks = parse_collapsed(path)
        if not stacks: continue
        a = analyze(stacks)
        if a.get("scan_samples"):
            vals.append(a["dv_union_samples"] / a["scan_samples"] * 100)
    return (st.median(vals), len(vals), (min(vals), max(vals))) if vals else (None, 0, (None, None))

p("")
p("─ Q_P2  cycles 로 귀속한 DV 비중이 ctimer 와 5%p 이내로 같은가 (F-018 보정 0.53 기전)")
res = {}
for ev in ("ctimer", "cycles", "branch-misses"):
    m, n, rr = share_of_scan("baseline", ev)
    res[ev] = m
    if m is None:
        p("   %-14s (프로파일 없음)" % ev)
    else:
        p("   %-14s DV/스캔 %6.2f%%   n=%d  범위 %.2f ~ %.2f" % (ev, m, n, rr[0], rr[1]))
if res.get("ctimer") is not None and res.get("cycles") is not None:
    d = res["cycles"] - res["ctimer"]
    p("   차이 %+.2f%%p" % d)
    p("   판정: %s" % ("맞음 — 샘플링 방식의 문제가 아니다. 보정 0.53 의 범인은 스캔 밖에 있다."
                      if abs(d) <= 5 else
                      "★빗나감 — %+.2f%%p 다. ctimer 가 DV 를 %s 평가했고, 그게 보정의 일부를 설명한다."
                      % (d, "과대" if d < 0 else "과소")))
else:
    p("   판정: 프로파일이 모자라 판정 불가")

if res.get("branch-misses") is not None:
    p("")
    p("─ 덤: 분기 예측 실패가 **어디서** 나는가 (branch-misses 이벤트로 귀속)")
    p("   DV 체크 구간이 전체 분기 실패의 %.2f%% 를 차지한다." % res["branch-misses"])
    if res.get("ctimer") is not None:
        p("   CPU 시간 비중(%.2f%%)과 비교하면 DV 구간은 분기를 %s 틀린다."
          % (res["ctimer"], "더 많이" if res["branch-misses"] > res["ctimer"] else "덜"))
p("=" * 96)
