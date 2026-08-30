#!/usr/bin/env bash
# 배관 확인: --batch-size 가 정말 Iceberg 리더에 도달하는가?
#
# 조용히 무시되면 스윕 결과가 전부 "차이 없음" 으로 나오고, 우리는 그걸
# '배치 크기는 영향이 없다' 로 오독하게 된다. 그래서 먼저 확인한다.
#
# 방법: 배치 10 vs 5000. 배치 10 이면 배치 개수가 500배가 되어 배치당 고정 비용
#       (ColumnarBatch 할당, rowIdMapping 배열, 리더 상태 갱신)이 폭발한다.
#       옵션이 먹으면 명백히 느려야 한다. 시간이 같으면 옵션이 무시된 것이다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

TABLE="${PROBE_TABLE:-dv.g.d610}"
for B in 10 5000; do
  echo "### batch-size = $B"
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --jars "$BASELINE_JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$TABLE" --label "probe_b${B}" --cols 1 \
      --batch-size "$B" --warmup 1 --iters 5 \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
    2>&1 | grep -E "median=|배치" || true
done
