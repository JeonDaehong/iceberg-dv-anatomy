#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스토리지 축 — 08-storage.sh 헤더의 S1~S4 를 채점한다. 예측은 저기 있고 여기 없다.

S1 이 판정용이다. F-019 가 로컬 콜드로 "DV 비중은 스토리지에 안 흔들린다" 를 얻었지만,
그건 로컬 콜드 읽기가 CPU 를 거의 안 쓰기 때문(페이지 폴트 + DMA)이었다.
S3 는 HTTP 파싱·TLS 복호화·체크섬이 전부 사용자 스레드의 CPU 라 ctimer 분모에 들어간다.

사용: python3 tools/storage.py <results 디렉터리>
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

TAG = os.environ.get("STO_TAG", "d610")
STORES = [("ebs", "EBS"), ("s3", "S3")]
COLS = [1, 20]
K_WALL = 0.46      # F-018
S4_BAND = 5.0
S1_BAND = 10.0     # S1: c=1 에서 10%p 이상 하락을 예측했다


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
                  "pct": a["dv_pct_of_scan"], "total": a["total_samples"],
                  "scan_pct_all": a["scan_pct_of_all"]}
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
    for store, _ in STORES:
        for c in COLS:
            tag = "%s%sc%d" % (TAG, store, c)
            b = profiles(results, "baseline", tag, c)
            q = profiles(results, "patched", tag, c)
            if not b or not q:
                continue
            wb, wq = walls(results, "baseline", tag), walls(results, "patched", tag)
            reps = sorted(set(wb) & set(wq))
            deltas = [(wq[r] / wb[r] - 1) * 100 for r in reps]
            bd = [v["dv"] for v in b.values()]
            qd = [v["dv"] for v in q.values()]
            bs = [v["scan"] for v in b.values()]
            qs = [v["scan"] for v in q.values()]
            cells[(store, c)] = dict(
                n=len(bd), dv_b=st.mean(bd), dv_q=st.mean(qd),
                scan_b=st.mean(bs),
                share=st.mean(v["pct"] for v in b.values()),
                scan_of_all=st.mean(v["scan_pct_all"] for v in b.values()),
                total=st.mean(v["total"] for v in b.values()),
                speedup=st.mean(bd) / st.mean(qd) if st.mean(qd) else float("inf"),
                scan_cut=(st.mean(bs) - st.mean(qs)) / st.mean(bs) * 100,
                deltas=deltas, slower=sum(1 for x in deltas if x > 0),
                wall_b=st.median([wb[r] for r in reps]) if reps else float("nan"),
                wall_q=st.median([wq[r] for r in reps]) if reps else float("nan"),
                wall_med=st.median(deltas) if deltas else None,
                dv_overlap=not (max(qd) < min(bd) or max(bd) < min(qd)),
                dv_range=(min(bd), max(bd)),
            )
    if not cells:
        print("결과가 없습니다: %s" % results)
        return

    h("EBS vs S3 — 같은 인스턴스·같은 세션·같은 반복 (읽기 경로만 다르다)")
    print("  %3s %-5s %3s %10s %10s %10s %10s %10s"
          % ("c", "저장소", "n", "wall base", "DV 샘플", "스캔 샘플", "DV 비중", "스캔/전체"))
    print("  " + "-" * 92)
    for c in COLS:
        for store, sl in STORES:
            r = cells.get((store, c))
            if not r:
                continue
            print("  %3d %-5s %3d %9.3fs %10.0f %10.0f %9.2f%% %9.2f%%"
                  % (c, sl, r["n"], r["wall_b"], r["dv_b"], r["scan_b"],
                     r["share"], r["scan_of_all"]))
        print("  " + "·" * 92)

    # ---------- S1 ----------
    h("S1 채점 [판정용] — S3 에서 DV 비중이 뚜렷하게 낮아지는가 (c=1 에서 10%p 이상)")
    print("  낮아지면: S3 클라이언트 CPU 가 분모에 들어간 것 -> 공개 글에 스토리지 조건 명시 필요.")
    print("  안 낮아지면: 그 CPU 가 스캔 서브트리 밖으로 귀속된 것 -> F-019 결론이 더 강해진다.\n")
    print("  %3s %12s %12s %10s %12s  %s"
          % ("c", "EBS 비중", "S3 비중", "차이", "wall 증가", "판정"))
    print("  " + "-" * 92)
    for c in COLS:
        e, s3 = cells.get(("ebs", c)), cells.get(("s3", c))
        if not (e and s3):
            continue
        d = s3["share"] - e["share"]
        wall_up = (s3["wall_b"] / e["wall_b"] - 1) * 100 if e["wall_b"] else float("nan")
        verdict = "10%p 이상 하락 ✅" if d <= -S1_BAND else (
                  "하락하지만 10%p 미만" if d < 0 else "안 낮아짐 ❌")
        print("  %3d %11.2f%% %11.2f%% %+9.1fp %+11.1f%%  %s"
              % (c, e["share"], s3["share"], d, wall_up, verdict))
    print("\n  참고 — 스캔 서브트리가 전체 CPU 에서 차지하는 몫:")
    for c in COLS:
        e, s3 = cells.get(("ebs", c)), cells.get(("s3", c))
        if e and s3:
            print("    c=%-3d EBS %.1f%% -> S3 %.1f%%  (%+.1f%%p)"
                  % (c, e["scan_of_all"], s3["scan_of_all"],
                     s3["scan_of_all"] - e["scan_of_all"]))
    print("  이 값이 크게 떨어지면 S3 클라이언트 CPU 가 '스캔 밖' 으로 귀속된 것이다.")

    # ---------- S2 ----------
    h("S2 채점 — DV 체크의 절대 비용이 두 스토리지에서 같은가 (귀속 검증)")
    print("  %3s %-9s %11s %11s %9s  %s" % ("c", "arm", "EBS", "S3", "차이", "반복 범위"))
    print("  " + "-" * 92)
    for c in COLS:
        for arm, key in (("baseline", "dv_b"), ("patched", "dv_q")):
            e, s3 = cells.get(("ebs", c)), cells.get(("s3", c))
            if not (e and s3):
                continue
            print("  %3d %-9s %11.0f %11.0f %+8.1f%%  %s"
                  % (c, arm, e[key], s3[key], (s3[key] / e[key] - 1) * 100 if e[key] else 0,
                     "—"))

    # ---------- S3P ----------
    h("S3P 채점 — 패치의 개선 배율이 두 스토리지에서 유지되는가")
    print("  %3s %-5s %10s %10s %9s  %s" % ("c", "저장소", "DV base", "DV patch", "배율", "DV 범위"))
    print("  " + "-" * 92)
    for c in COLS:
        for store, sl in STORES:
            r = cells.get((store, c))
            if not r:
                continue
            print("  %3d %-5s %10.0f %10.0f %8.2f배  %s"
                  % (c, sl, r["dv_b"], r["dv_q"], r["speedup"],
                     "겹침 — 주장 불가" if r["dv_overlap"] else "분리 ✅"))

    # ---------- S4 ----------
    h("S4 채점 — 'wall 단축 = %.2f × DV 비중' 이 S3 에서도 성립하는가" % K_WALL)
    print("  %3s %-5s %10s %12s %12s %9s %6s  %s"
          % ("c", "저장소", "DV비중", "예상 wall", "실측 wall", "차이", "부호", "판정"))
    print("  " + "-" * 92)
    hits = []
    for c in COLS:
        for store, sl in STORES:
            r = cells.get((store, c))
            if not r or r["wall_med"] is None:
                continue
            pred = -K_WALL * r["share"]
            ok = abs(r["wall_med"] - pred) <= S4_BAND
            hits.append(ok)
            print("  %3d %-5s %9.2f%% %11.1f%% %11.1f%% %+8.1fp %3d/%-2d  %s"
                  % (c, sl, r["share"], pred, r["wall_med"], r["wall_med"] - pred,
                     r["slower"], len(r["deltas"]), "안에 듦 ✅" if ok else "벗어남 ❌"))
    if hits:
        print("\n  -> %d/%d 셀 통과." % (sum(hits), len(hits)))

    print("\n  라운드별 wall-clock:")
    for c in COLS:
        for store, sl in STORES:
            r = cells.get((store, c))
            if not r or not r["deltas"]:
                continue
            p = sign_p(r["slower"], len(r["deltas"]))
            print("    c=%-3d %-4s %-38s 중앙 %+6.1f%%  p=%.2f"
                  % (c, sl, " ".join("%+.1f" % x for x in r["deltas"])[:38],
                     st.median(r["deltas"]), p))

    out = os.path.join(results, "storage.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"cells": {"%s_c%d" % k: v for k, v in cells.items()}}, fh,
                  indent=2, ensure_ascii=False)
    print("\n  -> %s" % out)


if __name__ == "__main__":
    main()
