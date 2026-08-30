#!/usr/bin/env bash
# C 축 비용 프로파일링. 밀도 고정이므로 삭제 행 수도 거의 같다
# -> 컨테이너 타입/구조 차이만 남는, 밀도 축보다 더 깨끗한 비교다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

: "${CLUSTER_DENSITY_BP:=50}"
: "${RUN_LENGTHS:=1 2 4 8 64 512 4096}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }

for L in $RUN_LENGTHS; do
  TAG="d${CLUSTER_DENSITY_BP}L${L}"
  PROF="${RESULTS}/profiles/${TAG}_c${SCAN_COLS}.collapsed"
  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then
    echo "  skip $TAG"
    continue
  fi
  echo
  echo "###### 프로파일: d=${CLUSTER_DENSITY_BP}bp  L=${L}"

  spark-submit \
    --master "local[${LOCAL_CORES}]" \
    --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" \
      --table "dv.g.${TAG}" \
      --label "$TAG" \
      --cols "$SCAN_COLS" \
      --warmup "$SCAN_WARMUP" \
      --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" \
      --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${TAG}.json" \
    2>&1 | grep -E "median=|경고"
done

echo
export PYTHONIOENCODING=utf-8
python3 ./tools/clustering_cost.py "$RESULTS" "$CLUSTER_DENSITY_BP" \
  | tee "${RESULTS}/clustering_cost.txt"
