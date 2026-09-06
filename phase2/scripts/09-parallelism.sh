#!/usr/bin/env bash
# 병렬도 축 — F-018 의 0.46 이 local[4] 의 상수인가.
#
# 왜 이 축인가:
#   이 연구에서 실무자가 실제로 행동에 옮길 유일한 숫자가 **손익분기 DV 비중 21%** 인데,
#   그 21% 는 F-018 의 `wall 단축 = 0.53 × 샘플 감소` 에서 나온다. 그리고 그 0.53 은
#   **local[4] 한 조건**에 적합시킨 값이다. 즉 가장 행동 가능한 숫자가 가장 흔들리는
#   고리에 매달려 있다.
#
#   F-018 이 "왜 절반만 도착하나" 의 후보로 적어둔 것이 꼬리 태스크다 — CPU-초는 균일하게
#   줄어도 wall-clock 은 가장 느린 태스크 하나가 정하므로 균일한 절감이 덜 반영된다.
#   그 가설이 맞다면 **병렬도 1 에서는 꼬리가 없어야 한다.** 클러스터가 필요 없고
#   한 대에서 local[N] 만 스윕하면 가설이 직접 찔린다.
#
# 무엇을 통제하는가:
#   - split-size 를 **모든 N 에 동일하게** 고정한다. 기본(128MB)이면 이 테이블
#     (4파일 × ~200MB)이 스플릿 8개라 local[16] 에서 코어 절반이 논다 —
#     병렬도를 안 올린 것과 같아진다. 32MB 로 낮춰 ~26 스플릿을 만든다.
#   - DRIVER_MEM 도 N 에 상관없이 고정한다. N 에 따라 바꾸면 그게 교란이다.
#   - 폭은 c=1 과 c=5 만 쓴다. 둘 다 로컬에서 wall 단축이 '주장 가능' 이었던 폭이라
#     (-18.8%, -15.2%) 신호가 확실하다. c=20 은 노이즈 안이라 기울기에 못 쓴다.
#   => 바뀌는 것은 병렬도뿐이다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   N1. [판정용] 샘플→wall 보정비(스캔 샘플 감소 ÷ wall 단축)가 병렬도에 따라 **커진다.**
#       local[1] 에서 1.0~1.3, local[4] 에서 1.9 부근(F-018 재현), local[16] 에서 2.5 이상.
#       근거: local[1] 은 태스크가 순차 실행이라 꼬리가 없다 — CPU 절감이 그대로 wall 이 된다.
#       깨지는 방향이 둘 다 의미가 있다:
#         - 보정비가 N 과 무관하게 일정하면 -> 0.46 은 병렬도의 함수가 아니다.
#           손익분기 21% 를 그대로 실을 수 있고, 덤으로 '왜 절반인가' 의 답이
#           꼬리 태스크가 **아니라** 스캔 서브트리 밖 고정비용이라는 뜻이 된다.
#         - 커지면 -> 0.46 과 21% 는 local[4] 의 값이다. 공개 글에는 DV 비중만 싣고
#           임계값은 "네 환경에서 재라" 로 바꿔야 한다.
#
#   N2. DV 비중은 병렬도와 무관하다 (같은 폭끼리 ±5%p). 행당 비용이기 때문이다.
#       깨지면 귀속이 병렬도에 오염된다는 뜻이고 F-015~F-018 전부를 다시 봐야 한다.
#
#   N3. 패치의 DV 개선 배율은 병렬도와 무관하게 6~9배 (d=6.1%).
#
#   ⚠️ 이 실험이 재지 '못하는' 것: 진짜 클러스터의 셔플·네트워크·익스큐터 스케줄링.
#      local[N] 은 한 JVM 안의 스레드 풀이다. 여기서 얻는 것은 '태스크 병렬도가
#      보정비를 움직이는가' 까지이고, 분산 실행 전체가 아니다.
#
#   ⚠️ split-size 를 바꿨으므로 절대 샘플 수를 다른 실험과 직접 비교하면 안 된다.
#      이 실험 안에서의 비교만 유효하다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
source ./config.env
REPS="${_REPS_CALLER:-6}"

PAR_TABLE="${PAR_TABLE:-dv.g.d610}"
PAR_TAG="${PAR_TAG:-d610}"
PAR_CORES="${PAR_CORES:-1 2 4 16}"
PAR_COLS="${PAR_COLS:-1 5}"
PAR_SPLIT="${PAR_SPLIT:-33554432}"        # 32MB — 모든 N 에 동일
PAR_MEM="${PAR_MEM:-16g}"                 # N 과 무관하게 고정
export SCAN_ITERS="${SCAN_ITERS:-20}"
export SCAN_WARMUP="${SCAN_WARMUP:-3}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

run_one() {   # $1=arm $2=rep $3=cols $4=cores
  local T="${PAR_TAG}p${4}c${3}"
  local PROF="${RESULTS}/profiles/${1}__${T}_c${3}_r${2}.collapsed"
  local JAR; case "$1" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;;
    *) echo "unknown arm $1" >&2; return 1 ;; esac

  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then echo "      skip $1 r$2"; return; fi

  spark-submit \
    --master "local[${4}]" --driver-memory "${PAR_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$PAR_TABLE" --label "${1}_${T}_r${2}" \
      --cols "$3" --split-size "$PAR_SPLIT" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$4" --driver-mem "$PAR_MEM" \
      --out-json "${RESULTS}/scan_${1}__${T}_r${2}.json" \
    2>&1 | grep -E "median=" | sed "s|^|      $1 r$2 (local[$4], c=$3)\: |" || true
}

START=$(date +%s); N=0
NC=$(echo "$PAR_CORES" | wc -w); NW=$(echo "$PAR_COLS" | wc -w)
echo "병렬도 ${NC}개 × 폭 ${NW}개 × 2 arm × ${REPS}회 = $(( NC * NW * 2 * REPS )) 프로파일"
echo "  local[N] : $PAR_CORES"
echo "  폭       : $PAR_COLS"
echo "  split    : ${PAR_SPLIT} 바이트 (모든 N 동일)  mem: ${PAR_MEM} (고정)"
echo "  SCAN_ITERS=${SCAN_ITERS} SCAN_WARMUP=${SCAN_WARMUP} REPS=${REPS}"
echo "  vCPU     : $(nproc)"
echo

for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for P in $PAR_CORES; do
    for C in $PAR_COLS; do
      echo "  local[$P] ${C}컬럼  [순서: ${ARMS[*]}]"
      for A in "${ARMS[@]}"; do
        run_one "$A" "$R" "$C" "$P"; N=$((N+1))
      done
    done
  done
  echo
done
echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
