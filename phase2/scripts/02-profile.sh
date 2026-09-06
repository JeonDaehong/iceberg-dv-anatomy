#!/usr/bin/env bash
# 패치 전후 프로파일 — 같은 테이블, 같은 워크로드, jar 만 다르다.
#
# 두 jar 을 '번갈아' 잰다 (baseline r1, patched r1, baseline r2, ...).
# 이유: 한쪽을 몰아서 재면 머신 상태의 표류(캐시 온도, 백그라운드 부하)가
#       그대로 jar 차이로 둔갑한다. 교차하면 표류가 양쪽에 균등하게 섞인다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR" ]] || { echo "패치 jar 없음: $PATCHED_JAR" >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음: $BASELINE_JAR" >&2; exit 1; }

run_one() {   # $1=table  $2=tag  $3=arm  $4=rep
  local PROF="${RESULTS}/profiles/${3}__${2}_c${SCAN_COLS}_r${4}.collapsed"
  local JAR
  case "$3" in
    baseline) JAR="$BASELINE_JAR" ;;
    patched)  JAR="$PATCHED_JAR" ;;
    *) echo "unknown arm: $3" >&2; return 1 ;;
  esac

  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then
    echo "      skip ${3} r${4}"
    return
  fi

  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$1" --label "${3}_${2}_r${4}" --cols "$SCAN_COLS" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${3}__${2}_r${4}.json" \
    2>&1 | grep -E "median=" | sed "s/^/      ${3} r${4}: /" || true
}

TOTAL=0
START=$(date +%s)

CONFIGS=()
for BP in $P2_DENSITIES_BP; do CONFIGS+=("dv.g.d${BP}:d${BP}"); done
for L in $RUN_LENGTHS;      do CONFIGS+=("dv.g.d${CLUSTER_DENSITY_BP}L${L}:d${CLUSTER_DENSITY_BP}L${L}"); done

echo "설정 ${#CONFIGS[@]}개 × 2 arm × ${REPS}회 = $(( ${#CONFIGS[@]} * 2 * REPS )) 프로파일"
echo

for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  for C in "${CONFIGS[@]}"; do
    TABLE="${C%%:*}"; TAG="${C##*:}"
    echo "  $TAG"
    run_one "$TABLE" "$TAG" baseline "$R"; TOTAL=$((TOTAL+1))
    run_one "$TABLE" "$TAG" patched  "$R"; TOTAL=$((TOTAL+1))
  done
  echo
done

echo "✅ 완료 — 프로파일 ${TOTAL}회, $(( $(date +%s) - START ))초"
export PYTHONIOENCODING=utf-8
python3 ./tools/compare.py "$RESULTS" | tee "${RESULTS}/compare.txt"
