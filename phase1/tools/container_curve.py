#!/usr/bin/env python3
"""
밀도 -> 컨테이너 타입 혼합 비율 곡선.

검증 대상 예측 (config.env 참조):
  청크당 카디널리티 ~ Binomial(65536, d), 표준편차 ≈ 62 @ d=6.25%
  -> 4096 임계값이 d=6.25% 에서 정확히 평균에 걸리므로 array:bitmap ≈ 50:50
  -> 전이 구간은 d ∈ [5.9%, 6.6%] 로 좁아야 한다
"""
import csv
import glob
import json
import math
import os
import re
import sys
from collections import Counter

CHUNK = 65536
THRESHOLD = 4096


def load(results_dir):
    out = {}
    for path in glob.glob(os.path.join(results_dir, "containers_*.csv")):
        m = re.search(r"containers_(\d+)\.csv$", path)
        if not m:
            continue
        bp = int(m.group(1))
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        if rows:
            out[bp] = rows
    return out


def normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def predicted_array_prob(d, capacity):
    """정규근사로 P(청크 카디널리티 <= 4096) 을 예측한다.

    capacity 는 그 청크가 담을 수 있는 position 수다. 마지막 청크는
    파일 행수가 65536 의 배수가 아니면 부분 청크가 되므로 capacity 가 작다.
    """
    mu = capacity * d
    sd = math.sqrt(capacity * d * (1 - d))
    if sd == 0:
        return 1.0 if mu <= THRESHOLD else 0.0
    return normal_cdf((THRESHOLD + 0.5 - mu) / sd)  # 연속성 보정


def chunk_capacity(chunk_idx, rows_per_file):
    """청크 인덱스의 실제 용량. 마지막 청크는 부분 청크다."""
    start = chunk_idx * CHUNK
    if start >= rows_per_file:
        return 0
    return min(CHUNK, rows_per_file - start)


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    rows_per_file = int(sys.argv[2]) if len(sys.argv) > 2 else 2000000
    data = load(results)
    if not data:
        print("containers_*.csv 가 없습니다. 02-inspect.sh 를 먼저 실행하세요.")
        sys.exit(1)

    last_chunk = (rows_per_file - 1) // CHUNK
    partial_cap = chunk_capacity(last_chunk, rows_per_file)
    partial = partial_cap < CHUNK

    print(f"  파일당 {rows_per_file:,} 행 -> 청크 0..{last_chunk}")
    if partial:
        flip = THRESHOLD / partial_cap
        print(f"  마지막 청크는 부분 청크 (용량 {partial_cap:,} = {100*partial_cap/CHUNK:.1f}%)")
        print(f"  -> 이 청크만 d≈{flip*100:.2f}% 에서 bitmap 으로 넘어간다 (전체 청크는 6.25%)")
    print()

    print("=" * 104)
    print(" 밀도 -> 컨테이너 혼합   (full = 온전한 65536 청크, part = 파일 끝 부분 청크)")
    print("=" * 104)
    print(f"  {'d':>7} {'array':>6} {'bitmap':>7} {'run':>4} | "
          f"{'full array%':>12} {'예측%':>7} | {'part array%':>12} {'예측%':>7} | "
          f"{'평균card':>9} {'sd':>7} {'DV bytes':>10}")
    print("  " + "-" * 100)

    curve = []
    for bp in sorted(data):
        rows = data[bp]
        d = bp / 10000.0
        types = Counter(r["container"] for r in rows)
        total_bytes = sum(int(r["bytes"]) for r in rows)

        full, part = [], []
        for r in rows:
            cap = chunk_capacity(int(r["chunk"]), rows_per_file)
            (full if cap == CHUNK else part).append(r)

        def share(group):
            if not group:
                return None
            return 100.0 * sum(1 for r in group if r["container"] == "array") / len(group)

        def pred(group):
            if not group:
                return None
            return 100.0 * sum(
                predicted_array_prob(d, chunk_capacity(int(r["chunk"]), rows_per_file))
                for r in group) / len(group)

        fcards = [int(r["cardinality"]) for r in full] or [0]
        mean = sum(fcards) / len(fcards)
        sd = (sum((c - mean) ** 2 for c in fcards) / len(fcards)) ** 0.5 if len(fcards) > 1 else 0.0

        fs, ps = share(full), share(part)
        fp, pp = pred(full), pred(part)

        def fmt(v, suffix="%"):
            return f"{v:>11.1f}{suffix}" if v is not None else f"{'-':>12}"

        print(f"  {d*100:>6.2f}% {types.get('array',0):>6} {types.get('bitmap',0):>7} "
              f"{types.get('run',0):>4} | {fmt(fs)} {fp:>6.1f}% | {fmt(ps)} {pp:>6.1f}% | "
              f"{mean:>9,.0f} {sd:>7.1f} {total_bytes:>10,}")

        curve.append({
            "density_bp": bp, "density": d,
            "chunks_full": len(full), "chunks_partial": len(part),
            "array": types.get("array", 0), "bitmap": types.get("bitmap", 0),
            "run": types.get("run", 0),
            "full_array_share": fs, "full_predicted": fp,
            "partial_array_share": ps, "partial_predicted": pp,
            "mean_cardinality_full": mean, "sd_cardinality_full": sd,
            "container_bytes": total_bytes,
        })

    print()
    print("=" * 104)
    print(" 판정")
    print("=" * 104)

    # 전이 구간은 '온전한 청크' 기준으로만 의미가 있다.
    trans = [c for c in curve if c["full_array_share"] is not None
             and 1.0 < c["full_array_share"] < 99.0]
    if trans:
        lo, hi = min(c["density"] for c in trans), max(c["density"] for c in trans)
        print(f"  전이 구간 (온전 청크 array 1~99%): d ∈ [{lo*100:.2f}%, {hi*100:.2f}%]"
              f"  폭 {(hi-lo)*100:.2f}%p")
    else:
        print("  전이 구간이 샘플에 안 잡혔습니다 — 밀도 해상도를 높이세요.")

    at625 = next((c for c in curve if c["density_bp"] == 625), None)
    if at625 and at625["full_array_share"] is not None:
        print(f"  d=6.25%  온전 청크 array 비율: 실측 {at625['full_array_share']:.1f}%"
              f"  (예측 {at625['full_predicted']:.1f}%)")
        ok = abs(at625["full_array_share"] - at625["full_predicted"]) < 15
        print(f"  -> 이항분포 예측 {'일치' if ok else '불일치 (모델 재검토 필요)'}")

    err = max((abs(c["full_array_share"] - c["full_predicted"])
               for c in curve if c["full_array_share"] is not None), default=0)
    print(f"  온전 청크 전 구간 예측 최대 오차: {err:.1f}%p")

    # 저장 크기 평탄 구간: bitmap 컨테이너는 8192B 고정이므로 밀도와 무관해진다
    big = [c for c in curve if c["bitmap"] > 0]
    if big:
        flat = [c for c in big if c["container_bytes"] > 0.97 * max(x["container_bytes"] for x in big)]
        if len(flat) > 1:
            lo, hi = min(c["density"] for c in flat), max(c["density"] for c in flat)
            print(f"  DV 크기 평탄 구간: d ∈ [{lo*100:.2f}%, {hi*100:.2f}%] 에서 "
                  f"바이트가 3% 이내로 동일 (bitmap 8192B 고정)")

    with open(os.path.join(results, "container_curve.json"), "w") as fh:
        json.dump(curve, fh, indent=2)
    print(f"\n  -> {os.path.join(results, 'container_curve.json')}")


if __name__ == "__main__":
    main()
