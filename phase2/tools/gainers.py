#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""두 조건 사이에서 **어느 메서드가 사이클을 벌었는가** — F-032 의 남은 71.5% 좁히기.

F-032 는 d=8% -> d=50% 에서 DV 구간 사이클이 +15.5% 늘고, 그중 분기 오예측이 28.5%
밖에 설명하지 못한다는 데서 멈췄다. 나머지 71.5% 의 정체는 다시 잴 필요가 없다 —
이미 찍어둔 cycles 프로파일에서 **말단 프레임별로 차이를 내면** 어디서 늘었는지 보인다.

방법:
  두 조건의 collapsed 프로파일을 각각 말단 프레임(leaf)별로 합산하고, 전체 대비 비중으로
  정규화한 뒤 뺀다. 정규화가 필요한 이유: 두 조건은 총 샘플 수가 다르므로 절대값을 빼면
  '전체가 커져서 늘어난 것' 과 '진짜로 이 프레임이 늘어난 것' 이 섞인다.

⚠️ 탐색적 분해다. 예측을 걸고 잰 것이 아니므로 결과는 **가설이지 판정이 아니다.**
   여기서 나온 후보는 별도 축으로 예측을 걸고 다시 쳐야 주장이 된다.
"""
import glob
import io
import os
import sys
from collections import Counter

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")


def leaves(pattern, dv_only=False):
    """말단 프레임별 샘플 합. dv_only 면 DV 프레임을 포함한 스택만 센다."""
    DV = ("ColumnarBatchUtil", "org.apache.iceberg.deletes.", "roaringbitmap.")
    c = Counter()
    total = 0
    n = 0
    for path in sorted(glob.glob(pattern)):
        n += 1
        for line in io.open(path, encoding="utf-8", errors="replace"):
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                stack, cnt = line.rsplit(" ", 1)
                cnt = int(cnt)
            except ValueError:
                continue
            if dv_only and not any(d in stack for d in DV):
                continue
            frames = [f for f in stack.split(";") if f]
            if not frames:
                continue
            c[frames[-1]] += cnt
            total += cnt
    return c, total, n


def main():
    lo_pat = sys.argv[1] if len(sys.argv) > 1 else \
        "results/profiles/baseline__rbd800_cycles_r*.collapsed"
    hi_pat = sys.argv[2] if len(sys.argv) > 2 else \
        "results/profiles/baseline__rbd5000_cycles_r*.collapsed"
    dv_only = os.environ.get("DV_ONLY", "1") == "1"

    lo, lot, lon = leaves(lo_pat, dv_only)
    hi, hit, hin = leaves(hi_pat, dv_only)
    if not lot or not hit:
        p("샘플이 없다 (lo=%d, hi=%d)" % (lot, hit)); return

    p("=" * 96)
    p("어느 메서드가 사이클을 벌었나  %s" % ("[DV 구간 스택만]" if dv_only else "[전체 스택]"))
    p("=" * 96)
    p("LO: %s  (%d개 파일, %s 샘플)" % (lo_pat.split('/')[-1], lon, f"{lot:,}"))
    p("HI: %s  (%d개 파일, %s 샘플)" % (hi_pat.split('/')[-1], hin, f"{hit:,}"))
    p("")
    p("비중(%) 으로 정규화한 뒤 차이를 낸다 — 총 샘플 수가 다르므로 절대값은 못 뺀다.")
    p("")

    keys = set(lo) | set(hi)
    rows = []
    for k in keys:
        a = lo.get(k, 0) / lot * 100
        b = hi.get(k, 0) / hit * 100
        rows.append((b - a, k, a, b, lo.get(k, 0), hi.get(k, 0)))
    rows.sort(reverse=True)

    net = hit - lot
    p("DV 구간 샘플 총합: %s -> %s  (%+.1f%%),  순증 %+d" % (f"{lot:,}", f"{hit:,}", (hit/lot-1)*100, net))
    p("")
    p("%-78s %8s %8s %8s %9s %9s" % ("말단 프레임", "LO %", "HI %", "차이%p", "절대차", "순증대비"))
    p("-" * 122)
    for d, k, a, b, _, _ in rows[:14]:
        if abs(d) < 0.05:
            continue
        da = hi.get(k, 0) - lo.get(k, 0)
        p("%-78s %7.2f%% %7.2f%% %+8.2f %+9d %8.0f%%"
          % (k.split("/")[-1][:78], a, b, d, da, da / net * 100 if net else float("nan")))
    p("   ... (가운데 생략)")
    for d, k, a, b, _, _ in rows[-8:]:
        if abs(d) < 0.05:
            continue
        da = hi.get(k, 0) - lo.get(k, 0)
        p("%-78s %7.2f%% %7.2f%% %+8.2f %+9d %8.0f%%"
          % (k.split("/")[-1][:78], a, b, d, da, da / net * 100 if net else float("nan")))
    p("-" * 122)
    gain = sum(d for d, _, _, _, _, _ in rows if d > 0)
    p("증가한 프레임들의 합 %+.2f%%p / 감소 %+.2f%%p"
      % (gain, sum(d for d, _, _, _, _, _ in rows if d < 0)))
    p("=" * 96)


main()
