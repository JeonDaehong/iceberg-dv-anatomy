#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""20컬럼 wall-clock 의 arm 간 쌍 비교 — F-015 가 '가설로 남긴다' 고 적은 항목의 판정.

무엇을 묻는가:
  F-015 에서 d=6.1% w20 은 6 라운드 모두 patched 가 느렸다(+0.5%~+8.3%).
  순서를 뒤집은 라운드(r4~6)에서도 그랬으므로 순서 효과가 아니다.
  그러나 6쌍은 부호검정 p=0.031 이고, 반복 범위가 통째로 겹치므로
  F-010 의 규칙상 '주장' 이 아니라 '가설' 로만 남겼다.

무엇을 더 재야 판정이 되는가:
  쌍의 개수. 라운드 안에서 두 arm 을 붙여 재므로 라운드 간 표류는 쌍차에서 상쇄된다.
  그래서 노이즈 바닥 9.7%(라운드 간 흩어짐) 는 이 검정의 문턱이 아니다 —
  문턱은 '쌍차의 부호가 우연으로 이만큼 일관될 확률' 이다.

  ⚠️ 대신 이 도구는 **크기를 주장하지 않는다.** 부호(방향)만 판정한다.
     크기를 말하려면 범위가 안 겹쳐야 하고, 여기서는 겹친다.

