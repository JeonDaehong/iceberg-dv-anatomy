#!/usr/bin/env bash
# 배치 크기 축 — 이 결과가 다른 환경으로 얼마나 따라가는지의 '대리 측정'.
#
# 왜 이 축인가:
#   배치 크기는 사용자가 설정으로 바꾸는 값이고(Iceberg 기본 5000, Trino/다른 엔진은 다름),
#   패치의 기전에 직접 얽혀 있다. 현행 코드는 '행당' 이진 탐색이라 배치 크기와 무관하고,
#   패치본은 '배치당' 한 번 순회하므로 고정 비용이 행 수에 분산된다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#   P1. baseline 의 행당 비용은 배치 크기에 거의 무관 -> DV 샘플 수가 평평하다.
#   P2. patched 의 행당 비용은 배치가 커질수록 내려간다.
#   P3. 따라서 speedup(20000) >= speedup(5000) >= speedup(1000).
#   P3 이 깨지면 "배치당 한 번 순회" 라는 기전 설명이 틀린 것이다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

BATCH_SIZES="${BATCH_SIZES:-1000 5000 20000}"
BS_TABLES="${BS_TABLES:-dv.g.d610:d610 dv.g.d50:d50}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

run_one() {   # $1=table $2=tag $3=arm $4=rep $5=batch
  local PROF="${RESULTS}/profiles/${3}__${2}b${5}_c${SCAN_COLS}_r${4}.collapsed"
  local JAR; case "$3" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;;
    *) echo "unknown arm $3" >&2; return 1 ;; esac

  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then echo "      skip ${3} r${4}"; return; fi

  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$1" --label "${3}_${2}b${5}_r${4}" --cols "$SCAN_COLS" \
      --batch-size "$5" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${3}__${2}b${5}_r${4}.json" \
    2>&1 | grep -E "median=" | sed "s/^/      ${3} r${4}: /" || true
}

N=0; START=$(date +%s)
CFG=(); for T in $BS_TABLES; do for B in $BATCH_SIZES; do CFG+=("${T}:${B}"); done; done
echo "설정 ${#CFG[@]}개 × 2 arm × ${REPS}회 = $(( ${#CFG[@]} * 2 * REPS )) 프로파일"
echo

# arm 을 번갈아 재는 이유는 02-profile.sh 와 같다 (머신 상태 표류의 균등 분배).
for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  for C in "${CFG[@]}"; do
    TABLE="${C%%:*}"; REST="${C#*:}"; TAG="${REST%%:*}"; B="${REST##*:}"
    echo "  ${TAG} batch=${B}"
    run_one "$TABLE" "$TAG" baseline "$R" "$B"; N=$((N+1))
    run_one "$TABLE" "$TAG" patched  "$R" "$B"; N=$((N+1))
  done
  echo
done

echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
