#!/usr/bin/env bash
# 밀도별 스캔 프로파일링 -> 밀도 → 비용 곡선.
#
# 설계 이점: 밀도가 달라도 contains() 호출 횟수는 완전히 동일하다
# (buildRowIdMapping 은 배치마다 batchSize 번 무조건 프로브한다).
# 총 스캔 행 수가 800만으로 고정이므로, 밀도 간 DV 샘플 수를
# 별도 정규화 없이 직접 비교하면 그게 곧 '호출당 비용' 이다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }

for BP in $PROFILE_DENSITIES_BP; do
  LABEL="d${BP}_c${SCAN_COLS}"
  PROF="${RESULTS}/profiles/${LABEL}.collapsed"
  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then
    echo "  skip $LABEL (이미 있음)"
    continue
  fi
  echo
  echo "###### 프로파일: d=${BP}bp  cols=${SCAN_COLS}  event=${AP_EVENT}"

  spark-submit \
    --master "local[${LOCAL_CORES}]" \
    --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" \
      --table "dv.g.d${BP}" \
      --label "$LABEL" \
      --cols "$SCAN_COLS" \
      --warmup "$SCAN_WARMUP" \
      --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" \
      --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${LABEL}.json" \
    2>&1 | grep -E "median=|벡터화|경고"

  [[ -s "$PROF" ]] && echo "  -> $(wc -l < "$PROF") stacks" || echo "  !! 프로파일 비어있음"
done

echo
echo "✅ 프로파일링 완료"
