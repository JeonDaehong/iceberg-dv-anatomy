#!/usr/bin/env bash
# C 축 — 밀도를 고정하고 평균 run 길이 L 을 스윕한다.
#
# 예측 (반증 가능하게 세움):
#   runOptimize() 는 바이트가 줄 때만 run 으로 바꾼다.
#     RunContainer   = 2 + 4*nRuns
#     ArrayContainer = 2*cardinality       (nRuns = cardinality/L)
#   -> 2 + 4*card/L < 2*card  =>  대략 L > 2 에서 run 컨테이너가 나온다
#   -> RunContainer.contains 는 nRuns 에 대한 이진 탐색이므로
#      L 이 커질수록 nRuns 가 줄어 비용이 단조 감소해야 한다
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

: "${CLUSTER_DENSITY_BP:=50}"          # 고정 밀도 (0.5% = 실무 CDC 대표값)
: "${RUN_LENGTHS:=1 2 4 8 64 512 4096}"

mkdir -p "$WAREHOUSE" "$RESULTS"
echo "고정 밀도 d=${CLUSTER_DENSITY_BP}bp,  run 길이 L: $RUN_LENGTHS"
echo

for L in $RUN_LENGTHS; do
  TAG="d${CLUSTER_DENSITY_BP}L${L}"
  TABLE="dv.g.${TAG}"
  if [[ -f "${RESULTS}/gen_${TAG}.json" && "${FORCE:-0}" != "1" ]]; then
    echo "  skip L=${L} (이미 생성됨)"
    continue
  fi
  spark-submit \
    --master "local[${LOCAL_CORES}]" \
    --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ./spark/gen_grid.py \
      --warehouse "$WAREHOUSE" \
      --table "$TABLE" \
      --density-bp "$CLUSTER_DENSITY_BP" \
      --run-length "$L" \
      --rows-per-file "$ROWS_PER_FILE" \
      --num-files "$NUM_FILES" \
      --seed "$SEED" \
      --cores "$LOCAL_CORES" \
      --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/gen_${TAG}.json" \
    2>&1 | grep -E "^===|predicate|run_length|삭제 |DV |레이아웃"
done

echo
echo "컨테이너 덤프..."
export PYTHONIOENCODING=utf-8
for L in $RUN_LENGTHS; do
  TAG="d${CLUSTER_DENSITY_BP}L${L}"
  shopt -s nullglob
  PUFFINS=( "${WAREHOUSE}/g/${TAG}/data"/*.puffin )
  [[ ${#PUFFINS[@]} -eq 0 ]] && { echo "  L=${L}: puffin 없음"; continue; }
  python3 ../tools/dv_inspect.py "${PUFFINS[@]}" \
      --csv "${RESULTS}/containers_${TAG}.csv" --limit 0 >/dev/null 2>&1 \
    || { echo "  L=${L}: 파싱 실패"; continue; }
  echo "  L=${L}: $(( $(wc -l < "${RESULTS}/containers_${TAG}.csv") - 1 ))개"
done

echo
python3 ./tools/clustering_curve.py "$RESULTS" "$CLUSTER_DENSITY_BP" "$ROWS_PER_FILE" \
  | tee "${RESULTS}/clustering_curve.txt"
