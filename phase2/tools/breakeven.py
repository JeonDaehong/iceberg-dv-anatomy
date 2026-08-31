#!/usr/bin/env python3
"""
손익분기 컬럼 수 — 투영 폭 c 에 따라 패치 효과가 어디서 노이즈 밑으로 내려가는가.

06-widescan.sh 헤더의 Q5~Q7 을 채점한다. 예측은 저기 있고 여기 없다.
여기 다시 적으면 사후에 어느 쪽을 고쳤는지 알 수 없게 된다.

주장 규칙은 compare.py 와 같다 (F-010): 반복 범위가 겹치면 주장하지 않는다.
wall-clock 은 라운드 안에서 쌍으로 본다 — 라운드 간 표류에 방어하기 위해서다
(F-015 의 라운드 1 오염이 왜 필요한지 보여줬다).
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

DENSITIES = [("d50", "0.5%"), ("d610", "6.1%"), ("d700", "7.0%")]
WIDTHS = [1, 3, 5, 10, 20]
NOISE_FLOOR = 9.7   # F-010

# 06-widescan.sh Q5/Q6 의 예측값 (d=6.1% 기준, 모델 scan(c) ≈ 870 + 933c)
PRED_SHARE = {3: 24.0, 5: 16.0, 10: 8.5}
PRED_CUT = {3: 21.0, 5: 14.0, 10: 7.0}


def tag_for(dens, c):
    return dens if c == 1 else "%sw%d" % (dens, c)


def profiles(results, arm, tag, c):
    pat = os.path.join(results, "profiles", "%s__%s_c%d_r*.collapsed" % (arm, tag, c))
    out = {}
    for path in sorted(glob.glob(pat)):
        m = re.search(r"_r(\d+)\.collapsed$", path)
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        out[int(m.group(1))] = {
            "dv": a["dv_union_samples"],
            "scan": a["scan_samples"],
            "pct_scan": a["dv_pct_of_scan"],
        }
    return out


def walls(results, arm, tag):
    """rep -> median wall-clock 초"""
    out = {}
    for path in glob.glob(os.path.join(results, "scan_%s__%s_r*.json" % (arm, tag))):
        m = re.search(r"_r(\d+)\.json$", path)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        v = d.get("median_s") or d.get("median") or d.get("median_sec")
        if v:
            out[int(m.group(1))] = float(v)
    return out


def sign_test_p(k, n):
    """양측 부호검정. k = 한쪽 부호의 개수."""
    if n == 0:
        return 1.0
    k = max(k, n - k)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / (2.0 ** n)
    return min(1.0, 2 * tail)


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    rows = []

    print("=" * 96)
    print(" 손익분기 컬럼 수 — 투영 폭에 따른 패치 효과")
    print("=" * 96)

    for dens, label in DENSITIES:
        print("\n### d=%s" % label)
        print("  %3s %10s %10s %8s %9s %10s  %s"
              % ("c", "DV base", "DV patch", "개선", "DV비중", "스캔감소", "판정"))
        print("  " + "-" * 88)
        for c in WIDTHS:
            tag = tag_for(dens, c)
            b = profiles(results, "baseline", tag, c)
            q = profiles(results, "patched", tag, c)
            if not b or not q:
                continue
            bd = [v["dv"] for v in b.values()]
            qd = [v["dv"] for v in q.values()]
            bs = [v["scan"] for v in b.values()]
            qs = [v["scan"] for v in q.values()]
            bp = st.mean(v["pct_scan"] for v in b.values())
            overlap = not (max(qd) < min(bd) or max(bd) < min(qd))
            speedup = st.mean(bd) / st.mean(qd) if st.mean(qd) else float("inf")
            scan_cut = (st.mean(bs) - st.mean(qs)) / st.mean(bs) * 100

            # wall-clock: 라운드 안에서 쌍으로
            wb, wq = walls(results, "baseline", tag), walls(results, "patched", tag)
            reps = sorted(set(wb) & set(wq))
            deltas = [(wq[r] / wb[r] - 1) * 100 for r in reps]

            rows.append({"dens": dens, "label": label, "c": c,
                         "dv_base": st.mean(bd), "dv_patch": st.mean(qd),
                         "speedup": speedup, "overlap": overlap,
                         "share": bp, "scan_cut": scan_cut,
                         "n": len(bd), "deltas": deltas, "reps": reps})
            print("  %3d %10.0f %10.0f %7.2f배 %8.2f%% %9.1f%%  %s"
                  % (c, st.mean(bd), st.mean(qd), speedup, bp, scan_cut,
                     "범위 겹침 — 주장 불가" if overlap
                     else "유의 (최악 %.2f배)" % (min(bd) / max(qd))))

    # ---------- wall-clock: 라운드별 쌍 ----------
    print("\n" + "=" * 96)
    print(" wall-clock — 라운드 안에서 쌍으로 (양수 = patched 가 느림)")
    print("=" * 96)
    print("  %8s %3s  %-42s %8s %8s  %s"
          % ("d", "c", "라운드별 (patched/baseline-1)", "중앙값", "부호", "판정"))
    print("  " + "-" * 88)
    for r in rows:
        d = r["deltas"]
        if not d:
            continue
        slower = sum(1 for x in d if x > 0)
        med = st.median(d)
        cut = -med   # 단축률
        p = sign_test_p(slower, len(d))
        if abs(med) < NOISE_FLOOR:
            verdict = "노이즈 안 — 주장 불가"
        else:
            verdict = "단축 %.1f%%" % cut if med < 0 else "느려짐 %.1f%%" % med
        print("  %8s %3d  %-42s %+7.1f%% %4d/%-3d  %s (부호 p=%.2f)"
              % (r["label"], r["c"], " ".join("%+.1f" % x for x in d)[:42],
                 med, slower, len(d), verdict, p))

    # ---------- Q5 / Q6 채점 ----------
    print("\n" + "=" * 96)
    print(" Q5 · Q6 채점 (d=6.1%, 06-widescan.sh 헤더의 모델 대비)")
    print("=" * 96)
    print("  %3s %12s %12s %8s | %12s %12s %8s"
          % ("c", "DV비중 예측", "실측", "차이", "스캔감소 예측", "실측", "차이"))
    print("  " + "-" * 88)
    for r in rows:
        if r["dens"] != "d610" or r["c"] not in PRED_SHARE:
            continue
        ps, pc = PRED_SHARE[r["c"]], PRED_CUT[r["c"]]
        print("  %3d %11.1f%% %11.2f%% %+7.1fp | %11.1f%% %11.1f%% %+7.1fp"
              % (r["c"], ps, r["share"], r["share"] - ps,
                 pc, r["scan_cut"], r["scan_cut"] - pc))

    # ---------- 손익분기 ----------
    print("\n" + "=" * 96)
    print(" 손익분기 — wall-clock 중앙값이 노이즈 바닥 %.1f%% 를 넘는 가장 넓은 c" % NOISE_FLOOR)
    print("=" * 96)
    for dens, label in DENSITIES:
        hits = [r["c"] for r in rows if r["dens"] == dens and r["deltas"]
                and st.median(r["deltas"]) < -NOISE_FLOOR]
        print("  d=%-6s  %s" % (label,
              "c ≤ %d 에서 단축이 노이즈 위" % max(hits) if hits
              else "어느 폭에서도 노이즈를 넘지 못함"))

    with open(os.path.join(results, "breakeven.json"), "w", encoding="utf-8") as fh:
        json.dump({"rows": rows, "noise_floor": NOISE_FLOOR}, fh,
                  indent=2, ensure_ascii=False)
    print("\n  -> %s" % os.path.join(results, "breakeven.json"))


if __name__ == "__main__":
    main()
