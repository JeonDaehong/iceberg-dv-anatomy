#!/usr/bin/env bash
# p 축 — 밀도와 L 을 고정하고 '점유 청크 수'만 바꾼다.
#
# 왜 필요한가 (F-009 의 교란변수):
#   L 을 키우면 삭제가 소수 청크에 뭉쳐서 컨테이너 개수도 같이 줄었다.
#   L=1 -> 124개, L=4096 -> 12개. 그래서 F-009 의 3.49배가
#   'run 구조' 덕인지 '컨테이너 개수 감소' 덕인지 구분이 안 된다.
#
# 결정적 비교:
#   여기서 p=10% 로 만들면 컨테이너 ≈ 12개인데 타입은 여전히 array 다.
#   F-009 의 L=4096 (컨테이너 12개, run) 와 나란히 놓으면
#   개수 효과와 구조 효과가 분리된다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

: "${OCC_DENSITY_BP:=50}"                 # 청크 안의 밀도 (array 유지)
: "${OCCUPANCIES:=10000 5000 2500 1000 500}"   # p, bp of 10000

mkdir -p "$WAREHOUSE" "$RESULTS/profiles"
export PYTHONIOENCODING=utf-8

for P in $OCCUPANCIES; do
  TAG="d${OCC_DENSITY_BP}p${P}"
  TABLE="dv.g.${TAG}"

  if [[ ! -f "${RESULTS}/gen_${TAG}.json" || "${FORCE:-0}" == "1" ]]; then
    spark-submit \
      --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
      "${SPARK_ICEBERG_ARGS[@]}" \
      --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
      ./spark/gen_grid.py \
        --warehouse "$WAREHOUSE" --table "$TABLE" \
        --density-bp "$OCC_DENSITY_BP" --run-length 1 --occupancy-bp "$P" \
        --rows-per-file "$ROWS_PER_FILE" --num-files "$NUM_FILES" --seed "$SEED" \
        --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
        --out-json "${RESULTS}/gen_${TAG}.json" \
      2>&1 | grep -E "^===|L=|삭제 |DV |레이아웃"
  else
    echo "  skip gen ${TAG}"
  fi

  shopt -s nullglob
  PUFFINS=( "${WAREHOUSE}/g/${TAG}/data"/*.puffin )
  if [[ ${#PUFFINS[@]} -gt 0 ]]; then
    python3 ../tools/dv_inspect.py "${PUFFINS[@]}" \
        --csv "${RESULTS}/containers_${TAG}.csv" --limit 0 >/dev/null 2>&1 || true
  fi

  PROF="${RESULTS}/profiles/${TAG}_c${SCAN_COLS}.collapsed"
  if [[ ! -s "$PROF" || "${FORCE:-0}" == "1" ]]; then
    spark-submit \
      --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
      --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
      "${SPARK_ICEBERG_ARGS[@]}" \
      --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
      ../phase0/spark/scan.py \
        --warehouse "$WAREHOUSE" --table "$TABLE" --label "$TAG" --cols "$SCAN_COLS" \
        --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
        --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
        --out-json "${RESULTS}/scan_${TAG}.json" \
      2>&1 | grep -E "median=|경고"
  else
    echo "  skip profile ${TAG}"
  fi
done

echo
python3 ./tools/occupancy_curve.py "$RESULTS" "$OCC_DENSITY_BP" \
  | tee "${RESULTS}/occupancy_curve.txt"
