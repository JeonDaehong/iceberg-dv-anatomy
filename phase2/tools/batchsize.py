#!/usr/bin/env python3
"""
배치 크기 축 — 세 가지 예측을 검정한다 (scripts/05-batchsize.sh 참조).

  P1. baseline 의 DV 비용은 배치 크기에 거의 무관하다 (행당 이진 탐색이므로).
  P2. patched 의 DV 비용은 배치가 커질수록 내려간다 (배치당 고정 비용의 분산).
  P3. 따라서 speedup 은 배치 크기에 대해 단조 비감소.

행 수는 배치 크기와 무관하게 같으므로 DV 샘플 수를 그대로 비교해도 된다.
판정 규칙은 F-010 과 같다: 반복 범위가 겹치면 차이를 주장하지 않는다.
"""
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from compare import collect, stats, wall  # noqa: E402


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    by = collect(results)

    # 태그는 "<설정>b<배치>" 형태. 배치 접미사가 없는 건 이 분석의 대상이 아니다.
    grid = defaultdict(dict)     # base_tag -> batch -> {arm: stats}
    for (arm, tag), reps in by.items():
        m = re.match(r"^(.*)b(\d+)$", tag)
        if not m:
            continue
        base, batch = m.group(1), int(m.group(2))
        s = stats(reps, "dv")
        p = stats(reps, "pct_scan")
        grid[base].setdefault(batch, {})[arm] = {
            **s, "pct_scan": p["mean"], "wall_s": wall(results, arm, tag)}

    if not grid:
        print("배치 크기 프로파일이 없습니다. 05-batchsize.sh 를 먼저 돌리세요.")
        sys.exit(1)

    out = []
    print("=" * 96)
    print(" 배치 크기 축 — DV 체크 CPU 샘플 (행 수는 모든 배치에서 동일)")
    print("=" * 96)

    for base in sorted(grid):
        print(f"\n  ## {base}")
        print(f"  {'배치':>8} {'baseline':>20} {'patched':>20} {'개선':>9}  판정")
        print("  " + "-" * 88)
        speeds = {}
        for batch in sorted(grid[base]):
            arms = grid[base][batch]
            b, q = arms.get("baseline"), arms.get("patched")
            row = {"config": base, "batch": batch}
            cells = []
            for a in (b, q):
                cells.append(f"{a['mean']:>7,.0f} ({a['min']:,}–{a['max']:,})" if a else " " * 20)
            verdict, sp = "", None
            if b and q:
                overlap = not (q["max"] < b["min"] or b["max"] < q["min"])
                sp = b["mean"] / q["mean"] if q["mean"] > 0 else float("inf")
                speeds[batch] = sp
                verdict = "범위 겹침" if overlap else f"유의 (최악 {b['min']/q['max']:.2f}배)"
                row.update(speedup=sp, overlap=overlap,
                           baseline=b, patched=q)
            print(f"  {batch:>8,} {cells[0]:>20} {cells[1]:>20} "
                  f"{(f'{sp:.2f}배' if sp else '—'):>9}  {verdict}")
            out.append(row)

        # 예측 검정.
        #
        # ⚠️ 점 추정만 보고 단조성을 따지면 안 된다 (F-010). 반복 범위가 겹치면
        #    두 배치 크기는 '구분 불가' 이고, 구분 불가한 값들로 만든 순서에는
        #    아무 의미가 없다. 그래서 겹침을 먼저 판정한다.
        if len(speeds) >= 2:
            ks = sorted(speeds)
            print()

            def separable(arm):
                """배치 크기끼리 구분되는 쌍이 하나라도 있나?"""
                pairs = []
                for i in range(len(ks)):
                    for j in range(i + 1, len(ks)):
                        a = grid[base][ks[i]].get(arm)
                        b = grid[base][ks[j]].get(arm)
                        if not (a and b):
                            continue
                        if a["max"] < b["min"] or b["max"] < a["min"]:
                            pairs.append((ks[i], ks[j]))
                return pairs

            for arm, pred in (("baseline", "P1 baseline 은 배치 크기에 무관"),
                              ("patched", "P2 patched 는 배치가 커질수록 싸진다")):
                vals = [grid[base][k][arm]["mean"] for k in ks if arm in grid[base][k]]
                sep = separable(arm)
                spread = (max(vals) - min(vals)) / (sum(vals) / len(vals)) * 100
                if not sep:
                    mark = "✅ 예측대로" if arm == "baseline" else "❌ 예측과 다름"
                    note = "배치 크기끼리 구분되지 않음 (모든 쌍의 범위가 겹침)"
                else:
                    mark = "❌ 예측과 다름" if arm == "baseline" else "△"
                    note = f"구분되는 쌍: {sep}"
                print(f"  {pred}")
                print(f"     {' → '.join(f'{v:,.0f}' for v in vals)}  "
                      f"(흩어짐 {spread:.1f}%)  [{mark}] {note}")

            # P3: speedup 의 순서. 보수적으로 — baseline 최소/patched 최대 로 만든
            # '최악의 개선' 구간이 서로 겹치면 speedup 차이도 주장할 수 없다.
            bands = {}
            for k in ks:
                b, q = grid[base][k].get("baseline"), grid[base][k].get("patched")
                if b and q:
                    bands[k] = (b["min"] / q["max"], b["max"] / q["min"])
            sep3 = [(ks[i], ks[j]) for i in range(len(ks)) for j in range(i + 1, len(ks))
                    if ks[i] in bands and ks[j] in bands
                    and (bands[ks[i]][1] < bands[ks[j]][0] or bands[ks[j]][1] < bands[ks[i]][0])]
            print("  P3 speedup 은 배치 크기에 대해 단조 비감소")
            print(f"     {' → '.join(f'{speeds[k]:.2f}배' for k in ks)}  "
                  f"[{'△ 순서 판정 가능' if sep3 else '판정 불가 — 배치 간 speedup 이 서로 구분되지 않음'}]")
            lo = min(b[0] for b in bands.values()) if bands else None
            hi = max(b[1] for b in bands.values()) if bands else None
            if lo:
                print(f"     ⇒ 배치 {min(ks):,}~{max(ks):,} 전 구간에서 개선이 "
                      f"최소 {lo:.2f}배 이상으로 유지된다")

    path = os.path.join(results, "batchsize.json")
    with open(path, "w") as fh:
        json.dump({"configs": out}, fh, indent=2)
    print(f"\n  -> {path}")


if __name__ == "__main__":
    main()
