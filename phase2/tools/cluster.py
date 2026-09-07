#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
분산 클러스터 축 — cloud/cluster.sh 헤더의 C1~C3 을 채점한다.

단일 JVM 과 다른 점: 프로파일이 **익스큐터마다** 나온다. 한 실행(label)의
모든 익스큐터 파일을 합산해야 그 실행의 CPU 가 된다.
파일명: <arm>_<tag>_r<rep>-<pid>.collapsed, 노드별 디렉터리 아래.

사용: python3 tools/cluster.py <cluster-results 디렉터리>
"""
import glob
import json
import math
import os
import re
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

QUERIES = [("clscan", "스캔 (셔플 없음)"), ("claggL", "집계 (큰 셔플)")]
# 비교 기준: 단일 JVM 에서 같은 쿼리·같은 컬럼(k01,k04)으로 잰 값 (F-024)
LOCAL = {"clscan": dict(share_scan=53.95, share_all=13.99, speedup=892 / 131),
         "claggL": dict(share_scan=43.19, share_all=2.35, speedup=816 / 119)}
BAND_C1 = 10.0


def runs(root):
    """label -> 합산된 analyze 결과. 익스큐터 파일을 모두 더한다."""
    agg = defaultdict(list)
    for path in glob.glob(os.path.join(root, "prof", "*", "*.collapsed")):
        m = re.match(r"^(.*)-\d+\.collapsed$", os.path.basename(path))
        if not m:
            continue
        stacks = parse_collapsed(path)
        if stacks:
            agg[m.group(1)].append(stacks)
    out = {}
    for label, lst in agg.items():
        merged = []
        for s in lst:
            merged.extend(s)
        out[label] = (analyze(merged), len(lst))
    return out


def walls(root):
    out = {}
    for path in glob.glob(os.path.join(root, "scan_*.json")):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if d.get("median_s"):
            out[d["label"]] = float(d["median_s"])
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
    root = sys.argv[1] if len(sys.argv) > 1 else "cloud-results/cluster"
    R = runs(root)
    W = walls(root)
    if not R:
        print("프로파일이 없습니다: %s" % root)
        return

    # 클러스터 증거
    ev = os.path.join(root, "evidence", "cluster-state.json")
    if os.path.exists(ev):
        try:
            d = json.load(open(ev, encoding="utf-8"))
            h("클러스터 구성 (측정 직전에 남긴 증거)")
            print("  워커 %d개, 총 %d코어, 메모리 %s"
                  % (len(d.get("workers", [])), d.get("cores", 0), d.get("memory", "?")))
            for w in d.get("workers", []):
                print("   - %s  %s코어  %s" % (w.get("host"), w.get("cores"), w.get("state")))
        except Exception:
            pass

    cells = {}
    for q, ql in QUERIES:
        for arm in ("baseline", "patched"):
            reps = {}
            for label, (a, nexec) in R.items():
                m = re.match(r"^%s_%s_r(\d+)$" % (arm, q), label)
                if m and int(m.group(1)) >= 1:      # r0 은 게이트용
                    reps[int(m.group(1))] = (a, nexec)
            if not reps:
                continue
            cells[(q, arm)] = dict(
                n=len(reps),
                nexec=st.median([v[1] for v in reps.values()]),
                dv=st.mean(v[0]["dv_union_samples"] for v in reps.values()),
                scan=st.mean(v[0]["scan_samples"] for v in reps.values()),
                total=st.mean(v[0]["total_samples"] for v in reps.values()),
                share_scan=st.mean(v[0]["dv_pct_of_scan"] for v in reps.values()),
                share_all=st.mean(v[0]["dv_pct_of_all"] for v in reps.values()),
                rng=(min(v[0]["dv_union_samples"] for v in reps.values()),
                     max(v[0]["dv_union_samples"] for v in reps.values())),
                wall={r: W.get("%s_%s_r%d" % (arm, q, r)) for r in reps
                      if W.get("%s_%s_r%d" % (arm, q, r))},
            )

    h("분산 클러스터 — 익스큐터 프로파일을 실행 단위로 합산")
    print("  %-16s %-9s %3s %5s %10s %11s %11s %11s"
          % ("쿼리", "arm", "n", "exec", "DV 샘플", "스캔 샘플", "DV/스캔", "DV/전체"))
    print("  " + "-" * 92)
    for q, ql in QUERIES:
        for arm in ("baseline", "patched"):
            r = cells.get((q, arm))
            if not r:
                continue
            print("  %-16s %-9s %3d %5d %10.0f %11.0f %10.2f%% %10.2f%%"
                  % (ql, arm, r["n"], r["nexec"], r["dv"], r["scan"],
                     r["share_scan"], r["share_all"]))
        print("  " + "·" * 92)

    # ---------- C1 ----------
    h("C1 채점 [판정용] — DV 의 '스캔 내' 비중이 단일 JVM 과 ±%.0f%%p 안인가" % BAND_C1)
    print("  이게 이슈의 핵심 수치다. 깨지면 첫 문단에 '단일 JVM' 조건을 박아야 한다.\n")
    print("  %-16s %14s %14s %10s  %s" % ("쿼리", "단일 JVM", "클러스터", "차이", "판정"))
    print("  " + "-" * 92)
    oks = []
    for q, ql in QUERIES:
        r = cells.get((q, "baseline"))
        if not r:
            continue
        loc = LOCAL[q]["share_scan"]
        d = r["share_scan"] - loc
        ok = abs(d) <= BAND_C1
        oks.append(ok)
        print("  %-16s %13.2f%% %13.2f%% %+9.1fp  %s"
              % (ql, loc, r["share_scan"], d, "안에 듦 ✅" if ok else "벗어남 ❌"))
    if oks:
        print("\n  -> %d/%d 통과. %s" % (sum(oks), len(oks),
              "C1 적중 — 스캔 내 비중은 분산 실행에서도 유지된다." if all(oks)
              else "C1 빗나감 — 조건을 명시해야 한다."))

    # ---------- C2 ----------
    h("C2 채점 — DV 의 '전체 대비' 비중이 단일 JVM 보다 낮은가")
    print("  %-16s %14s %14s  %s" % ("쿼리", "단일 JVM", "클러스터", "판정"))
    print("  " + "-" * 92)
    for q, ql in QUERIES:
        r = cells.get((q, "baseline"))
        if not r:
            continue
        loc = LOCAL[q]["share_all"]
        print("  %-16s %13.2f%% %13.2f%%  %s"
              % (ql, loc, r["share_all"],
                 "낮아짐 ✅" if r["share_all"] < loc else "안 낮아짐 ❌"))

    # ---------- C3 ----------
    h("C3 채점 — 패치의 DV 개선 배율이 6~9배를 유지하는가")
    print("  %-16s %10s %10s %9s %12s  %s"
          % ("쿼리", "DV base", "DV patch", "배율", "단일 JVM", "DV 범위"))
    print("  " + "-" * 92)
    for q, ql in QUERIES:
        b, p = cells.get((q, "baseline")), cells.get((q, "patched"))
        if not (b and p):
            continue
        sp = b["dv"] / p["dv"] if p["dv"] else float("inf")
        ov = not (p["rng"][1] < b["rng"][0] or b["rng"][1] < p["rng"][0])
        print("  %-16s %10.0f %10.0f %8.2f배 %11.2f배  %s"
              % (ql, b["dv"], p["dv"], sp, LOCAL[q]["speedup"],
                 "겹침 — 주장 불가" if ov else "분리 ✅"))

    # ---------- wall ----------
    h("wall-clock (참고 — 이 축은 처음부터 샘플로만 본다고 적었다)")
    for q, ql in QUERIES:
        b, p = cells.get((q, "baseline")), cells.get((q, "patched"))
        if not (b and p):
            continue
        reps = sorted(set(b["wall"]) & set(p["wall"]))
        if not reps:
            continue
        d = [(p["wall"][r] / b["wall"][r] - 1) * 100 for r in reps]
        pv = sign_p(sum(1 for x in d if x > 0), len(d))
        print("  %-16s %-34s 중앙 %+6.1f%%  p=%.2f  base %.2fs"
              % (ql, " ".join("%+.1f" % x for x in d)[:34], st.median(d), pv,
                 st.median([b["wall"][r] for r in reps])))

    out = os.path.join(root, "cluster.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"cells": {"%s_%s" % k: {kk: vv for kk, vv in v.items() if kk != "wall"}
                             for k, v in cells.items()}}, fh, indent=2, ensure_ascii=False)
    print("\n  -> %s" % out)


if __name__ == "__main__":
    main()
