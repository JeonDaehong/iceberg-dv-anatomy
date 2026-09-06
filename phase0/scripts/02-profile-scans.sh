#!/usr/bin/env bash
# 패턴 x 컬럼폭 조합마다 별도 JVM 으로 스캔하며 async-profiler 로 프로파일링.
#
# 폭마다 JVM 을 분리하는 이유: 하나의 collapsed 파일에 좁은 스캔과 넓은 스캔이
# 섞이면 "DV 비중은 컬럼 수에 반비례한다"는 핵심 관찰을 분리해낼 수 없다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

mkdir -p "$RESULTS/profiles"

if [[ ! -f "$AP_LIB" ]]; then
  echo "async-profiler 없음: $AP_LIB" >&2; exit 1
fi

run_one() {
  local PAT="$1" NCOLS="$2"
  local LABEL="${PAT}_c${NCOLS}"
  local PROF="${RESULTS}/profiles/${LABEL}.collapsed"
  local AGENT="-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}"

  echo
  echo "########################################################################"
  echo "# 프로파일: pattern=${PAT}  cols=${NCOLS}  event=${AP_EVENT}"
  echo "########################################################################"

  # local 모드에서는 driver JVM 이 곧 executor 다.
  # client 모드 driver 옵션은 --driver-java-options 로만 확실히 전달된다.
  spark-submit \
    --master "local[${LOCAL_CORES}]" \
    --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "${AGENT}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ./spark/scan.py \
      --warehouse "$WAREHOUSE" \
      --table "dv.db.t_${PAT}" \
      --label "$LABEL" \
      --cols "$NCOLS" \
      --warmup "$SCAN_WARMUP" \
      --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" \
      --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${LABEL}.json"

  if [[ -s "$PROF" ]]; then
    echo "  -> $PROF ($(wc -l < "$PROF") stacks)"
  else
    echo "  !! 프로파일이 비었습니다: $PROF" >&2
    echo "     agent 옵션이 전달됐는지, JVM 이 정상 종료했는지 확인하세요." >&2
  fi
}

for PAT in $PATTERNS; do
  run_one "$PAT" "$NARROW_COLS"
  run_one "$PAT" "$WIDE_COLS"
done

echo
echo "✅ 프로파일링 완료 -> ${RESULTS}/profiles/"
