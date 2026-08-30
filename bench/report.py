#!/usr/bin/env python3
"""
JMH JSON -> 패턴별 비교표.

각 삭제 패턴에서 baseline(현행 Iceberg) 대비 배속을 낸다.
JMH 의 오차(99.9% CI)를 함께 표기하고, CI 가 겹치면 '차이 없음'으로 표시한다.
단일 숫자로 "N배"라고 쓰지 않기 위한 장치다.
"""
import json
import sys
from collections import defaultdict

BASELINE = "a_baseline_perRow_RoaringPositionBitmap"

LABELS = {
    "a_baseline_perRow_RoaringPositionBitmap": "A 현행 (RPB.contains/행)",
    "b_perRow_RoaringBitmap":                  "B raw RB.contains/행",
    "c_forAllInRange":                         "C forAllInRange",
    "d_forEachInRange":                        "D forEachInRange+fill",
    "e_batchIterator":                         "E BatchIterator",
    "f_rangeCardFastPath":                     "F rangeCard fast-path",
    "g_isDeleted_baseline":                    "G isDeleted 현행",
    "h_isDeleted_forEachInRange":              "H isDeleted forEachInRange",
}

# rowIdMapping 경로와 isDeleted 경로는 산출물이 달라 서로 비교하면 안 된다.
GROUPS = [
    ("rowIdMapping 경로 (ColumnarBatchUtil.buildRowIdMapping)",
     ["a_baseline_perRow_RoaringPositionBitmap", "b_perRow_RoaringBitmap",
      "c_forAllInRange", "d_forEachInRange", "e_batchIterator", "f_rangeCardFastPath"],
     "a_baseline_perRow_RoaringPositionBitmap"),
    ("isDeleted 경로 (ColumnarBatchUtil.buildIsDeleted)",
     ["g_isDeleted_baseline", "h_isDeleted_forEachInRange"],
     "g_isDeleted_baseline"),
]


def short(bm):
    return bm.rsplit(".", 1)[-1]


def load(path):
    with open(path) as fh:
        raw = json.load(fh)
    # data[pattern][method] = (score, error, unit)
    data = defaultdict(dict)
    batch = None
    for r in raw:
        m = short(r["benchmark"])
        p = r["params"].get("pattern", "-")
        batch = r["params"].get("batchSize", batch)
        pm = r["primaryMetric"]
        data[p][m] = (pm["score"], pm.get("scoreError") or 0.0, pm["scoreUnit"])
    return data, batch


def main():
    if len(sys.argv) < 2:
        print("usage: report.py <jmh-result.json>")
        sys.exit(1)
    data, batch = load(sys.argv[1])

    order = ["SPARSE_0_5", "MEDIUM_5", "DENSE_12", "RUN_CONTIG", "EMPTY_BATCH"]
    patterns = [p for p in order if p in data] + [p for p in data if p not in order]

    print(f"\nbatchSize = {batch}   (단위: us/op, 낮을수록 빠름)")
    print("오차는 JMH 99.9% 신뢰구간. baseline CI 와 겹치면 '~' 로 표시(유의차 없음).\n")

    for title, methods, base_key in GROUPS:
        present = [m for m in methods if any(m in data[p] for p in patterns)]
        if not present:
            continue
        print("=" * 92)
        print(title)
        print("=" * 92)
        hdr = f"  {'구현':<30}" + "".join(f"{p:>14}" for p in patterns)
        print(hdr)
        print("  " + "-" * (30 + 14 * len(patterns)))

        for m in present:
            row = f"  {LABELS.get(m, m):<30}"
            for p in patterns:
                if m not in data[p]:
                    row += f"{'-':>14}"
                    continue
                s, e, _ = data[p][m]
                row += f"{s:>10.2f}±{e:<3.1f}" if e < 100 else f"{s:>14.2f}"
            print(row)

        # 배속
        print()
        print(f"  {'baseline 대비 배속':<30}" + "".join(f"{p:>14}" for p in patterns))
        print("  " + "-" * (30 + 14 * len(patterns)))
        for m in present:
            if m == base_key:
                continue
            row = f"  {LABELS.get(m, m):<30}"
            for p in patterns:
                if m not in data[p] or base_key not in data[p]:
                    row += f"{'-':>14}"
                    continue
                s, e, _ = data[p][m]
                bs, be, _ = data[p][base_key]
                overlap = (s - e) <= (bs + be) and (bs - be) <= (s + e)
                if overlap:
                    row += f"{'~ 차이없음':>14}"
                else:
                    row += f"{bs / s:>13.2f}x"
            print(row)
        print()

    print("=" * 92)
    print("해석 주의")
    print("=" * 92)
    print("""  - 이건 마이크로벤치다. 여기서의 Nx 가 쿼리 Nx 가 아니다.
    실제 절감폭 상한은 Phase 0 게이트가 잰 'DV 체크가 스캔 CPU 에서 차지하는 비중'이다.
  - 버퍼(int[] mapping)를 재사용해 알고리즘만 비교했다. 실제 Iceberg 는
    배치마다 new int[batchSize] 를 할당하므로, 할당/GC 비용은 여기에 없다.
  - Windows/개발 머신 측정치는 상대 비교용이다. 사이클/분기실패 귀속은
    Phase 2 의 .metal 에서 -prof perfnorm/perfasm 으로 확정한다.
  - EMPTY_BATCH 는 '정렬 레이아웃에서 삭제가 소수 배치에 뭉쳤을 때'를 뜻한다.
    README 3-A(레이아웃 처방)와 F(fast-path)가 여기서 만난다.""")


if __name__ == "__main__":
    main()
