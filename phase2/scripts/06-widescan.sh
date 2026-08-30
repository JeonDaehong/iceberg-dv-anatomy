#!/usr/bin/env bash
# 넓은 투영(20컬럼)에서 패치가 얼마나 남는가 — 실용성 주장의 유일한 공백.
#
# 왜 필요한가:
#   F-011 의 9.28배는 전부 1컬럼이다. 그런데 F-005 에서 이미 알고 있다 —
#   투영을 20컬럼으로 넓히면 DV 비중이 40.4% -> 3.75% 로 떨어진다.
#   비중이 작아진 자리에서 9배를 줄여봐야 전체로는 미미할 수 있다.
#   "그래서 실무에서 몇 % 빨라지나" 에 답이 없으면 실용성 주장이 통째로 빈다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#   Q1. DV 샘플 자체의 개선 배율은 1컬럼과 비슷하다 (약 8~9배).
#       DV 체크는 행당 1회로 컬럼 수와 무관하고, 패치는 그 안쪽만 바꾸기 때문.
#   Q2. 그러나 스캔 서브트리 전체의 감소는 훨씬 작다 — 대략 3~4% 수준.
#       분모(Parquet 디코딩)가 20배로 커졌으므로.
#   Q3. wall-clock 차이는 노이즈 바닥(9.7%) 안으로 들어가 주장 불가가 된다.
#
#   Q1 이 깨지면 "패치는 DV 체크 내부만 건드린다" 는 설명이 틀린 것이다.
#   Q2 가 예측보다 크게 나오면 그건 좋은 소식이지만 이유를 따로 설명해야 한다.
#
# 태그에 w20 을 붙이는 이유: compare.py 의 태그 정규식이 _c<컬럼수>_ 를 버리므로,
# 접미사를 안 붙이면 1컬럼 프로파일과 같은 태그로 합쳐져 버린다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

WIDE_COLS="${WIDE_COLS:-20}"
WIDE_TABLES="${WIDE_TABLES:-dv.g.d610:d610 dv.g.d50:d50 dv.g.d700:d700}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

run_one() {   # $1=table $2=tag $3=arm $4=rep
  local T="${2}w${WIDE_COLS}"
  local PROF="${RESULTS}/profiles/${3}__${T}_c${WIDE_COLS}_r${4}.collapsed"
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
      --warehouse "$WAREHOUSE" --table "$1" --label "${3}_${T}_r${4}" --cols "$WIDE_COLS" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${3}__${T}_r${4}.json" \
    2>&1 | grep -E "median=" | sed "s/^/      ${3} r${4}: /" || true
}

N=0; START=$(date +%s)
CFG=(); for T in $WIDE_TABLES; do CFG+=("$T"); done
echo "설정 ${#CFG[@]}개 × 2 arm × ${REPS}회 = $(( ${#CFG[@]} * 2 * REPS )) 프로파일 (${WIDE_COLS}컬럼)"
echo

for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  for C in "${CFG[@]}"; do
    TABLE="${C%%:*}"; TAG="${C##*:}"
    echo "  ${TAG}"
    run_one "$TABLE" "$TAG" baseline "$R"; N=$((N+1))
    run_one "$TABLE" "$TAG" patched  "$R"; N=$((N+1))
  done
  echo
done

echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
