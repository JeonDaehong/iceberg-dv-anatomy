#!/usr/bin/env bash
# 반복 측정 — 핵심 곡선 두 개(밀도 축, 클러스터링 축)를 REPS 회씩 다시 잰다.
#
# 왜: 2026-08-17 에 같은 설정을 두 번 재서 303 vs 363 (20%), 120 vs 169 (41%) 가
#     나왔다. 반복 없이는 2배 미만의 차이를 주장할 수 없다.
#     프로파일 1회 비용의 대부분이 JVM 기동이라, SCAN_ITERS 를 올리는 건 거의 공짜다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

: "${CLUSTER_DENSITY_BP:=50}"
: "${RUN_LENGTHS:=1 2 4 8 64 512 4096}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }

run_one() {   # $1=table  $2=tag  $3=rep
  local PROF="${RESULTS}/profiles/${2}_c${SCAN_COLS}_r${3}.collapsed"
  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then
    echo "    skip r${3}"
    return
  fi
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$1" --label "${2}_r${3}" --cols "$SCAN_COLS" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${2}_r${3}.json" \
    2>&1 | grep -E "median=" || true
}

TOTAL=0
START=$(date +%s)

echo "### 밀도 축  (REPS=$REPS, SCAN_ITERS=$SCAN_ITERS)"
for BP in $PROFILE_DENSITIES_BP; do
  echo "  d=${BP}bp"
  for R in $(seq 1 "$REPS"); do run_one "dv.g.d${BP}" "d${BP}" "$R"; TOTAL=$((TOTAL+1)); done
done

echo
echo "### 클러스터링 축"
for L in $RUN_LENGTHS; do
  TAG="d${CLUSTER_DENSITY_BP}L${L}"
  echo "  L=${L}"
  for R in $(seq 1 "$REPS"); do run_one "dv.g.${TAG}" "$TAG" "$R"; TOTAL=$((TOTAL+1)); done
done

echo
echo "✅ 완료 — 프로파일 ${TOTAL}회, $(( $(date +%s) - START ))초"
export PYTHONIOENCODING=utf-8
python3 ./tools/aggregate.py "$RESULTS" | tee "${RESULTS}/summary.txt"
