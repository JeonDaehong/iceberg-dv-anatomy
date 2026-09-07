#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
문자열 길이 축 — 12-strlen.sh 헤더의 T1~T3 을 채점한다. 예측은 저기 있고 여기 없다.

핵심은 `fixed` 를 몰라도 판정할 수 있게 짜는 것이다. 길이가 다른 네 문자열 컬럼의
**차분**을 쓰면 컬럼당 고정비와 DV 가 소거된다:
    scan(s16) − scan(s8) = cost(16) − cost(8)
길이에 비례하면 (16−8):(24−8):(32−8) = 1:2:3 이 나온다.

그리고 cost = a + b·len 으로 적합해 a(컬럼당 고정비)와 b(바이트당 비용)를 나눈다.
  a 가 크고 b 가 작으면 -> '문자열이라서' (객체 처리 고정비)
  a 가 작고 b 가 크면 -> '바이트가 많아서'
"""
import glob
import json
import math
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

TAG = os.environ.get("STR_TAG", "d610")
# 이름, 컬럼, 길이(문자), 표시
SETS = [("int", "id",  0,  "정수 (id)"),
        ("s8",  "s10", 8,  "md5 8자"),
        ("s16", "s09", 16, "md5 16자"),
        ("s24", "s17", 24, "md5 24자"),
        ("s32", "s07", 32, "md5 32자")]
NOISE_FLOOR = 9.7
BAND = 0.25   # T1: 1:2:3 의 ±25%


def cell(results, arm, name):
    tag = "%sL%s" % (TAG, name)
    pr = {}
    for path in sorted(glob.glob(os.path.join(
            results, "profiles", "%s__%s_c1_r*.collapsed" % (arm, tag)))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        pr[int(re.search(r"_r(\d+)\.collapsed$", path).group(1))] = a
    if not pr:
        return None
    w = {}
    for path in glob.glob(os.path.join(results, "scan_%s__%s_r*.json" % (arm, tag))):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if d.get("median_s"):
            w[int(re.search(r"_r(\d+)\.json$", path).group(1))] = float(d["median_s"])
    return dict(
        n=len(pr),
        dv=st.mean(a["dv_union_samples"] for a in pr.values()),
        scan=st.mean(a["scan_samples"] for a in pr.values()),
        share=st.mean(a["dv_pct_of_scan"] for a in pr.values()),
        dv_rng=(min(a["dv_union_samples"] for a in pr.values()),
                max(a["dv_union_samples"] for a in pr.values())),
        wall=w,
    )


def sign_p(k, n):
    if n == 0:
        return 1.0
    k = max(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / (2.0 ** n))


def h(t):
    print("\n" + "=" * 96)
    print(" " + t)
    print("=" * 96)


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    B = {n: cell(results, "baseline", n) for n, _, _, _ in SETS}
    P = {n: cell(results, "patched", n) for n, _, _, _ in SETS}
    B = {k: v for k, v in B.items() if v}
    if not B:
        print("결과가 없습니다: %s" % results)
        return

    h("문자열 길이 축 — 타입은 전부 string, 길이만 다르다 (한 컬럼씩 투영)")
    print("  %-12s %4s %3s %10s %11s %10s %10s"
          % ("컬럼", "길이", "n", "DV 샘플", "스캔 샘플", "DV 비중", "wall"))
    print("  " + "-" * 92)
    for name, col, ln, lab in SETS:
        r = B.get(name)
        if not r:
            continue
        wm = st.median(r["wall"].values()) if r["wall"] else float("nan")
        print("  %-12s %4s %3d %10.0f %11.0f %9.2f%% %9.3fs"
              % (lab, ("%d자" % ln) if ln else "—", r["n"], r["dv"], r["scan"], r["share"], wm))

    # ---------- T1 ----------
    h("T1 채점 [판정용] — 컬럼 비용이 길이에 비례하는가")
    s8 = B.get("s8")
    if not s8:
        print("  s8 이 없어 채점 불가")
        return
    print("  차분으로 본다 — 컬럼당 고정비와 DV 가 소거된다.")
    print("  길이에 비례하면 (16−8):(24−8):(32−8) = 1 : 2 : 3 이어야 한다.\n")
    print("  %-14s %12s %10s %10s  %s" % ("차분", "스캔 샘플 차", "비", "예상 비", "판정"))
    print("  " + "-" * 92)
    d16 = B["s16"]["scan"] - s8["scan"] if "s16" in B else None
    hits = []
    for name, exp in (("s16", 1.0), ("s24", 2.0), ("s32", 3.0)):
        if name not in B or not d16:
            continue
        d = B[name]["scan"] - s8["scan"]
        ratio = d / d16
        ok = abs(ratio - exp) <= exp * BAND
        if name != "s16":
            hits.append(ok)
        print("  %-14s %12.0f %9.2f %9.1f  %s"
              % ("%s − s8" % name, d, ratio, exp,
                 "기준" if name == "s16" else ("안에 듦 ✅" if ok else "벗어남 ❌")))

    # 선형 적합: cost = a + b·len  (문자열 넷만)
    xs = [ln for n, c, ln, l in SETS if n in B and ln > 0]
    ys = [B[n]["scan"] - B[n]["dv"] for n, c, ln, l in SETS if n in B and ln > 0]
    if len(xs) >= 3:
        mx, my = st.mean(xs), st.mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else float("nan")
        a = my - b * mx
        ss = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
        tot = sum((y - my) ** 2 for y in ys)
        print("\n  적합 (non-DV 스캔 샘플 = a + b·길이, 문자열 4점):")
        print("    a(컬럼 고정비 + fixed) = %.0f     b(문자당) = %.1f     R² = %.2f"
              % (a, b, 1 - ss / tot if tot else float("nan")))
        span = b * 32
        print("    32자에서 길이 항 = %.0f, 고정 항 = %.0f  ->  길이 항이 전체의 %.0f%%"
              % (span, a, 100 * span / (span + a) if (span + a) else 0))
        if hits and all(hits):
            print("\n  -> T1 적중. 축은 **바이트**다.")
            print("     F-017 의 'md5 = 정수 8~10개' 는 32바이트라서지 문자열이라서가 아니다.")
            print("     이슈 초안의 caveat 을 지울 수 있다.")
        elif b <= 0 or span < a * 0.5:
            print("\n  -> T1 빗나감 — 길이 항이 작다. 축은 **문자열 처리 자체**다.")
            print("     길이가 아니라 문자열 컬럼 '개수' 가 중요하다는 뜻이고 처방 문장이 달라진다.")
        else:
            print("\n  -> T1 부분 적중 — 길이에 따라 늘긴 하는데 비례는 아니다.")
            print("     고정비와 길이 항이 둘 다 유의하다. 위 a, b 로 설명해야 한다.")

    # ---------- 정수 대비 ----------
    if "int" in B:
        h("참고 — F-017 의 '정수 컬럼 몇 개어치' 를 길이별로")
        base = B["int"]["scan"] - B["int"]["dv"]
        print("  정수 컬럼(id)의 non-DV 스캔 샘플 = %.0f 을 1 로 본다.\n" % base)
        for name, col, ln, lab in SETS:
            if name == "int" or name not in B:
                continue
            v = B[name]["scan"] - B[name]["dv"]
            print("  %-12s (%2d자)  %8.0f   = 정수의 %5.2f배" % (lab, ln, v, v / base if base else 0))

    # ---------- T2 ----------
    h("T2 채점 — DV 비중이 길이에 따라 단조 감소하는가")
    seq = [(lab, B[n]["share"]) for n, c, ln, lab in SETS if n in B]
    for lab, v in seq:
        print("  %-12s %7.2f%%" % (lab, v))
    strs = [v for n, c, ln, lab in SETS if n in B and ln > 0 for v in [B[n]["share"]]]
    if len(strs) >= 2:
        mono = all(strs[i] > strs[i + 1] for i in range(len(strs) - 1))
        print("\n  -> 문자열 4점 %s" % ("단조 감소 ✅" if mono else "단조 아님 ❌"))

    # ---------- T3 ----------
    h("T3 채점 — 패치 개선 배율이 길이와 무관한가")
    print("  %-12s %10s %10s %9s  %s" % ("컬럼", "DV base", "DV patch", "배율", "DV 범위"))
    print("  " + "-" * 92)
    sp = []
    for name, col, ln, lab in SETS:
        if name not in B or name not in P or not P[name]:
            continue
        s_ = B[name]["dv"] / P[name]["dv"] if P[name]["dv"] else float("inf")
        sp.append(s_)
        ov = not (P[name]["dv_rng"][1] < B[name]["dv_rng"][0]
                  or B[name]["dv_rng"][1] < P[name]["dv_rng"][0])
        print("  %-12s %10.0f %10.0f %8.2f배  %s"
              % (lab, B[name]["dv"], P[name]["dv"], s_,
                 "겹침 — 주장 불가" if ov else "분리 ✅"))
    if sp:
        print("\n  배율 폭 %.2f~%.2f배  ->  %s"
              % (min(sp), max(sp),
                 "T3 적중 — 길이와 무관 ✅" if max(sp) / min(sp) <= 1.5
                 else "T3 빗나감 — 길이에 따라 움직인다 ❌"))

    print("\n  wall-clock (라운드 쌍):")
    for name, col, ln, lab in SETS:
        if name not in B or name not in P or not P[name]:
            continue
        wb, wq = B[name]["wall"], P[name]["wall"]
        reps = sorted(set(wb) & set(wq))
        if not reps:
            continue
        d = [(wq[r] / wb[r] - 1) * 100 for r in reps]
        p = sign_p(sum(1 for x in d if x > 0), len(d))
        med = st.median(d)
        print("    %-12s %-34s 중앙 %+6.1f%%  p=%.2f  %s"
              % (lab, " ".join("%+.1f" % x for x in d)[:34], med, p,
                 "주장 가능" if abs(med) > NOISE_FLOOR and p <= 0.05 else "주장 불가"))

    out = os.path.join(results, "strlen.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"baseline": {k: {kk: vv for kk, vv in v.items() if kk != "wall"}
                                for k, v in B.items()}}, fh, indent=2, ensure_ascii=False)
    print("\n  -> %s" % out)


if __name__ == "__main__":
    main()
