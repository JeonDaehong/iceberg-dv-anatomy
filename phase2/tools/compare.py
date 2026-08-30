#!/usr/bin/env python3
"""
패치 전후 비교 — arm(baseline/patched) × 설정 × 반복.

주장 규칙은 Phase 1 과 같다 (F-010): 반복 범위가 겹치면 차이를 주장하지 않는다.
다만 여기서는 '같은 설정의 두 arm' 을 비교하므로, 각 arm 의 흩어짐을 각각 내고
둘 다 고려한 보수적 판정을 한다.
"""
import glob
import json
import os
import re
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402


def collect(results):
    """(arm, tag) -> [측정, ...]"""
    by = defaultdict(list)
    for path in glob.glob(os.path.join(results, "profiles", "*.collapsed")):
        name = os.path.basename(path)
        m = re.match(r"^(baseline|patched)__(.*?)_c\d+_r(\d+)\.collapsed$", name)
        if not m:
            continue
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        by[(m.group(1), m.group(2))].append({
            "rep": int(m.group(3)),
            "dv": a["dv_union_samples"],
            "scan": a["scan_samples"],
            "total": a["total_samples"],
            "pct_scan": a["dv_pct_of_scan"],
        })
    return by


def wall(results, arm, tag):
    """스캔 median wall-clock (초). 없으면 None."""
    vals = []
    for path in glob.glob(os.path.join(results, f"scan_{arm}__{tag}_r*.json")):
        try:
            d = json.load(open(path))
        except Exception:
            continue
        v = d.get("median_s") or d.get("median") or d.get("median_sec")
        if v:
            vals.append(float(v))
    return st.median(vals) if vals else None


def stats(reps, key):
    vals = [r[key] for r in reps]
    mean = sum(vals) / len(vals)
    return {
        "n": len(vals), "mean": mean, "min": min(vals), "max": max(vals),
        "spread": ((max(vals) - min(vals)) / mean * 100) if mean > 0 else 0.0,
    }


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    by = collect(results)
    if not by:
        print("프로파일이 없습니다.")
        sys.exit(1)

    tags = sorted({t for (_, t) in by}, key=lambda t: (len(t), t))
    out = []

    print("=" * 108)
    print(" §7.1 패치 전후 — DV 체크 CPU 샘플")
    print("=" * 108)
    print(f"  {'설정':>10}  {'arm':>9} {'DV 샘플':>9} {'범위':>13} {'흩어짐':>7} "
          f"{'DV/스캔':>8} {'스캔 샘플':>10}")
    print("  " + "-" * 104)

    for tag in tags:
        row = {"tag": tag}
        for arm in ("baseline", "patched"):
            reps = by.get((arm, tag))
            if not reps:
                continue
            s = stats(reps, "dv")
            p = stats(reps, "pct_scan")
            sc = stats(reps, "scan")
            row[arm] = {**s, "pct_scan": p["mean"], "scan_mean": sc["mean"],
                        "wall_s": wall(results, arm, tag)}
            print(f"  {tag:>10}  {arm:>9} {s['mean']:>9,.0f} "
                  f"{s['min']:>6,}–{s['max']:<6,} {s['spread']:>6.1f}% "
                  f"{p['mean']:>7.2f}% {sc['mean']:>10,.0f}")

        b, q = row.get("baseline"), row.get("patched")
        if b and q:
            # 보수적 판정: 최선의 baseline(min) 과 최악의 patched(max) 를 비교해도
            # 개선이 남는가? 범위가 겹치면 주장하지 않는다.
            overlap = not (q["max"] < b["min"] or b["max"] < q["min"])
            speedup = (b["mean"] / q["mean"]) if q["mean"] > 0 else float("inf")
            worst = (b["min"] / q["max"]) if q["max"] > 0 else float("inf")
            verdict = ("범위 겹침 — 주장 불가" if overlap
                       else f"유의 (최악의 경우에도 {worst:.2f}배)")
            row["speedup"] = speedup
            row["overlap"] = overlap
            print(f"  {'':>10}  {'→':>9} 개선 {speedup:>8.2f}배   [{verdict}]")

            # 스캔 서브트리 자체가 얼마나 줄었나 (전체 쿼리 관점)
            if b["scan_mean"] > 0:
                scan_cut = (b["scan_mean"] - q["scan_mean"]) / b["scan_mean"] * 100
                row["scan_cut_pct"] = scan_cut
                print(f"  {'':>10}  {'':>9} 스캔 서브트리 감소 {scan_cut:.1f}%   "
                      f"DV 비중 {b['pct_scan']:.1f}% → {q['pct_scan']:.1f}%")
            if b.get("wall_s") and q.get("wall_s"):
                row["wall_baseline_s"] = b["wall_s"]
                row["wall_patched_s"] = q["wall_s"]
                cut = (1 - q["wall_s"] / b["wall_s"]) * 100
                print(f"  {'':>10}  {'':>9} wall-clock {b['wall_s']:.3f}s → "
                      f"{q['wall_s']:.3f}s (단축 {cut:+.1f}%)")
        print()
        out.append(row)

    with open(os.path.join(results, "compare.json"), "w") as fh:
        json.dump({"configs": out}, fh, indent=2)
    print(f"  -> {os.path.join(results, 'compare.json')}")


if __name__ == "__main__":
    main()
