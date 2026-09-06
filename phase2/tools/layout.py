#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3-A 레이아웃 처방 — 10-layout.sh 헤더의 L1~L4 를 채점한다.

예측은 저기 있고 여기 없다.

L3 이 통제 확인용이다. 두 테이블의 non-DV 스캔 샘플(Parquet 디코딩)이 같아야 하고,
다르면 L1 을 해석하면 안 된다.
L4 가 글에 제일 중요하다 — 패치와 정렬 처방이 겹치는가 독립인가.
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

LAYOUTS = [("uns", "무정렬"), ("srt", "정렬")]
COLS = int(os.environ.get("LAY_COLS", "1"))
NOISE_FLOOR = 9.7


def profiles(results, arm, tag, c):
    out = {}
    for path in sorted(glob.glob(os.path.join(
            results, "profiles", "%s__%s_c%d_r*.collapsed" % (arm, tag, c)))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        r = int(re.search(r"_r(\d+)\.collapsed$", path).group(1))
        out[r] = {"dv": a["dv_union_samples"], "scan": a["scan_samples"],
                  "pct": a["dv_pct_of_scan"]}
    return out


def walls(results, arm, tag):
    out = {}
    for path in glob.glob(os.path.join(results, "scan_%s__%s_r*.json" % (arm, tag))):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if d.get("median_s"):
            out[int(re.search(r"_r(\d+)\.json$", path).group(1))] = float(d["median_s"])
    return out


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
    cells = {}
    for lay, _ in LAYOUTS:
        tag = "lay%sc%d" % (lay, COLS)
        for arm in ("baseline", "patched"):
            pr = profiles(results, arm, tag, COLS)
            if not pr:
                continue
            w = walls(results, arm, tag)
            dv = [v["dv"] for v in pr.values()]
            sc = [v["scan"] for v in pr.values()]
            nondv = [v["scan"] - v["dv"] for v in pr.values()]
            cells[(lay, arm)] = dict(
                n=len(dv), dv=st.mean(dv), scan=st.mean(sc), nondv=st.mean(nondv),
                share=st.mean(v["pct"] for v in pr.values()),
                dv_rng=(min(dv), max(dv)), nondv_rng=(min(nondv), max(nondv)),
                wall=w,
            )
    if not cells:
        print("결과가 없습니다: %s" % results)
        return

    h("레이아웃 처방 — 같은 행을 삭제하고 물리 배치만 바꿨다 (술어 없는 스캔, 파일 스킵 불가)")
    print("  %-6s %-9s %3s %10s %11s %11s %9s %10s"
          % ("레이아웃", "arm", "n", "DV 샘플", "스캔 샘플", "non-DV", "DV 비중", "wall 중앙"))
    print("  " + "-" * 92)
    for lay, ll in LAYOUTS:
        for arm in ("baseline", "patched"):
            r = cells.get((lay, arm))
            if not r:
                continue
            wm = st.median(r["wall"].values()) if r["wall"] else float("nan")
            print("  %-6s %-9s %3d %10.0f %11.0f %11.0f %8.2f%% %9.3fs"
                  % (ll, arm, r["n"], r["dv"], r["scan"], r["nondv"], r["share"], wm))
        print("  " + "·" * 92)

    # ---------- L3 (통제 확인 먼저) ----------
    h("L3 채점 [통제 확인] — 두 테이블의 Parquet 디코딩 비용이 같은가")
    print("  읽는 행 수와 컬럼이 같으므로 non-DV 스캔 샘플이 같아야 한다.")
    print("  다르면 통제가 실패한 것이고 L1 을 해석하면 안 된다.\n")
    ok3 = True
    for arm in ("baseline", "patched"):
        u, s = cells.get(("uns", arm)), cells.get(("srt", arm))
        if not (u and s):
            continue
        d = (s["nondv"] / u["nondv"] - 1) * 100 if u["nondv"] else float("nan")
        overlap = not (s["nondv_rng"][1] < u["nondv_rng"][0]
                       or u["nondv_rng"][1] < s["nondv_rng"][0])
        if not overlap:
            ok3 = False
        print("  %-9s 무정렬 %8.0f  정렬 %8.0f  차이 %+6.1f%%   %s"
              % (arm, u["nondv"], s["nondv"], d,
                 "범위 겹침 ✅" if overlap else "범위 분리 ⚠️"))
    print("\n  -> %s" % ("통제 성립. L1 을 해석해도 된다." if ok3
                        else "⚠️ 디코딩 비용이 다르다. L1 해석에 주의가 필요하다."))

    # ---------- L1 ----------
    h("L1 채점 [판정용] — baseline 에서 정렬이 DV 체크 CPU 를 2배 이상 낮추는가")
    u, s = cells.get(("uns", "baseline")), cells.get(("srt", "baseline"))
    if u and s:
        ratio = u["dv"] / s["dv"] if s["dv"] else float("inf")
        sep = s["dv_rng"][1] < u["dv_rng"][0]
        print("  무정렬 DV %.0f (범위 %d~%d)" % (u["dv"], u["dv_rng"][0], u["dv_rng"][1]))
        print("  정렬   DV %.0f (범위 %d~%d)" % (s["dv"], s["dv_rng"][0], s["dv_rng"][1]))
        print("\n  -> %.2f배 %s   %s" % (ratio, "감소" if ratio > 1 else "증가",
                                        "범위 분리 ✅" if sep else "범위 겹침 — 주장 불가"))
        print("     DV 비중도 %.2f%% -> %.2f%% 로 떨어진다." % (u["share"], s["share"]))
        print("  -> L1 %s" % ("적중 (2배 이상)" if ratio >= 2.0 and sep
                              else "빗나감 — 2배에 못 미친다" if sep else "판정 불가"))

    # ---------- L4 ----------
    h("L4 채점 [글에 제일 중요] — 패치를 적용하면 정렬의 이득이 줄어드는가")
    print("  줄면 두 처방이 겹치는 것(하나만 해도 된다), 안 줄면 독립(둘 다 하면 둘 다 먹는다).\n")
    print("  %-9s %11s %11s %10s" % ("arm", "무정렬 DV", "정렬 DV", "정렬 이득"))
    print("  " + "-" * 92)
    gains = {}
    for arm in ("baseline", "patched"):
        u, s = cells.get(("uns", arm)), cells.get(("srt", arm))
        if not (u and s):
            continue
        g = u["dv"] / s["dv"] if s["dv"] else float("inf")
        gains[arm] = g
        print("  %-9s %11.0f %11.0f %9.2f배" % (arm, u["dv"], s["dv"], g))
    if len(gains) == 2:
        b, p = gains["baseline"], gains["patched"]
        print("\n  baseline 에서 %.2f배 -> patched 에서 %.2f배" % (b, p))
        if p < b * 0.7:
            print("  -> L4 적중. 패치가 이미 DV 를 싸게 만들어 정렬의 여지가 줄었다.")
            print("     두 처방은 **겹친다** — 패치가 오면 정렬의 DV 이득은 작아진다.")
        elif p > b * 1.3:
            print("  -> L4 가 반대로 깨졌다. 패치 위에서 정렬이 **더** 효과적이다. 기전 설명이 필요하다.")
        else:
            print("  -> L4 빗나감. 정렬 이득이 두 arm 에서 비슷하다 — 두 처방이 **독립**이다.")
            print("     좋은 소식이다: 둘 다 하면 둘 다 먹는다.")

    # ---------- 패치 대 정렬, 그리고 둘 다 ----------
    h("실무자에게 줄 표 — 무엇을 하면 DV 체크 CPU 가 얼마나 줄어드나")
    base = cells.get(("uns", "baseline"))
    if base:
        print("  기준: 무정렬 + 패치 없음 = DV 샘플 %.0f (비중 %.2f%%)\n" % (base["dv"], base["share"]))
        for lay, ll in LAYOUTS:
            for arm in ("baseline", "patched"):
                r = cells.get((lay, arm))
                if not r:
                    continue
                what = ("아무것도 안 함" if (lay, arm) == ("uns", "baseline") else
                        "정렬만" if (lay, arm) == ("srt", "baseline") else
                        "패치만" if (lay, arm) == ("uns", "patched") else "정렬 + 패치")
                print("  %-14s DV %7.0f  비중 %6.2f%%  기준 대비 %6.2f배 감소"
                      % (what, r["dv"], r["share"], base["dv"] / r["dv"] if r["dv"] else float("inf")))

    # ---------- wall-clock ----------
    h("wall-clock — 라운드 안에서 쌍으로 (패치 효과), 그리고 레이아웃 간 비교")
    for lay, ll in LAYOUTS:
        wb = cells.get((lay, "baseline"), {}).get("wall", {})
        wq = cells.get((lay, "patched"), {}).get("wall", {})
        reps = sorted(set(wb) & set(wq))
        if not reps:
            continue
        d = [(wq[r] / wb[r] - 1) * 100 for r in reps]
        slower = sum(1 for x in d if x > 0)
        p = sign_p(slower, len(d))
        med = st.median(d)
        claim = abs(med) > NOISE_FLOOR and p <= 0.05
        print("  %-6s 패치 효과: %-38s 중앙 %+6.1f%%  %d/%d  p=%.2f  %s"
              % (ll, " ".join("%+.1f" % x for x in d)[:38], med, slower, len(d), p,
                 "주장 가능" if claim else "주장 불가"))
    ub = cells.get(("uns", "baseline"), {}).get("wall", {})
    sb = cells.get(("srt", "baseline"), {}).get("wall", {})
    reps = sorted(set(ub) & set(sb))
    if reps:
        d = [(sb[r] / ub[r] - 1) * 100 for r in reps]
        med = st.median(d)
        p = sign_p(sum(1 for x in d if x > 0), len(d))
        print("\n  정렬 효과(baseline, 라운드 쌍): %-30s 중앙 %+6.1f%%  p=%.2f  %s"
              % (" ".join("%+.1f" % x for x in d)[:30], med, p,
                 "주장 가능" if abs(med) > NOISE_FLOOR and p <= 0.05 else "주장 불가"))

    out = os.path.join(results, "layout.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"cells": {"%s_%s" % k: {kk: vv for kk, vv in v.items() if kk != "wall"}
                             for k, v in cells.items()}}, fh, indent=2, ensure_ascii=False)
    print("\n  -> %s" % out)


if __name__ == "__main__":
    main()