사용: python3 tools/w20pairs.py [results] [tag]
"""
import glob
import io
import json
import math
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
TAGS = sys.argv[2:] or ["d610w20", "d50w20", "d700w20"]

# r1~r3 = baseline 먼저, r4~r6 = patched 먼저 (FLIP_MODE=block, F-015).
# r7 이후는 FLIP_MODE=alternate — 홀수 라운드 baseline 먼저, 짝수 라운드 patched 먼저.
def order(rep):
    if rep <= 3:
        return "base→patch"
    if rep <= 6:
        return "patch→base"
    return "base→patch" if rep % 2 == 1 else "patch→base"


def walls(tag, arm):
    out = {}
    for path in glob.glob(os.path.join(R, "scan_%s__%s_r*.json" % (arm, tag))):
        m = re.search(r"_r(\d+)\.json$", path)
        if not m:
            continue
        try:
            d = json.load(io.open(path, encoding="utf-8"))
        except Exception:
            continue
        if d.get("median_s"):
            out[int(m.group(1))] = float(d["median_s"])
    return out


def samples(tag, arm):
    """라운드 -> (DV, 스캔, 비-DV 스캔) 샘플. 크기가 어디서 나는지 보려면 분모를 갈라야 한다."""
    out = {}
    for path in glob.glob(os.path.join(R, "profiles", "%s__%s_c*_r*.collapsed" % (arm, tag))):
        m = re.search(r"_r(\d+)\.collapsed$", path)
        if not m:
            continue
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        out[int(m.group(1))] = (a["dv_union_samples"], a["scan_samples"],
                                a["scan_samples"] - a["dv_union_samples"])
    return out


def binom_two_sided(k, n):
    """부호검정 정확 p (양측). 귀무가설: 각 쌍의 부호가 5:5."""
    k = max(k, n - k)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / (2.0 ** n)
    return min(1.0, 2 * tail)


def boot_ci(vals, iters=20000, seed=20260908):
    """중앙값의 부트스트랩 95% 구간. 표본이 작으므로 정규 가정을 쓰지 않는다."""
    import random
    rnd = random.Random(seed)
    n = len(vals)
    meds = sorted(st.median(rnd.choices(vals, k=n)) for _ in range(iters))
    return meds[int(0.025 * iters)], meds[int(0.975 * iters)]


p("=" * 100)
p("20컬럼 wall-clock — 라운드 안 쌍 비교 (F-015 의 '6/6 저하' 판정)")
p("=" * 100)

for tag in TAGS:
    b, q = walls(tag, "baseline"), walls(tag, "patched")
    reps = sorted(set(b) & set(q))
    if not reps:
        p("")
        p("── %s : 쌍이 없다" % tag)
        continue

    p("")
    p("── %s   쌍 %d개" % (tag, len(reps)))
    p("%5s %12s %12s %10s %14s" % ("r", "baseline", "patched", "차이%", "순서"))
    diffs = []
    for r in reps:
        d = (q[r] / b[r] - 1) * 100
        diffs.append((r, d))
        p("%5d %12.4f %12.4f %+10.2f %14s" % (r, b[r], q[r], d, order(r)))

    def block(name, sel):
        vals = [d for r, d in diffs if sel(r)]
        if not vals:
            return
        k = sum(1 for v in vals if v > 0)
        n = len(vals)
        pv = binom_two_sided(k, n)
        lo, hi = boot_ci(vals) if n >= 4 else (float("nan"), float("nan"))
        p("   %-22s patched 느림 %2d/%-2d  부호검정 p=%.4f  중앙값 %+.2f%%  95%%CI [%+.2f, %+.2f]"
          % (name, k, n, pv, st.median(vals), lo, hi))
        return (k, n, pv, lo, hi)

    p("")
    old = block("r1~r6 (F-015, block)", lambda r: r <= 6)
    new = block("r7~ (신규, alternate)", lambda r: r >= 7)
    allb = block("전체", lambda r: True)

    # 크기가 어디서 나는가 — wall 이 느려졌다면 어느 샘플이 늘었는지 본다.
    sb, sq = samples(tag, "baseline"), samples(tag, "patched")
    common = sorted(set(sb) & set(sq))
    if common:
        p("")
        p("   ─ 어디서 나는가 (같은 라운드 안에서 샘플을 갈라 본다)")
        p("   %5s %10s %10s %10s %10s %12s %12s"
          % ("r", "DV(b)", "DV(p)", "scan(b)", "scan(p)", "비DV(b)", "비DV(p)"))
        ddv, dnon = [], []
        for r in common:
            (d1, s1, n1), (d2, s2, n2) = sb[r], sq[r]
            ddv.append(d2 / d1 - 1 if d1 else 0)
            dnon.append(n2 / n1 - 1 if n1 else 0)
            p("   %5d %10d %10d %10d %10d %12d %12d" % (r, d1, d2, s1, s2, n1, n2))
        kd = sum(1 for v in dnon if v > 0)
        p("   DV 샘플 변화 중앙값 %+.1f%%   비-DV 스캔 변화 중앙값 %+.1f%%  (patched 가 큰 라운드 %d/%d)"
          % (st.median(ddv) * 100, st.median(dnon) * 100, kd, len(dnon)))
        p("   해석: wall 이 느려진 만큼 **비-DV 스캔**이 늘었으면 패치 밖에서 값을 치르는 것이고,")
        p("         비-DV 가 안 움직였으면 wall 차이는 스캔 밖(JIT·GC·기동)에서 온 것이다.")

    if new and allb:
        k, n, pv, lo, hi = new
        p("")
        p("   판정 — 신규 라운드만으로:")
        if n < 8:
            p("     쌍이 %d개뿐이다. 부호검정은 %d/%d 여도 p=%.3f 이하로 못 내려간다. 더 필요."
              % (n, n, n, binom_two_sided(n, n)))
        elif pv < 0.05 and lo > 0:
            p("     **실재한다.** 부호 %d/%d (p=%.4f), 쌍차 중앙값의 95%%CI 가 0 위에 있다."
              % (k, n, pv))
            p("     크기는 주장하지 않는다 — 라운드별 흩어짐이 이 차이보다 크다.")
        elif pv < 0.05:
            p("     방향은 실재한다 (부호 %d/%d, p=%.4f). 다만 CI 가 0 을 포함하므로"
              % (k, n, pv))
            p("     크기는 여전히 주장 불가다.")
        else:
            p("     **노이즈로 확정한다.** 부호 %d/%d, p=%.4f. F-015 의 6/6 은 우연이었다."
              % (k, n, pv))
        if old:
            same_dir = (old[0] / old[1] - 0.5) * (k / n - 0.5) > 0
            p("     r1~r6 와 방향 일치: %s" % ("예" if same_dir else "아니오"))

p("=" * 100)
