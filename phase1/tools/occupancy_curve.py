#!/usr/bin/env python3
"""
점유율 p -> DV 체크 비용, 그리고 F-009 의 L 축과의 교차 비교.

F-009 에서 L 을 키우면 (a) 컨테이너가 run 이 되고 (b) 컨테이너 개수가 줄었다.
여기서는 (b) 만 바꾸고 (a) 는 array 로 고정한다.
컨테이너 개수가 같은 두 조건(예: p=10% array 12개 vs L=4096 run 12개)을
나란히 놓으면 '개수 효과'와 '구조 효과'가 분리된다.
"""
import csv
import glob
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402


def containers_of(results, tag):
    path = os.path.join(results, f"containers_{tag}.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def summarize(results, tag):
    prof = os.path.join(results, "profiles", f"{tag}_c1.collapsed")
    if not os.path.exists(prof):
        return None
    stacks = parse_collapsed(prof)
    if not stacks:
        return None
    a = analyze(stacks)
    rows = containers_of(results, tag) or []
    types = Counter(r["container"] for r in rows)
    cards = [int(r["cardinality"]) for r in rows]
    gen_path = os.path.join(results, f"gen_{tag}.json")
    gen = json.load(open(gen_path)) if os.path.exists(gen_path) else {}
    return {
        "tag": tag,
        "containers": len(rows),
        "type": ("run" if types.get("run") else
                 "bitmap" if types.get("bitmap") else "array") if rows else "-",
        "mean_cardinality": (sum(cards) / len(cards)) if cards else 0,
        "deleted_rows": gen.get("deleted_rows"),
        "dv_bytes": sum(int(r["bytes"]) for r in rows) if rows else 0,
        "dv_samples": a["dv_union_samples"],
        "dv_pct_scan": a["dv_pct_of_scan"],
    }


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    d_bp = int(sys.argv[2]) if len(sys.argv) > 2 else 50

    rows = []
    for path in sorted(glob.glob(os.path.join(results, f"gen_d{d_bp}p*.json"))):
        m = re.search(rf"gen_d{d_bp}p(\d+)\.json$", path)
        if not m:
            continue
        p = int(m.group(1))
        s = summarize(results, f"d{d_bp}p{p}")
        if s:
            s["occupancy_bp"] = p
            rows.append(s)
    rows.sort(key=lambda r: -r["occupancy_bp"])

    if not rows:
        print("결과가 없습니다. 06-occupancy.sh 를 먼저 실행하세요.")
        sys.exit(1)

    ref = rows[0]["dv_samples"]

    print("=" * 100)
    print(f" 점유율 p -> DV 체크 비용   [청크 내 밀도 {d_bp/100:.2f}%, L=1 고정 -> 컨테이너는 항상 array]")
    print("=" * 100)
    print(f"  {'p':>7} {'컨테이너':>8} {'타입':>7} {'평균card':>9} {'삭제행':>10} "
          f"{'DV 샘플':>9} {'DV/스캔':>9} {'p=100% 대비':>12}")
    print("  " + "-" * 96)
    for r in rows:
        rel = f"{r['dv_samples']/ref:>11.2f}x" if ref else f"{'-':>12}"
        print(f"  {r['occupancy_bp']/100:>6.1f}% {r['containers']:>8} {r['type']:>7} "
              f"{r['mean_cardinality']:>9,.0f} {str(r['deleted_rows'] or '-'):>10} "
              f"{r['dv_samples']:>9,} {r['dv_pct_scan']:>8.2f}% {rel}")

    # ---- L 축과 교차 비교 ----
    lc_path = os.path.join(results, f"clustering_cost_d{d_bp}.json")
    if os.path.exists(lc_path):
        lrows = json.load(open(lc_path))
        lcont = {}
        cc = os.path.join(results, f"clustering_curve_d{d_bp}.json")
        if os.path.exists(cc):
            for c in json.load(open(cc)):
                lcont[c["run_length"]] = c["array"] + c["bitmap"] + c["run"]

        print()
        print("=" * 100)
        print(" 교차 비교 — 컨테이너 개수가 비슷한 조건끼리")
        print("=" * 100)
        print(f"  {'조건':>22} {'컨테이너':>8} {'타입':>7} {'DV 샘플':>9}")
        print("  " + "-" * 96)
        merged = []
        for r in rows:
            merged.append((f"p={r['occupancy_bp']/100:.0f}% (L=1)", r["containers"],
                           r["type"], r["dv_samples"]))
        for lr in lrows:
            n = lcont.get(lr["run_length"])
            if n:
                merged.append((f"L={lr['run_length']} (p=100%)", n,
                               lr["container"], lr["dv_samples"]))
        merged.sort(key=lambda x: -x[1])
        for name, n, t, s in merged:
            print(f"  {name:>22} {n:>8} {str(t):>7} {s:>9,}")

        # 같은 컨테이너 개수에서 array vs run 비교
        print()
        print(" 판정")
        print("  " + "-" * 96)
        for name, n, t, s in merged:
            pass
        buckets = {}
        for name, n, t, s in merged:
            key = round(n / 10)  # 대략 같은 규모끼리 묶기
            buckets.setdefault(key, []).append((name, n, t, s))
        found = False
        for key, items in sorted(buckets.items(), reverse=True):
            types = {i[2] for i in items}
            if len(items) > 1 and len(types) > 1:
                found = True
                print(f"  컨테이너 ~{key*10}개 구간:")
                for name, n, t, s in items:
                    print(f"     {name:>22} 컨테이너 {n:>3}  {t:>6}  DV 샘플 {s:>6,}")
                arr = [i for i in items if i[2] == "array"]
                run = [i for i in items if i[2] == "run"]
                if arr and run:
                    a, b = arr[0][3], run[0][3]
                    print(f"     -> 같은 개수에서 array {a:,} vs run {b:,}  "
                          f"= 구조만으로 {a/max(b,1):.2f}배")
        if not found:
            print("  컨테이너 개수가 겹치는 조건 쌍이 없습니다. OCCUPANCIES 를 조정하세요.")

    with open(os.path.join(results, f"occupancy_curve_d{d_bp}.json"), "w") as fh:
        json.dump(rows, fh, indent=2)
    print(f"\n  -> occupancy_curve_d{d_bp}.json")


if __name__ == "__main__":
    main()
