#!/usr/bin/env bash
# 셔플 축 — 쿼리가 스캔 말고 다른 일을 더 하면 이야기가 어떻게 달라지나.
#
# 왜 이 축인가:
#   지금까지의 모든 측정이 `noop` 싱크 스캔이다. 즉 **쿼리 ≈ 스캔** 이었다.
#   그런데 실무 쿼리는 조인·집계를 한다. 그러면 셔플 쓰기/읽기, 해시 집계가
#   분모에 들어오고, 같은 DV 절감이 더 작은 비율로 보인다.
#
#   F-018/F-022 가 낸 손익분기 "DV 비중 20%" 는 **스캔 대비** 값이다. 셔플이 무거운
#   쿼리에 그대로 들이대면 틀린다. 공식이 두 항인데 한 항만 잰 셈이다:
#
#     쿼리 시간 단축 ≈ 0.46 × (DV 의 스캔 내 비중) × (스캔이 쿼리에서 차지하는 몫)
#                              ^ 지금까지 잰 것          ^ 우리 워크로드에선 ≈ 1
#
#   두 번째 항을 재지 않으면 공개 글에서 "조인 쿼리에서는요?" 에 추측으로 답해야 한다.
#
# 무엇을 통제하는가:
#   세 쿼리 모두 **같은 테이블에서 같은 두 컬럼(k01, k04)을 읽는다.**
#   투영한 컬럼을 전부 집계에 소비해(sum) 컬럼 프루닝을 막았다 — 안 그러면 Spark 가
#   안 쓰는 컬럼을 안 읽어서 디코딩 비용이 쿼리마다 달라진다.
#   달라지는 것은 **스캔 뒤에 붙는 일**뿐이다:
#
#     q0 scan   : GROUP BY 없음                  셔플 없음
#     q1 aggS   : GROUP BY k04  (고유값 1,000)   부분집계가 크게 줄여서 셔플이 작다
#     q2 aggL   : GROUP BY k01  (고유값 ~800만)  부분집계가 거의 못 줄여 셔플이 크다
#
#   local[4] 에서도 셔플은 실제로 일어난다 (디스크 경유). 클러스터가 없어도
#   '스캔 밖 일' 을 늘리는 목적에는 충분하다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   Q_S1. [판정용] DV 의 **스캔 내** 비중(dv_pct_of_scan)은 세 쿼리에서 같다 (±5%p).
#         스캔 연산자가 하는 일이 같기 때문이다.
#         깨지면 귀속이 셔플에 오염된다는 뜻이고, 그건 심각하다 —
#         '스캔 서브트리' 정의부터 다시 봐야 하고 F-015~F-023 이 전부 흔들린다.
#
#   Q_S2. DV 의 **전체 대비** 비중은 셔플이 커질수록 떨어진다. q0 > q1 > q2.
#
#   Q_S3. 패치의 wall-clock 단축이 스캔 몫에 비례해 줄어든다:
#           단축(q) ≈ 단축(q0) × wall(q0)/wall(q)     (±5%p)
#         스캔 몫을 프로파일이 아니라 **wall-clock 비**로 잰다 — q0 대비 늘어난 시간이
#         곧 스캔 밖 일이기 때문이다. 이게 맞으면 공개 글에 곱셈 공식을 실을 수 있다.
#         깨지면 셔플이 스캔 자체를 느리게 만드는(GC·메모리 압력) 상호작용이 있다는 뜻이고,
#         그건 곱셈으로 안 잡히므로 "재보라" 고만 써야 한다.
#
#   ⚠️ 이 실험이 재지 '못하는' 것: 분산 클러스터의 네트워크 셔플. local[4] 의 셔플은
#      같은 머신 디스크를 탄다. 네트워크가 들어가면 스캔 몫이 더 떨어질 것이다 — 방향은
#      같고 크기만 다르다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
source ./config.env
REPS="${_REPS_CALLER:-6}"

SHF_TABLE="${SHF_TABLE:-dv.g.d610}"
SHF_TAG="${SHF_TAG:-d610}"
SHF_COLS="${SHF_COLS:-k01,k04}"
# 집계가 붙으면 반복이 비싸다. 세 쿼리에 **같은 값**을 쓰므로 비교는 성립한다.
export SCAN_ITERS="${SCAN_ITERS:-10}"
export SCAN_WARMUP="${SCAN_WARMUP:-2}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

NCOL=$(echo "$SHF_COLS" | tr ',' '\n' | wc -l)

run_one() {   # $1=arm $2=rep $3=query(scan|aggS|aggL)
  local TAG="${SHF_TAG}shf$3"
  local PROF="${RESULTS}/profiles/${1}__${TAG}_c${NCOL}_r${2}.collapsed"
  local JAR; case "$1" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;;
    *) echo "unknown arm $1" >&2; return 1 ;; esac

  local G=()
  case "$3" in
    scan) G=() ;;
    aggS) G=(--group-col k04) ;;
    aggL) G=(--group-col k01) ;;
    *) echo "unknown query $3" >&2; return 1 ;;
  esac

  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then echo "      skip $1 r$2"; return; fi

  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$SHF_TABLE" --label "${1}_${TAG}_r${2}" \
      --cols "$NCOL" --col-list "$SHF_COLS" "${G[@]}" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${1}__${TAG}_r${2}.json" \
    2>&1 | grep -E "median=" | sed "s|^|      $1 r$2 ($3)\: |" || true
}

echo "셔플 축 — 쿼리 3종 × 2 arm × ${REPS}회 = $(( 3 * 2 * REPS )) 프로파일"
echo "  테이블 : $SHF_TABLE   투영 : $SHF_COLS (${NCOL}컬럼, 전부 집계에 소비)"
echo "  q0 scan : 셔플 없음"
echo "  q1 aggS : GROUP BY k04 (고유값 1,000)"
echo "  q2 aggL : GROUP BY k01 (고유값 ~800만)"
echo "  SCAN_ITERS=${SCAN_ITERS} SCAN_WARMUP=${SCAN_WARMUP} REPS=${REPS}"
echo

START=$(date +%s); N=0
for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for Q in scan aggS aggL; do
    echo "  ${Q}  [순서: ${ARMS[*]}]"
    for A in "${ARMS[@]}"; do
      run_one "$A" "$R" "$Q"; N=$((N+1))
    done
  done
  echo
done
echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
