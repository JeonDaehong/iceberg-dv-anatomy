#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
셔플 축 — 11-shuffle.sh 헤더의 Q_S1~Q_S3 을 채점한다. 예측은 저기 있고 여기 없다.

Q_S1 이 판정용이다. DV 의 '스캔 내' 비중이 세 쿼리에서 같아야 한다.
깨지면 귀속이 셔플에 오염된다는 뜻이고 F-015~F-023 이 전부 흔들린다.

Q_S3 이 글에 필요한 것 — 곱셈 공식이 성립하면 "조인 쿼리에서는?" 에 숫자로 답한다.
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

TAG = os.environ.get("SHF_TAG", "d610")
QUERIES = [("scan", "q0 스캔"), ("aggS", "q1 집계-소"), ("aggL", "q2 집계-대")]
NCOL = 2
NOISE_FLOOR = 9.7
BAND = 5.0


def cells(results):
    out = {}
    for q, _ in QUERIES:
        tag = "%sshf%s" % (TAG, q)
        for arm in ("baseline", "patched"):
            pr = {}
            for path in sorted(glob.glob(os.path.join(
                    results, "profiles", "%s__%s_c%d_r*.collapsed" % (arm, tag, NCOL)))):
                stacks = parse_collapsed(path)
                if not stacks:
                    continue
                a = analyze(stacks)
                r = int(re.search(r"_r(\d+)\.collapsed$", path).group(1))
                pr[r] = a
            if not pr:
                continue
            w = {}
            for path in glob.glob(os.path.join(results, "scan_%s__%s_r*.json" % (arm, tag))):
                try:
                    d = json.load(open(path, encoding="utf-8"))
                except Exception:
                    continue
                if d.get("median_s"):
                    w[int(re.search(r"_r(\d+)\.json$", path).group(1))] = float(d["median_s"])
            out[(q, arm)] = dict(
                n=len(pr),
                share_scan=st.mean(a["dv_pct_of_scan"] for a in pr.values()),
                share_all=st.mean(a["dv_pct_of_all"] for a in pr.values()),
                scan_of_all=st.mean(a["scan_pct_of_all"] for a in pr.values()),
                dv=st.mean(a["dv_union_samples"] for a in pr.values()),
                scan=st.mean(a["scan_samples"] for a in pr.values()),
                share_rng=(min(a["dv_pct_of_scan"] for a in pr.values()),
                           max(a["dv_pct_of_scan"] for a in pr.values())),
                wall=w,
            )
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
    C = cells(results)
    if not C:
        print("결과가 없습니다: %s" % results)
        return

    h("셔플 축 — 같은 테이블·같은 두 컬럼을 읽고, 스캔 뒤에 붙는 일만 바꿨다")
    print("  %-12s %-9s %3s %10s %11s %12s %11s %10s"
          % ("쿼리", "arm", "n", "DV 샘플", "스캔 샘플", "DV/스캔", "DV/전체", "wall 중앙"))
    print("  " + "-" * 92)
    for q, ql in QUERIES:
        for arm in ("baseline", "patched"):
            r = C.get((q, arm))
            if not r:
                continue
            wm = st.median(r["wall"].values()) if r["wall"] else float("nan")
            print("  %-12s %-9s %3d %10.0f %11.0f %11.2f%% %10.2f%% %9.3fs"
                  % (ql, arm, r["n"], r["dv"], r["scan"], r["share_scan"], r["share_all"], wm))
        print("  " + "·" * 92)

    # ---------- Q_S1 ----------
    h("Q_S1 채점 [판정용] — DV 의 '스캔 내' 비중이 세 쿼리에서 같은가 (±%.0f%%p)" % BAND)
    print("  깨지면 귀속이 셔플에 오염된다는 뜻이고, 스캔 서브트리 정의부터 다시 봐야 한다.\n")
    vals = []
    for q, ql in QUERIES:
        r = C.get((q, "baseline"))
        if not r:
            continue
        vals.append(r["share_scan"])
        print("  %-12s %7.2f%%  (반복 범위 %.1f~%.1f%%)"
              % (ql, r["share_scan"], r["share_rng"][0], r["share_rng"][1]))
    if len(vals) >= 2:
        spread = max(vals) - min(vals)
        print("\n  폭 %.1f%%p  ->  %s"
              % (spread, "Q_S1 적중 — 스캔 내 비중은 셔플과 무관하다 ✅" if spread <= BAND
                 else "Q_S1 빗나감 — 비중이 셔플에 따라 움직인다 ❌"))

    # ---------- Q_S2 ----------
    h("Q_S2 채점 — DV 의 '전체 대비' 비중이 셔플이 커질수록 떨어지는가")
    seq = [(ql, C[(q, "baseline")]["share_all"]) for q, ql in QUERIES if (q, "baseline") in C]
    for ql, v in seq:
        print("  %-12s %7.2f%%" % (ql, v))
    if len(seq) >= 2:
        mono = all(seq[i][1] > seq[i + 1][1] for i in range(len(seq) - 1))
        print("\n  -> %s" % ("Q_S2 적중 — 단조 감소 ✅" if mono else "Q_S2 빗나감 — 단조가 아니다"))

    # ---------- Q_S3 ----------
    h("Q_S3 채점 [글에 필요] — 패치 단축이 스캔 몫에 비례하는가")
    print("  예측: 단축(q) ≈ 단축(q0) × wall(q0)/wall(q).  스캔 몫을 wall-clock 비로 잰다.\n")

    def paired(q):
        b = C.get((q, "baseline"), {}).get("wall", {})
        p = C.get((q, "patched"), {}).get("wall", {})
        reps = sorted(set(b) & set(p))
        if not reps:
            return None
        d = [(p[r] / b[r] - 1) * 100 for r in reps]
        return dict(cut=-st.median(d), deltas=d,
                    slower=sum(1 for x in d if x > 0),
                    wall_b=st.median([b[r] for r in reps]))

    base = paired("scan")
    if not base:
        print("  q0 데이터가 없다.")
        return
    print("  %-12s %10s %10s %12s %12s %9s  %s"
          % ("쿼리", "wall base", "스캔 몫", "예상 단축", "실측 단축", "차이", "판정"))
    print("  " + "-" * 92)
    hits = []
    for q, ql in QUERIES:
        pr = paired(q)
        if not pr:
            continue
        frac = base["wall_b"] / pr["wall_b"] if pr["wall_b"] else float("nan")
        pred = base["cut"] * frac
        diff = pr["cut"] - pred
        ok = abs(diff) <= BAND
        if q != "scan":
            hits.append(ok)
        print("  %-12s %9.3fs %9.2f %11.1f%% %11.1f%% %+8.1fp  %s"
              % (ql, pr["wall_b"], frac, pred, pr["cut"], diff,
                 "기준" if q == "scan" else ("안에 듦 ✅" if ok else "벗어남 ❌")))
    if hits:
        print("\n  -> %d/%d 통과. %s" % (sum(hits), len(hits),
              "곱셈 공식을 공개 글에 실을 수 있다." if all(hits)
              else "셔플이 스캔 자체와 상호작용한다 — '재보라' 고만 써야 한다."))

    print("\n  라운드별 wall-clock (patched/baseline-1):")
    for q, ql in QUERIES:
        pr = paired(q)
        if not pr:
            continue
        p = sign_p(pr["slower"], len(pr["deltas"]))
        claim = pr["cut"] > NOISE_FLOOR and p <= 0.05
        print("    %-12s %-38s 중앙 %+6.1f%%  p=%.2f  %s"
              % (ql, " ".join("%+.1f" % x for x in pr["deltas"])[:38],
                 -pr["cut"], p, "주장 가능" if claim else "주장 불가"))

    out = os.path.join(results, "shuffle.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"cells": {"%s_%s" % k: {kk: vv for kk, vv in v.items() if kk != "wall"}
                             for k, v in C.items()}}, fh, indent=2, ensure_ascii=False)
    print("\n  -> %s" % out)


if __name__ == "__main__":
    main()
