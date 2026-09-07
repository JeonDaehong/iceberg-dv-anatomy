#!/usr/bin/env bash
# F-008 의 bitmap 구간 반등(1.26배) 기전 — 이번엔 진짜 분기 가설을 친다.
#
# 왜 별도 스크립트인가 (정직하게):
#   15-qperf.sh 헤더의 Q_P3 을 **잘못 적었다.** 거기서 "d=6.10%(반등 정점)" 이라고 썼는데
#   d=6.10% 는 array 쪽 정점이지 반등이 아니다. F-008 의 반등은 **경계 위**에서
#   d=8%(394) -> d=50%(496) 로 다시 오르는 구간이다. 대상 밀도를 틀리게 지정했으므로
#   Q_P3 은 이 축의 판정에 쓸 수 없고, 여기서 새로 예측을 적는다.
#
# F-028 이 죽인 가설과 **다른 분기**라는 점이 중요하다:
#   F-028 이 반증한 것은 "array vs bitmap 의 비용 차이가 컨테이너 내부(이진 탐색)의
#   분기 예측 실패 때문" 이라는 주장이다. 여기 가설은 다른 분기다 —
#       `if (!rpb.contains(pos)) mapping[live++] = rowId;`
#   삭제 여부에 따라 갈리는 **데이터 의존 분기**이고, d=50% 에서 정확히 동전 던지기가 된다.
#   F-028 때문에 이게 자동으로 틀린 것은 아니다. 따로 재야 한다.
#
# 계산해둔 기대값 (이게 예측을 반증 가능하게 만든다):
#   스캔 1회당 8,000,000 행을 프로브한다. warmup 3 + iters 30 = 33회면 2.64억 행.
#   포화 카운터 예측기는 d=8% 면 "안 삭제됨" 으로 굳어져 실패율이 최대 8%,
#   d=50% 면 예측이 불가능해 약 50% 다. 차이 42%p.
#     추가 실패 기대값 = 2.64억 x 0.42 = 약 1.11억 회
#   실측 총 branch-misses 가 12.0억 수준이므로 이건 **약 9% 증가**로 보여야 한다.
#   노이즈에 묻히지 않는 크기다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   Q_P5. [판정용] d=50% 의 총 branch-misses 가 d=8% 보다 **5% 이상 많다.**
#         (기대값 9%, 안전하게 5% 로 문턱을 잡는다. 라운드 범위가 겹치면 판정 불가로 둔다.)
#         깨지면 반등의 범인은 분기가 아니다 — F-028 과 같은 결론이 이 축에서도 나오는 것이고,
#         그때는 instructions 로 갈려야 한다.
#
#   Q_P6. [기전 확정] 그 추가 실패가 **사이클 차이를 절반 이상 설명한다.**
#         (Δbranch-misses x 18 cycle) / Δcycles >= 50%.
#         Q_P5 가 맞고 Q_P6 이 깨지면 "분기가 늘긴 하는데 비용의 주범은 아니다" 가 된다.
#
#   Q_P7. [대안] instructions 로 설명되는가. d=50% 는 살아남는 행이 절반뿐이라
#         `mapping[live++]` 쓰기가 줄어든다 — 명령어는 오히려 **줄어야** 한다.
#         줄어드는데도 사이클이 늘면 그건 확실히 파이프라인 문제(= 분기)다.
#
# ⚠️ 통제의 한계 (F-008 이 이미 안고 있던 것):
#   d=8% 와 d=50% 는 살아남는 행 수가 다르다 (7.36M vs 4.0M). contains() 호출 횟수는
#   8M 으로 같지만 그 뒤에 이어지는 작업량은 다르다. 이 축은 **완전 통제가 아니다.**
#   그래서 Q_P7 을 같이 둔다 — 명령어 수가 방향을 갈라준다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
source ./config.env
REPS="${_REPS_CALLER:-3}"

PERF_DIR="${PERF_DIR:-/usr/lib/linux-tools-6.8.0-139}"
export PATH="${PERF_DIR}:${PATH}"
export SCAN_ITERS="${SCAN_ITERS:-30}"
export SCAN_WARMUP="${SCAN_WARMUP:-3}"
RB_COLS="${RB_COLS:-1}"
RB_TAGS="${RB_TAGS:-d800 d5000}"

mkdir -p "$RESULTS/qperf" "$RESULTS/profiles"
command -v perf >/dev/null || { echo "perf 없음: $PERF_DIR" >&2; exit 1; }
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

stat_one() {  # $1=tag $2=rep
  local OUT="${RESULTS}/qperf/rb_stat_${1}_r${2}.txt"
  local SJ="${RESULTS}/qperf/rb_scan_${1}_r${2}.json"
  [[ -s "$OUT" && -s "$SJ" && "${FORCE:-0}" != "1" ]] && { echo "    skip ${1} r${2}"; return; }
  perf stat -x, -e cycles,instructions,branches,branch-misses,cache-misses -o "$OUT" -- \
    spark-submit \
      --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
      --jars "$BASELINE_JAR" \
      --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
      ../phase0/spark/scan.py \
        --warehouse "$WAREHOUSE" --table "dv.g.${1}" --label "rb_${1}_r${2}" --cols "$RB_COLS" \
        --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
        --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
        --out-json "$SJ" \
    >/dev/null 2>&1 || true
  # 게이트는 산출물을 본다 (로그가 아니라).
  [[ -s "$OUT" ]] || { echo "    *** perf 출력이 비었다 (${1} r${2})" >&2; return 1; }
  [[ -s "$SJ"  ]] || { echo "    *** 스캔 JSON 이 없다 — 쿼리 실패 (${1} r${2})" >&2; return 1; }
  echo "    ${1} r${2}: $(grep ',branch-misses,' "$OUT" | cut -d, -f1) misses"
}

echo "F-008 반등 기전  (${RB_TAGS}, ${RB_COLS}컬럼, iters=${SCAN_ITERS}, REPS=${REPS})"
echo "  삭제 판정 분기가 d=50% 에서 동전 던지기가 되는가를 직접 센다."
echo
for R in $(seq 1 "$REPS"); do
  # 태그 순서를 라운드마다 교대한다.
  if (( R % 2 == 1 )); then ORDER="$RB_TAGS"; else ORDER=$(echo "$RB_TAGS" | tr ' ' '\n' | tac | tr '\n' ' '); fi
  echo "  라운드 $R  순서: $ORDER"
  for T in $ORDER; do stat_one "$T" "$R"; done
done
echo
echo "채점: python3 tools/rebound.py results"
