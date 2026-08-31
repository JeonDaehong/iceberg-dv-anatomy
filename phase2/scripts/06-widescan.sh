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
#
# ---------------------------------------------------------------------------
# 2026-08-31 추가 — 손익분기 컬럼 수 (WIDE_COLS=3, 5, 10)
#
# 왜 필요한가:
#   F-015 는 1컬럼과 20컬럼 두 점만 쟀다. 초안의 마지막 문장이
#   "I have not measured where in between the crossover is" 인데,
#   리뷰어가 정확히 그걸 묻는다. 사이를 채워야 "몇 컬럼부터 실효가 있나" 에 답한다.
#
# 모델 (F-015 의 절대 샘플 수에서 유도, d=6.1% 기준):
#   scan(c) ≈ DV + fixed + per_col·c
#   c=1  -> 1,558 샘플,  c=20 -> 19,332 샘플,  DV ≈ 870 (두 폭에서 거의 불변)
#   => per_col ≈ (18,430-688)/19 ≈ 933,  fixed ≈ 0
#   => DV 비중 = 870 / (870 + 933c)
#
# 반증 가능한 예측 (측정 전에 적는다):
#   Q5. DV 비중은 c=3 에서 약 24%, c=5 에서 약 16%, c=10 에서 약 8.5%.
#       (모델이 맞다면 실측이 이 값의 ±5%p 안에 든다. 벗어나면 선형 모델이 틀렸고,
#        컬럼 수에 따라 per-column 비용이 일정하다는 가정부터 다시 봐야 한다.)
#   Q6. 패치는 DV 의 약 87% 를 없애므로(6.3~9.3배), 전체 스캔 감소는
#       c=3 -> ~21%, c=5 -> ~14%, c=10 -> ~7%.
#       노이즈 바닥 9.7%(F-010) 를 넘는 **손익분기는 c=5 와 c=10 사이, 대략 7컬럼**.
#       => c=3 과 c=5 에서는 wall-clock 개선이 보여야 하고, c=10 에서는 안 보여야 한다.
#   Q7. DV 샘플 자체의 개선 배율은 세 폭 모두 6~9배로 컬럼 수와 무관하다.
#       (F-015 Q1 이 20컬럼에서 점추정 3/3 하락을 보였으므로, 그 하락이 실재라면
#        c 가 커질수록 단조 감소해야 한다. 그렇지 않으면 그건 흩어짐이었다.)
#
#   Q6 이 깨지는 방향이 둘 다 의미가 있다:
#     - 손익분기가 c=3 보다 아래면 -> 이 패치는 좁은 투영 전용이고 초안을 더 좁혀야 한다.
#     - c=10 에서도 보이면 -> 모델이 틀렸고 실용 범위가 예상보다 넓다. 좋은 소식.
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

WIDE_COLS="${WIDE_COLS:-20}"
WIDE_TABLES="${WIDE_TABLES:-dv.g.d610:d610 dv.g.d50:d50 dv.g.d700:d700}"

# arm 실행 순서 — 라운드마다 뒤집는다.
#
# 왜 필요한가:
#   r1~r3 는 전부 baseline -> patched 순으로 돌았다. 그 9쌍 중 8쌍에서 patched 의
#   wall-clock 이 더 느렸다(부호검정 p≈0.02). 이 순서 고정에서는
#   "패치가 느리다" 와 "두 번째로 도는 쪽이 느리다"(캐시/열/JIT 상태 드리프트)를
#   분리할 수 없다. 순서를 뒤집은 라운드가 있어야 답이 나온다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#   Q4. patched 를 먼저 돌리면 이번엔 baseline 이 느려진다 = 순서 효과다.
#       이 경우 "패치가 wall-clock 을 악화시킨다" 는 주장은 폐기된다.
#   Q4 가 깨지고 patched 가 순서와 무관하게 계속 느리면, 20컬럼에서 패치가
#   실제로 작은 회귀를 낳는다는 뜻이고 그 기전을 따로 설명해야 한다.
#
# r1~r3 의 순서는 보존한다 — 이미 측정된 데이터의 조건을 사후에 바꾸지 않는다.
FLIP_FROM="${FLIP_FROM:-4}"

# FLIP_MODE — 순서를 어떻게 뒤집을 것인가.
#   block     : 라운드 FLIP_FROM 부터 뒤집는다. w20 이 이렇게 돌았다(전반 3 / 후반 3).
#               블록으로 나뉘므로 '순서' 와 '측정 시각' 이 부분적으로 얽힌다.
#   alternate : 라운드마다 뒤집는다(홀수=baseline 먼저, 짝수=patched 먼저).
#               순서 효과가 시간 표류와 얽히지 않으므로 **신규 축은 이걸 쓴다.**
# 기본값을 block 으로 두는 이유는 오직 하나 — w20 을 FORCE=1 로 다시 돌렸을 때
# 이미 기록된 F-015 와 같은 조건이 재현되어야 하기 때문이다.
FLIP_MODE="${FLIP_MODE:-block}"

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
    case "$FLIP_MODE" in
      alternate) if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi ;;
      block)     if (( R >= FLIP_FROM )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi ;;
      *) echo "unknown FLIP_MODE=$FLIP_MODE (block|alternate)" >&2; exit 1 ;;
    esac
    echo "  ${TAG}  [순서: ${ARMS[*]}]"
    for A in "${ARMS[@]}"; do
      run_one "$TABLE" "$TAG" "$A" "$R"; N=$((N+1))
    done
  done
  echo
done

echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
