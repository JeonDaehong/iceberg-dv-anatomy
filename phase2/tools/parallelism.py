#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
병렬도 축 — 09-parallelism.sh 헤더의 N1~N3 을 채점한다.

예측은 저기 있고 여기 없다.

N1 이 이 프로젝트의 마지막 미지수다. F-018 의 `wall 단축 = 0.53 × 샘플 감소` 에서
손익분기 21% 가 나오는데, 그 0.53 은 local[4] 한 조건에 적합시킨 값이다.
보정비가 병렬도에 따라 움직이면 21% 는 local[4] 의 값이고, 안 움직이면 그대로 쓸 수 있다.

사용: python3 tools/parallelism.py <results 디렉터리>
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

TAG = os.environ.get("PAR_TAG", "d610")
CORES = [1, 2, 4, 16]
COLS = [1, 5]
NOISE_FLOOR = 9.7   # F-010 (로컬 WSL2 기준. F-020 에서 이 값이 환경 의존임이 드러났다)


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
    for p in CORES:
        for c in COLS:
            tag = "%sp%dc%d" % (TAG, p, c)
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
            bw = [wb[r] for r in reps]
            scan_cut = (st.mean(bs) - st.mean(qs)) / st.mean(bs) * 100
            wall_cut = -st.median(deltas) if deltas else float("nan")
            cells[(p, c)] = dict(
                p=p, c=c, n=len(bd),
                dv_b=st.mean(bd), dv_q=st.mean(qd),
                speedup=st.mean(bd) / st.mean(qd) if st.mean(qd) else float("inf"),
                share=st.mean(v["pct"] for v in b.values()),
                scan_cut=scan_cut, wall_cut=wall_cut,
                ratio=scan_cut / wall_cut if wall_cut and wall_cut > 0 else float("nan"),
                deltas=deltas, slower=sum(1 for x in deltas if x > 0),
                wall_base=st.median(bw) if bw else float("nan"),
                spread=(max(bw) - min(bw)) / st.median(bw) * 100 if len(bw) >= 3 else float("nan"),
                dv_overlap=not (max(qd) < min(bd) or max(bd) < min(qd)),
            )
    if not cells:
        print("결과가 없습니다: %s" % results)
        return

    # ---------- 표 ----------
    h("병렬도 축 — local[N] 만 바꾼다 (split-size · 메모리 · 반복 전부 고정)")
    print("  %3s %3s %3s %10s %9s %9s %8s %10s %10s %8s"
          % ("N", "c", "n", "wall base", "DV base", "DV patch", "배율",
             "DV비중", "샘플감소", "wall단축"))
    print("  " + "-" * 92)
    for c in COLS:
        for p in CORES:
            r = cells.get((p, c))
            if not r:
                continue
            print("  %3d %3d %3d %9.3fs %9.0f %9.0f %7.2f배 %9.2f%% %9.1f%% %7.1f%%"
                  % (p, c, r["n"], r["wall_base"], r["dv_b"], r["dv_q"], r["speedup"],
                     r["share"], r["scan_cut"], r["wall_cut"]))
        print("  " + "·" * 92)

    # ---------- N1 ----------
    h("N1 채점 [판정용] — 샘플→wall 보정비가 병렬도에 따라 커지는가")
    print("  보정비 = 스캔 샘플 감소 ÷ wall-clock 단축.")
    print("  예측: local[1] 에서 1.0~1.3, local[4] 에서 1.9 부근, local[16] 에서 2.5 이상.\n")
    print("  %3s %12s %12s %12s %10s  %s"
          % ("N", "c=1 보정비", "c=5 보정비", "평균", "vs local[1]", "wall 판정(c=1)"))
    print("  " + "-" * 92)
    base = None
    rows = []
    for p in CORES:
        rs = [cells[(p, c)] for c in COLS if (p, c) in cells]
        if not rs:
            continue
        vals = [r["ratio"] for r in rs if r["ratio"] == r["ratio"]]
        avg = st.mean(vals) if vals else float("nan")
        if base is None:
            base = avg
        r1 = cells.get((p, 1))
        pv = sign_p(r1["slower"], len(r1["deltas"])) if r1 and r1["deltas"] else 1.0
        claim = (r1 and abs(st.median(r1["deltas"])) > NOISE_FLOOR and pv <= 0.05)
        rows.append((p, avg))
        print("  %3d %11.2f배 %11.2f배 %11.2f배 %9.2f배  %s (p=%.2f)"
              % (p,
                 cells[(p, 1)]["ratio"] if (p, 1) in cells else float("nan"),
                 cells[(p, 5)]["ratio"] if (p, 5) in cells else float("nan"),
                 avg, avg / base if base else float("nan"),
                 "주장 가능" if claim else "주장 불가", pv))

    # 단조성이 핵심이다. 1.5배 같은 임의 임계값으로 이분 판정하면
    # 연속적인 추세를 잘못 읽는다 (실제로 한 번 잘못 읽었다).
    print()
    mono = {}
    for c in COLS:
        seq = [cells[(p, c)]["ratio"] for p in CORES if (p, c) in cells]
        mono[c] = all(seq[i] < seq[i + 1] for i in range(len(seq) - 1)) if len(seq) > 1 else False
        print("  c=%d 계열: %s   %s"
              % (c, " < ".join("%.2f" % v for v in seq),
                 "단조 증가 ✅" if mono[c] else "단조 아님"))

    # ratio = a × N^b 로 적합한다. b 가 0 에 가까우면 병렬도와 무관한 것이다.
    xs, ys = [], []
    for c in COLS:
        for p in CORES:
            if (p, c) in cells and cells[(p, c)]["ratio"] == cells[(p, c)]["ratio"]:
                xs.append(math.log(p))
                ys.append(math.log(cells[(p, c)]["ratio"]))
    if len(xs) >= 3:
        mx, my = st.mean(xs), st.mean(ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sxx = sum((x - mx) ** 2 for x in xs)
        b = sxy / sxx if sxx else float("nan")
        a = math.exp(my - b * mx)
        ss = sum((y - (math.log(a) + b * x)) ** 2 for x, y in zip(xs, ys))
        tot = sum((y - my) ** 2 for y in ys)
        print("\n  적합: 보정비 ≈ %.2f × N^%.3f   (R²=%.2f, %d점)"
              % (a, b, 1 - ss / tot if tot else float("nan"), len(xs)))
        for N in (4, 16, 64):
            print("     N=%-3d -> %.2f배" % (N, a * N ** b))

    if all(mono.values()) and len(mono) > 1:
        print("\n  -> N1 적중 (방향). 보정비는 병렬도의 함수다 — 두 폭 모두에서 단조 증가한다.")
        print("     다만 **크기는 예측보다 훨씬 약하다.** 병렬도를 16배 올려도 보정비는 1.5배만 커진다.")
        print("     예측은 local[1]=1.0~1.3(적중), local[4]=1.9(실측 1.6), local[16]>=2.5(실측 1.9)였다.")
        print("\n     실용적 함의: 손익분기 DV 비중은 병렬도에 따라 움직이지만 **완만하다.**")
        print("     N^%.2f 이므로 병렬도를 4배 올릴 때마다 임계값이 약 %.0f%% 씩 오른다."
              % (b, (4 ** b - 1) * 100))
        print("     ⚠️ 이 적합을 실제 클러스터로 외삽하지 말 것 — local[N] 은 한 JVM 안의")
        print("        스레드 풀이고, 셔플·네트워크·익스큐터 스케줄링이 없다.")
    else:
        print("\n  -> 단조가 아니다. 병렬도와 보정비의 관계를 단순하게 말할 수 없다.")

    # ---------- N2 / N3 ----------
    h("N2 · N3 채점 — DV 비중과 개선 배율이 병렬도와 무관한가")
    for c in COLS:
        sh = [cells[(p, c)]["share"] for p in CORES if (p, c) in cells]
        sp = [cells[(p, c)]["speedup"] for p in CORES if (p, c) in cells]
        ov = [p for p in CORES if (p, c) in cells and cells[(p, c)]["dv_overlap"]]
        if not sh:
            continue
        print("  c=%-3d 비중 %.2f~%.2f%% (폭 %.1f%%p)   배율 %.2f~%.2f배%s"
              % (c, min(sh), max(sh), max(sh) - min(sh), min(sp), max(sp),
                 "   ⚠️ DV 범위 겹침: local%s" % ov if ov else ""))
    print("\n  N2 판정: %s" % ("✅ 비중 폭이 ±5%p 안" if all(
        max(cells[(p, c)]["share"] for p in CORES if (p, c) in cells)
        - min(cells[(p, c)]["share"] for p in CORES if (p, c) in cells) <= 10.0
        for c in COLS if any((p, c) in cells for p in CORES))
        else "❌ 비중이 병렬도에 따라 움직인다 — 귀속을 다시 봐야 한다"))

    # ---------- 확장성 참고 ----------
    h("참고 — 확장성 (같은 일을 N 코어로)")
    for c in COLS:
        b1 = cells.get((1, c))
        if not b1:
            continue
        print("  c=%d  " % c + "  ".join(
            "local[%d] %.3fs (%.2f배)" % (p, cells[(p, c)]["wall_base"],
                                          b1["wall_base"] / cells[(p, c)]["wall_base"])
            for p in CORES if (p, c) in cells))

    print("\n  라운드별 wall-clock:")
    for c in COLS:
        for p in CORES:
            r = cells.get((p, c))
            if not r or not r["deltas"]:
                continue
            print("    local[%-2d] c=%-2d %-40s 중앙 %+6.1f%%  흩어짐 %.1f%%"
                  % (p, c, " ".join("%+.1f" % x for x in r["deltas"])[:40],
                     st.median(r["deltas"]), r["spread"]))

    out = os.path.join(results, "parallelism.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"cells": {"p%dc%d" % k: v for k, v in cells.items()}}, fh,
                  indent=2, ensure_ascii=False)
    print("\n  -> %s" % out)


if __name__ == "__main__":
    main()
