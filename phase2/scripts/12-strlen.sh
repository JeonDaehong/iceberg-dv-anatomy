#!/usr/bin/env bash
# 문자열 길이 축 — F-017 이 분리하지 못한 것.
#
# 왜 이 축인가:
#   F-017 은 "md5 컬럼 하나 = 정수 컬럼 8~10개어치" 를 얻었고, 그 한계를 이렇게 적었다:
#   *"문자열이라서 비싼지, 바이트가 많아서 비싼지는 분리하지 못한다. s07 은 32자이고
#     k01 은 4바이트다."* 이슈 초안의 caveats 에도 같은 문장이 들어가 있다.
#
#   테이블에 길이가 다른 md5 파생 컬럼이 이미 넷 있다. **타입은 전부 string 으로 같고
#   길이만 다르다.** 그러면 두 가설이 갈린다:
#     - 바이트 가설  : 비용이 길이에 비례한다 (8:16:24:32 = 1:2:3:4)
#     - 문자열 가설  : 길이와 무관하게 평평하다 (UTF8String 할당·객체 처리가 지배)
#
# 컬럼 (phase1/spark/gen_grid.py 의 정의):
#   s10 = substr(md5(id*13), 1, 8)   8자
#   s09 = substr(md5(id*7),  1, 16)  16자
#   s17 = substr(md5(id*17), 1, 24)  24자
#   s07 = md5(id)                    32자
#   id  = bigint                     정수 기준점
#
#   넷 다 md5 파생이라 엔트로피가 높다 — 사전 인코딩이 안 걸려 조건이 같다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   T1. [판정용] 컬럼당 스캔 샘플(= 전체 스캔 샘플 − DV − fixed)이 **길이에 비례**한다.
#       s10:s09:s17:s07 이 1 : 2 : 3 : 4 의 ±25% 안에 든다.
#       - 맞으면 축은 **바이트**다. F-017 의 "md5 = 정수 8~10개" 는 32바이트라서지
#         문자열이라서가 아니고, 이슈 초안의 caveat 을 지울 수 있다.
#       - 평평하면 축은 **문자열 처리 자체**다. 그러면 길이가 아니라 컬럼 개수가
#         중요하고, 처방 문장이 달라진다.
#
#   T2. DV 비중이 길이에 따라 단조 감소한다 (분모가 커지므로).
#
#   T3. 패치의 DV 개선 배율은 길이와 무관하다. DV 체크는 컬럼과 상관없이 행당 1회다.
#       깨지면 귀속이 컬럼 타입에 오염된다는 뜻이다.
#
#   ⚠️ 이 실험이 분리하지 '못하는' 것: Parquet 인코딩 차이. 넷 다 PLAIN 일 것으로
#      보지만 확인하지 않았다. 길이가 짧으면 사전이 걸릴 수도 있는데, 그러면
#      "길이" 가 아니라 "인코딩" 을 재는 것이 된다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
_ITERS_CALLER="${SCAN_ITERS:-}"
source ./config.env
REPS="${_REPS_CALLER:-6}"
export SCAN_ITERS="${_ITERS_CALLER:-$SCAN_ITERS}"

STR_TABLE="${STR_TABLE:-dv.g.d610}"
STR_TAG="${STR_TAG:-d610}"
# 이름:컬럼:길이  (길이는 라벨용)
STR_SETS="${STR_SETS:-int:id:8 s8:s10:8 s16:s09:16 s24:s17:24 s32:s07:32}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

run_one() {   # $1=arm $2=rep $3=setname $4=column
  local TAG="${STR_TAG}L$3"
  local PROF="${RESULTS}/profiles/${1}__${TAG}_c1_r${2}.collapsed"
  local JAR; case "$1" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;;
    *) echo "unknown arm $1" >&2; return 1 ;; esac

  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then echo "      skip $1 r$2"; return; fi

  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$STR_TABLE" --label "${1}_${TAG}_r${2}" \
      --cols 1 --col-list "$4" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${1}__${TAG}_r${2}.json" \
    2>&1 | grep -E "median=" | sed "s|^|      $1 r$2 ($3=$4)\: |" || true
}

SETS=(); for S in $STR_SETS; do SETS+=("$S"); done
echo "문자열 길이 축 — 컬럼 ${#SETS[@]}종 × 2 arm × ${REPS}회 = $(( ${#SETS[@]} * 2 * REPS )) 프로파일"
for S in "${SETS[@]}"; do
  echo "  $(echo "$S" | cut -d: -f1) = $(echo "$S" | cut -d: -f2)  ($(echo "$S" | cut -d: -f3)자)"
done
echo "  SCAN_ITERS=${SCAN_ITERS} SCAN_WARMUP=${SCAN_WARMUP} REPS=${REPS}"
echo

START=$(date +%s); N=0
for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for S in "${SETS[@]}"; do
    NAME=$(echo "$S" | cut -d: -f1); COL=$(echo "$S" | cut -d: -f2)
    echo "  ${NAME} (${COL})  [순서: ${ARMS[*]}]"
    for A in "${ARMS[@]}"; do
      run_one "$A" "$R" "$NAME" "$COL"; N=$((N+1))
    done
  done
  echo
done
echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
