#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
콜드 캐시 — "이 스캔이 정말 CPU 바운드인가", 그리고 F-018 의 0.46 이 조건을 넘는가.

07-coldcache.sh 헤더의 R1~R4 를 채점한다. 예측은 저기 있고 여기 없다.

R4 가 이 도구의 핵심이다. F-018 의 "wall 단축 = 0.46 × DV 비중" 은 웜 캐시 24점에
얹은 사후 회귀였다. 콜드는 분모에 I/O 를 더해 비중을 바꾸므로, 회귀를 적합시킨 적
없는 조건이다. 여기서 깨지면 0.46 은 법칙이 아니라 그 조건의 값일 뿐이다.
"""
import glob
import json
import math
import os
import re
import statistics as st
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

TAG = os.environ.get("COLD_TAG", "d610")
COLS = [1, 20]
MODES = [("warm", "웜"), ("cold", "콜드")]
NOISE_FLOOR = 9.7   # F-010
K_WALL = 0.46       # F-018: wall 단축 = 0.53(회귀) x 0.87(패치가 없애는 DV 몫)
R4_BAND = 5.0       # F-018 관계식의 허용 오차 (%p) — 07-coldcache.sh R4


def pad(s, w):
    n = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)
    return s + " " * max(0, w - n)


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


def sign_test_p(k, n):
    if n == 0:
        return 1.0
    k = max(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / (2.0 ** n))


def h(t):
    print("\n" + "=" * 100)
    print(" " + t)
    print("=" * 100)


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    cells = {}
    for c in COLS:
        for mode, mlabel in MODES:
            tag = "%s%sc%d" % (TAG, mode, c)
            b = profiles(results, "baseline", tag, c)
            q = profiles(results, "patched", tag, c)
            if not b or not q:
                continue
            wb, wq = walls(results, "baseline", tag), walls(results, "patched", tag)
            reps = sorted(set(wb) & set(wq))
            deltas = [(wq[r] / wb[r] - 1) * 100 for r in reps]
            cells[(c, mode)] = dict(
                c=c, mode=mode, mlabel=mlabel, tag=tag,
                dv_b=[v["dv"] for v in b.values()], dv_q=[v["dv"] for v in q.values()],
                scan_b=[v["scan"] for v in b.values()],
                scan_q=[v["scan"] for v in q.values()],
                share=st.mean(v["pct"] for v in b.values()),
                wall_b=[wb[r] for r in reps], wall_q=[wq[r] for r in reps],
                deltas=deltas, n=len(b),
            )
    if not cells:
        print("측정 결과가 없습니다: %s" % results)
        return

    # ---------- 1. 전체 표 ----------
    h("콜드 vs 웜 — baseline 기준 (%s)" % TAG)
    print("  %-4s %-6s %3s %10s %10s %9s %10s %10s"
          % ("컬럼", "모드", "n", "DV 샘플", "스캔 샘플", "DV 비중", "wall base", "wall patch"))
    print("  " + "-" * 96)
    for c in COLS:
        for mode, ml in MODES:
            r = cells.get((c, mode))
            if not r:
                continue
            print("  %-4d %-6s %3d %10.0f %10.0f %8.2f%% %9.3fs %9.3fs"
                  % (c, pad(ml, 6), r["n"], st.mean(r["dv_b"]), st.mean(r["scan_b"]),
                     r["share"], st.median(r["wall_b"]), st.median(r["wall_q"])))

    # ---------- 2. R1 ----------
    h("R1 채점 — 콜드에서 DV 비중이 웜보다 뚜렷하게 낮아지는가")
    print("  %-4s %10s %10s %10s  %s" % ("컬럼", "웜 비중", "콜드 비중", "비", "판정"))
    print("  " + "-" * 96)
    for c in COLS:
        w, cd = cells.get((c, "warm")), cells.get((c, "cold"))
        if not w or not cd:
            continue
        ratio = w["share"] / cd["share"] if cd["share"] else float("nan")
        print("  %-4d %9.2f%% %9.2f%% %9.2f배  %s"
              % (c, w["share"], cd["share"], ratio,
                 "낮아짐 ✅" if cd["share"] < w["share"] else "낮아지지 않음 ❌"))
    print("\n  I/O 가 분모에 얼마나 들어왔는지 (baseline wall-clock):")
    for c in COLS:
        w, cd = cells.get((c, "warm")), cells.get((c, "cold"))
        if not w or not cd:
            continue
        ww, cw = st.median(w["wall_b"]), st.median(cd["wall_b"])
        print("    %2d컬럼  웜 %.3fs -> 콜드 %.3fs  (%+.0f%%)" % (c, ww, cw, (cw / ww - 1) * 100))

    # ---------- 3. R2 ----------
    h("R2 채점 — DV 체크의 '절대' 비용이 웜과 콜드에서 같은가 (귀속 검증)")
    print("  같은 행 수에 같은 일을 하므로 같아야 한다. 다르면 귀속이 틀린 것이다.")
    print("  %-4s %-8s %12s %12s %10s  %s"
          % ("컬럼", "arm", "웜 DV", "콜드 DV", "차이", "반복 범위"))
    print("  " + "-" * 96)
    for c in COLS:
        for arm, key in (("baseline", "dv_b"), ("patched", "dv_q")):
            w, cd = cells.get((c, "warm")), cells.get((c, "cold"))
            if not w or not cd:
                continue
            mw, mc = st.mean(w[key]), st.mean(cd[key])
            overlap = not (max(cd[key]) < min(w[key]) or max(w[key]) < min(cd[key]))
            print("  %-4d %-8s %12.0f %12.0f %+9.1f%%  %s"
                  % (c, arm, mw, mc, (mc / mw - 1) * 100 if mw else 0,
                     "겹침 = 같다고 볼 수 있음 ✅" if overlap else "분리됨 — 설명 필요 ⚠️"))

    # ---------- 4. R3 ----------
    h("R3 채점 — 콜드에서도 패치의 개선 방향이 유지되는가")
    print("  %-4s %-6s %10s %10s %9s %10s  %s"
          % ("컬럼", "모드", "DV base", "DV patch", "배율", "스캔감소", "DV 범위"))
    print("  " + "-" * 96)
    for c in COLS:
        for mode, ml in MODES:
            r = cells.get((c, mode))
            if not r:
                continue
            sp = st.mean(r["dv_b"]) / st.mean(r["dv_q"]) if st.mean(r["dv_q"]) else float("inf")
            cut = (st.mean(r["scan_b"]) - st.mean(r["scan_q"])) / st.mean(r["scan_b"]) * 100
            ov = not (max(r["dv_q"]) < min(r["dv_b"]) or max(r["dv_b"]) < min(r["dv_q"]))
            print("  %-4d %-6s %10.0f %10.0f %7.2f배 %9.1f%%  %s"
                  % (c, pad(ml, 6), st.mean(r["dv_b"]), st.mean(r["dv_q"]), sp, cut,
                     "겹침 — 주장 불가" if ov else "분리 ✅"))

    # ---------- 5. R4 — F-018 의 조건 밖 시험 ----------
    h("R4 채점 — F-018 의 'wall 단축 = %.2f × DV 비중' 이 콜드에서도 성립하는가" % K_WALL)
    print("  이 관계식은 웜 캐시 24점에 얹은 사후 회귀다. 콜드는 적합시킨 적 없는 조건이다.")
    print("  ±%.0f%%p 를 벗어나면 0.46 은 법칙이 아니라 웜 조건의 값이다.\n" % R4_BAND)
    print("  %-4s %-6s %10s %12s %12s %9s %6s  %s"
          % ("컬럼", "모드", "DV 비중", "예상 wall", "실측 wall", "차이", "부호", "판정"))
    print("  " + "-" * 96)
    hits = []
    for c in COLS:
        for mode, ml in MODES:
            r = cells.get((c, mode))
            if not r or not r["deltas"]:
                continue
            pred = -K_WALL * r["share"]
            med = st.median(r["deltas"])
            slower = sum(1 for x in r["deltas"] if x > 0)
            ok = abs(med - pred) <= R4_BAND
            hits.append(ok)
            print("  %-4d %-6s %9.2f%% %11.1f%% %11.1f%% %+8.1fp %3d/%-2d  %s"
                  % (c, pad(ml, 6), r["share"], pred, med, med - pred,
                     slower, len(r["deltas"]), "안에 듦 ✅" if ok else "벗어남 ❌"))
    if hits:
        print("\n  -> %d/%d 셀이 ±%.0f%%p 안. %s"
              % (sum(hits), len(hits), R4_BAND,
                 "관계식이 콜드에서도 산다." if all(hits)
                 else "일부가 벗어났다 — 아래 라운드별 값을 보고 판단할 것."))
    print("\n  라운드별 wall-clock (patched/baseline-1, 음수 = patched 가 빠름):")
    for c in COLS:
        for mode, ml in MODES:
            r = cells.get((c, mode))
            if not r or not r["deltas"]:
                continue
            p = sign_test_p(sum(1 for x in r["deltas"] if x > 0), len(r["deltas"]))
            claim = abs(st.median(r["deltas"])) > NOISE_FLOOR and p <= 0.05
            print("    %2d컬럼 %-5s %-40s 중앙 %+6.1f%%  p=%.2f  %s"
                  % (c, ml, " ".join("%+.1f" % x for x in r["deltas"])[:40],
                     st.median(r["deltas"]), p,
                     "주장 가능" if claim else "주장 불가"))

    out = os.path.join(results, "coldcache.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"k_wall": K_WALL, "noise_floor": NOISE_FLOOR,
                   "cells": {"%dc_%s" % (k[0], k[1]): v for k, v in cells.items()}},
                  fh, indent=2, ensure_ascii=False)
    print("\n  -> %s" % out)


if __name__ == "__main__":
    main()
