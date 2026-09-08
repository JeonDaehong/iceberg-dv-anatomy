#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DvReboundBench 의 -prof perfnorm 출력을 읽어 R1~R5 를 기계적으로 판정한다.

예측은 bench/src/.../DvReboundBench.java 헤더에 있고 여기에 복제하지 않는다.
(예측을 채점 도구가 다시 적으면 사후에 조용히 고칠 수 있게 된다 — score_perfnorm.py 와 같은 규율.)

사용: python3 bench/score_rebound.py [results/rebound-perfnorm.txt]
"""
import io
import re
import sys

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

path = sys.argv[1] if len(sys.argv) > 1 else "results/rebound-perfnorm.txt"
txt = io.open(path, encoding="utf-8", errors="replace").read()

# "DvReboundBench.v1_full[:counter]  5000  DENSE_8  avgt  24  28.880 ± 0.923  us/op"
row = re.compile(
    r"^DvReboundBench\.(?P<bench>\w+)(?::(?P<ctr>[\w\-]+))?\s+\d+\s+"
    r"(?P<pat>[A-Z_0-9]+)\s+avgt\s+\d+\s+"
    r"(?P<score>[\d.]+)\s*(?:±\s*(?P<err>[\d.]+))?", re.M)

D = {}
for m in row.finditer(txt):
    ctr = m.group("ctr") or "us_op"
    D.setdefault(m.group("bench"), {}).setdefault(m.group("pat"), {})[ctr] = (
        float(m.group("score")), float(m.group("err") or 0.0))

LO, HI = "DENSE_8", "DENSE_50"
ORDER = ["v1_full", "v2_noStore", "v3_fixedAddrStore", "v4_branchless",
         "v5_lookupOnly", "v6_withCounter"]
WHAT = {
    "v1_full":           "분기 O · 저장 O(live 주소)   ← 현행",
    "v2_noStore":        "분기 O · 저장 X",
    "v3_fixedAddrStore": "분기 O · 저장 O(rowId 주소)",
    "v4_branchless":     "분기 X · 저장 O(live 주소)",
    "v5_lookupOnly":     "분기 X · 저장 X   ← 조회만",
    "v6_withCounter":    "현행 + DeleteCounter",
}
benches = [b for b in ORDER if b in D and LO in D[b] and HI in D[b]]
if not benches:
    p("파싱된 벤치가 없다: %s" % path)
    sys.exit(1)


def val(b, pat, ctr="us_op"):
    return D[b][pat].get(ctr, (float("nan"), 0.0))


def sep(b, pat, c, pat2=None):
    """두 값의 오차 구간이 안 겹치는가."""
    (s1, e1), (s2, e2) = val(b, pat, c), val(c and b or b, pat2 or pat, c)
    return not (s1 + e1 >= s2 - e2 and s2 + e2 >= s1 - e1)


p("=" * 100)
p("반등의 나머지 55% — 루프 몸통을 변형으로 가른다 (둘 다 bitmap 컨테이너)")
p("=" * 100)
p("")
p("%-20s %-30s %16s %16s %14s %8s"
  % ("변형", "무엇을 남겼나", "d=8% (us/op)", "d=50% (us/op)", "증가폭", "비율"))

deltas = {}
for b in benches:
    (s1, e1), (s2, e2) = val(b, LO), val(b, HI)
    d = s2 - s1
    deltas[b] = d
    overlap = (s1 + e1 >= s2 - e2) and (s2 + e2 >= s1 - e1)
    p("%-20s %-30s %8.3f ±%-6.3f %8.3f ±%-6.3f %+13.3f %+7.1f%%%s"
      % (b, WHAT.get(b, ""), s1, e1, s2, e2, d, d / s1 * 100,
         "  (겹침)" if overlap else ""))

p("")
p("─ 하드웨어 카운터 (fork 3개뿐이라 오차가 크다 — 방향만 읽는다)")
p("%-20s %14s %14s %12s %12s %10s %10s"
  % ("변형", "분기실패 d8", "분기실패 d50", "IPC d8", "IPC d50", "명령어 d8", "명령어 d50"))
for b in benches:
    p("%-20s %14.1f %14.1f %12.2f %12.2f %10.0f %10.0f"
      % (b, val(b, LO, "branch-misses")[0], val(b, HI, "branch-misses")[0],
         val(b, LO, "IPC")[0], val(b, HI, "IPC")[0],
         val(b, LO, "instructions")[0], val(b, HI, "instructions")[0]))

base = deltas.get("v1_full")

p("")
p("─ R1 [설계 검증]  반등이 마이크로벤치에서 재현되는가")
if base is None:
    p("   v1_full 이 없다")
else:
    (s1, e1), (s2, e2) = val("v1_full", LO), val("v1_full", HI)
    ok = s2 - e2 > s1 + e1
    p("   v1_full  %.3f → %.3f us/op  (%+.1f%%),  오차 구간 분리: %s"
      % (s1, s2, base / s1 * 100, "예" if ok else "아니오"))
    p("   판정: %s" % ("**맞음** — 반등이 재현된다. 루프 몸통을 보는 게 맞았다."
                      if ok else
                      "★빗나감 — 재현이 안 된다. 반등은 Spark 경로 고유다. 아래는 무의미하다."))
    if not ok:
        p("=" * 100)
        sys.exit(0)

p("")
p("─ R2  조회만 하면 d=50% 가 느려지지 않는가")
if "v5_lookupOnly" in deltas:
    d = deltas["v5_lookupOnly"]
    r = d / val("v5_lookupOnly", LO)[0] * 100
    p("   v5_lookupOnly  %+.3f us/op (%+.1f%%)" % (d, r))
    p("   판정: %s" % ("**맞음** — 오히려 빠르다. F-034 의 '조회 기계장치는 싸진다' 가 재현됐다."
                      if r <= 10 else "★빗나감 — 조회 자체가 %+.1f%% 느려진다." % r))

p("")
p("─ R3 ★판정용★  분기를 지우면 반등이 절반 미만으로 줄어드는가")
if "v4_branchless" in deltas and base:
    d = deltas["v4_branchless"]
    share = d / base
    p("   v1_full 증가폭 %+.3f  →  v4_branchless 증가폭 %+.3f  (%.0f%% 남음)"
      % (base, d, share * 100))
    p("   분기 실패 d8→d50:  v1 %.0f → %.0f  |  v4 %.0f → %.0f"
      % (val("v1_full", LO, "branch-misses")[0], val("v1_full", HI, "branch-misses")[0],
         val("v4_branchless", LO, "branch-misses")[0], val("v4_branchless", HI, "branch-misses")[0]))
    p("   판정: %s" % ("**맞음** — 분기가 반등의 주범이다."
                      if share < 0.5 else
                      "★빗나감 — %.0f%% 가 남는다. 분기 말고 다른 게 있다." % (share * 100)))

p("")
p("─ R4 ★판정용★  저장을 지워도 반등이 80% 이상 남는가 (F-034 의 주소 의존 가설)")
if "v2_noStore" in deltas and base:
    d = deltas["v2_noStore"]
    share = d / base
    bm = val("v2_noStore", HI, "branch-misses")[0]
    p("   v2_noStore 증가폭 %+.3f  (%.0f%% 남음)" % (d, share * 100))
    p("   ⚠️ v2 의 d=50%% 분기 실패가 %.1f/op 다 — %s"
      % (bm, "JIT 이 조건문을 산술로 바꿔버렸다. 이 변형은 '분기 유지' 대조군이 못 됐다."
         if bm < 50 else "분기가 살아 있다."))
    p("   판정: %s" % ("맞음 — 저장은 주범이 아니다" if share >= 0.8 else
                      "★빗나감 — %.0f%% 만 남는다" % (share * 100)))

p("")
p("─ 그래서 저장 주소 의존은? (v3 이 진짜 대조군이다 — 분기는 두고 주소만 끊었다)")
if "v3_fixedAddrStore" in deltas and base:
    d = deltas["v3_fixedAddrStore"]
    (l1, le1), (l2, le2) = val("v3_fixedAddrStore", LO), val("v3_fixedAddrStore", HI)
    be = val("v1_full", LO)[1] ** 2 + val("v1_full", HI)[1] ** 2
    ve = le1 ** 2 + le2 ** 2
    import math
    be, ve = math.sqrt(be), math.sqrt(ve)
    overlap = (base - be <= d + ve) and (d - ve <= base + be)
    p("   v1_full %+.3f ±%.2f  vs  v3_fixedAddrStore %+.3f ±%.2f   (%.0f%% 남음)"
      % (base, be, d, ve, d / base * 100))
    p("   증가폭 오차 구간 겹침: %s" % ("예 — 차이를 주장하지 않는다" if overlap else "아니오"))
    p("   분기 실패 d50: v1 %.0f  vs  v3 %.0f (분기는 v3 에도 살아 있다)"
      % (val("v1_full", HI, "branch-misses")[0], val("v3_fixedAddrStore", HI, "branch-misses")[0]))

p("")
p("─ R5  DeleteCounter 를 넣으면 d=50% 에서 더 벌어지는가")
if "v6_withCounter" in deltas and base:
    g8 = val("v6_withCounter", LO)[0] - val("v1_full", LO)[0]
    g50 = val("v6_withCounter", HI)[0] - val("v1_full", HI)[0]
    p("   v6 - v1:  d=8%% %+.3f  |  d=50%% %+.3f" % (g8, g50))
    p("   ⚠️ v6 은 v1 의 `if` 를 `if/else` 로 바꾸면서 카운터를 넣었다 — **한 번에 두 가지를 바꿨다.**")
    p("      깨끗한 대조군이 아니므로 이 줄은 판정으로 쓰지 않는다.")
p("=" * 100)
