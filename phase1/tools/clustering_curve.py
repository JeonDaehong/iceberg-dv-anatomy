#!/usr/bin/env python3
"""
평균 run 길이 L -> 컨테이너 타입 / 크기 / run 수.

검증 대상 예측:
  RunContainer   = 2 + 4*nRuns 바이트
  ArrayContainer = 2*cardinality 바이트
  nRuns ≈ cardinality / L
  -> run 이 array 보다 작아지는 조건: 2 + 4*card/L < 2*card  =>  L > 2 부근
  -> 따라서 L>=4 부터 run 컨테이너가 물질화되어야 한다
"""
import csv
import glob
import json
import os
import re
import sys
from collections import Counter

CHUNK = 65536


def load(results_dir, density_bp):
    out = {}
    pat = os.path.join(results_dir, f"containers_d{density_bp}L*.csv")
    for path in glob.glob(pat):
        m = re.search(rf"containers_d{density_bp}L(\d+)\.csv$", path)
        if not m:
            continue
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        if rows:
            out[int(m.group(1))] = rows
    return out


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    density_bp = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    rows_per_file = int(sys.argv[3]) if len(sys.argv) > 3 else 2000000
    d = density_bp / 10000.0

    data = load(results, density_bp)
    if not data:
        print(f"containers_d{density_bp}L*.csv 가 없습니다. 04-gen-clustering.sh 를 먼저 실행하세요.")
        sys.exit(1)

    last_chunk = (rows_per_file - 1) // CHUNK

    print("=" * 100)
    print(f" 클러스터링(L) -> 컨테이너   [고정 밀도 d={d*100:.2f}%]")
    print("=" * 100)
    print(f"  {'L':>6} {'array':>6} {'bitmap':>7} {'run':>5} | {'평균card':>9} "
          f"{'평균nRuns':>10} {'예측nRuns':>10} | {'DV bytes':>10} {'array대비':>9} {'예측타입':>8}")
    print("  " + "-" * 96)

    curve = []
    for L in sorted(data):
        rows = [r for r in data[L] if int(r["chunk"]) != last_chunk]  # 부분 청크 제외
        if not rows:
            rows = data[L]
        types = Counter(r["container"] for r in rows)
        cards = [int(r["cardinality"]) for r in rows]
        mean_card = sum(cards) / len(cards)
        runs = [int(r["runs"]) for r in rows if r["runs"]]
        mean_runs = sum(runs) / len(runs) if runs else None
        total_bytes = sum(int(r["bytes"]) for r in data[L])

        # 이 카디널리티를 array 로 저장했을 때의 바이트
        array_bytes = sum(2 * int(r["cardinality"]) for r in data[L])
        ratio = total_bytes / array_bytes if array_bytes else 0

        pred_runs = mean_card / L
        pred_type = "run" if (2 + 4 * pred_runs) < min(2 * mean_card, 8192) else (
            "bitmap" if 2 * mean_card > 8192 else "array")

        mr = f"{mean_runs:>10,.0f}" if mean_runs is not None else f"{'-':>10}"
        print(f"  {L:>6} {types.get('array',0):>6} {types.get('bitmap',0):>7} "
              f"{types.get('run',0):>5} | {mean_card:>9,.0f} {mr} {pred_runs:>10,.0f} | "
              f"{total_bytes:>10,} {ratio:>8.3f}x {pred_type:>8}")

        curve.append({
            "run_length": L, "density_bp": density_bp,
            "array": types.get("array", 0), "bitmap": types.get("bitmap", 0),
            "run": types.get("run", 0),
            "mean_cardinality": mean_card, "mean_runs": mean_runs,
            "predicted_runs": pred_runs, "predicted_type": pred_type,
            "container_bytes": total_bytes, "bytes_vs_array": ratio,
        })

    print()
    print("=" * 100)
    print(" 판정")
    print("=" * 100)

    first_run = next((c for c in curve if c["run"] > 0), None)
    last_nonrun = None
    for c in curve:
        if c["run"] == 0:
            last_nonrun = c
        elif first_run and c["run_length"] >= first_run["run_length"]:
            break
    if first_run:
        print(f"  run 컨테이너가 처음 나타난 L: {first_run['run_length']}"
              + (f"  (그 아래 L={last_nonrun['run_length']} 은 run 0개)" if last_nonrun else ""))
        print(f"  -> 예측(L>2 부근) {'부합' if first_run['run_length'] <= 4 else '불일치'}")
    else:
        print("  run 컨테이너가 한 번도 안 나왔습니다 — 예측 불일치. runOptimize 조건 재검토 필요.")

    # nRuns 예측 정확도
    checks = [c for c in curve if c["mean_runs"]]
    if checks:
        err = max(abs(c["mean_runs"] - c["predicted_runs"]) / max(c["predicted_runs"], 1)
                  for c in checks)
        print(f"  nRuns 예측 최대 상대오차: {err*100:.1f}%")

    best = min(curve, key=lambda c: c["container_bytes"])
    worst = max(curve, key=lambda c: c["container_bytes"])
    print(f"  DV 크기: L={worst['run_length']} 에서 {worst['container_bytes']:,}B"
          f"  ->  L={best['run_length']} 에서 {best['container_bytes']:,}B"
          f"  ({worst['container_bytes']/max(best['container_bytes'],1):.0f}배 감소)")

    with open(os.path.join(results, f"clustering_curve_d{density_bp}.json"), "w") as fh:
        json.dump(curve, fh, indent=2)
    print(f"\n  -> clustering_curve_d{density_bp}.json")
    print("\n  주의: 여기까지는 '저장' 측면이다. 스캔 비용은 05-profile-clustering.sh 로 잰다.")


if __name__ == "__main__":
    main()
