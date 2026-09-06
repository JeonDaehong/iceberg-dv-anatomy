#!/usr/bin/env bash
# 콜드 캐시 — "이 스캔이 정말 CPU 바운드인가" 에 직접 답한다.
#
# 막으려는 공격 (docs/threats.md A):
#   "클러스터를 늘리면 되는 것 아니냐 / S3 에서는 I/O 바운드라 CPU 가 놀고 있으니 공짜다"
#
# 우리 답은 "이건 행당 CPU 비용이라 노드를 늘려도 CPU-초는 그대로다" 인데,
# 그 반박이 성립하려면 CPU 가 실제로 병목임을 보여야 한다.
# 지금까지의 모든 측정은 825 MB 테이블 / 15 GiB RAM 의 웜 캐시에서 나왔다.
#
# ⚠️ 한계: 로컬 디스크 콜드 리드는 S3 의 대리 지표다. S3 는 지연이 크고 병렬
#    프리페치가 있어 양상이 다르다. 여기서 보는 것은 'I/O 가 분모에 들어온 경우' 뿐이다.
#
# 반증 가능한 예측:
#   R1. 콜드에서 DV 비중이 웜보다 뚜렷하게 낮아진다 (분모에 I/O 가 더해지므로).
#   R2. 그러나 DV 체크의 '절대' 비용(샘플 수)은 웜과 콜드가 비슷하다 —
#       같은 행 수에 같은 일을 하기 때문. 이게 깨지면 귀속이 잘못된 것이다.
#   R3. 콜드에서도 패치의 개선 방향은 유지되나 전체 대비 효과는 작아진다.
#
# ---------------------------------------------------------------------------
# 2026-09-01 추가 — F-018 의 독립 검증
#
# F-018 은 "wall 단축 ≈ 0.46 × DV 비중" 을 냈지만, 그건 예측을 먼저 적고 잰 게 아니라
# 이미 있는 24점에 얹은 사후 회귀다. 그리고 그 24점은 전부 웜 캐시 · local[4] 다.
# 콜드는 분모(스캔 전체)에 I/O 를 더해서 DV 비중을 바꾼다 — 즉 회귀를 적합시킨 적 없는
# 조건이다. 관계식이 조건을 넘어 성립하는지 볼 수 있는 유일한 자리다.
#
#   R4. 콜드에서도 wall 단축 ≈ 0.46 × (콜드에서 실측한 DV 비중) 이 ±5%p 안에서 성립한다.
#       깨지면 0.46 은 웜 조건에만 맞춘 상수이고, F-018 을 "법칙" 으로 쓰면 안 된다.
#       성립하면 손익분기 임계값(DV 비중 21%)이 스토리지 조건을 넘어 살아남는다.
#
#   ⚠️ 정직하게: 이 예측을 적는 시점에 라운드 1 의 wall-clock 중앙값 4개(1컬럼 웜/콜드,
#      baseline/patched)는 이미 봤다. DV 비중은 웜·콜드 어느 쪽도 아직 안 봤고,
#      R4 는 비중을 입력으로 받는 관계식이므로 그 4개로는 답을 알 수 없다.
#
# ---------------------------------------------------------------------------
# 2026-09-06 추가 — 순서 교란을 걷어낸다 (F-019 의 자기 지적)
#
# 라운드 1~6 은 전부 baseline -> patched 고정 순서로 돌았다. F-015 에서 순서 효과에
# 속을 뻔한 뒤 신규 축의 기본으로 삼은 교대가 이 스크립트에만 안 들어가 있었다.
# 그래서 F-019 의 콜드 wall-clock '크기' 는 해석할 수 없다 —
# 콜드 1컬럼이 -27.1% 로 웜의 -20.7% 보다 큰데, 분모가 17% 커진 상황에서는
# 오히려 작아져야(-17.7%) 정상이다. 패치의 성질인지 순서인지 구분이 안 된다.
#
# 이미 잰 6 라운드는 버리지 않는다. 순서를 뒤집은 6 라운드를 더 붙여
# 균형 설계로 만든다 (r1~6 = baseline 먼저, r7~12 = patched 먼저).
#
#   R5. patched 를 먼저 돌려도 patched 가 계속 빠르다 = 패치의 효과다.
#       1컬럼 웜/콜드 모두 r7~12 의 중앙값이 음수로 유지되고, 12쌍 전체의
#       부호검정이 유의하다.
#       깨지는 방향이 둘 다 의미가 있다:
#         - r7~12 에서 부호가 뒤집히면 -> F-019 의 콜드 wall-clock 은 순서 효과였다.
#           R1~R3 (샘플 기반) 은 그대로지만 wall-clock 문장은 전부 철회해야 한다.
#         - 20컬럼에서도 음수로 유지되면 -> 지금까지 '주장 불가' 였던 넓은 투영에서
#           처음으로 주장이 선다. 그건 좋은 소식이고 따로 설명해야 한다.
#
#   ⚠️ 정직하게: 이 예측을 적는 시점에 r1~6 의 결과는 전부 봤다 (F-019 에 적혀 있다).
#      R5 는 '뒤집었을 때도 같은가' 를 묻는 것이므로 r1~6 을 아는 것과 무관하다.
#
#   ⚠️ 이건 block flip 이다 (전반 6 / 후반 6). 순서와 측정 시각이 부분적으로 얽힌다 —
#      F-015 에서 이미 지적한 한계다. 라운드마다 교대하는 alternate 가 더 좋지만,
#      그러려면 이미 잰 6 라운드를 버려야 한다. 여기서는 기존 데이터를 살리는 쪽을 택했다.
# ---------------------------------------------------------------------------
#
# ⚠️ 다른 측정과 절대 동시에 돌리지 말 것 (같은 머신을 나눠 쓰면 양쪽이 오염된다).
set -euo pipefail
cd "$(dirname "$0")/.."

# config.env 가 REPS 를 3 으로 심는다. 호출자가 준 값을 source 전에 붙잡아 둔다.
# (06c-coltype.sh 에서 실제로 밟은 함정이다 — 헤더 첫 줄을 반드시 확인할 것.)
_REPS_CALLER="${REPS:-}"
source ./config.env
REPS="${_REPS_CALLER:-$REPS}"

# arm 순서 — r1~6 은 baseline 먼저 (이미 측정됨), r7 부터 뒤집는다.
#   block     : FLIP_FROM 부터 뒤집는다. 기존 6 라운드를 재현하려면 이게 기본이어야 한다.
#   alternate : 라운드마다 뒤집는다. 새 축을 처음부터 잴 때 쓸 것.
FLIP_MODE="${FLIP_MODE:-block}"
FLIP_FROM="${FLIP_FROM:-7}"

COLD_TABLE="${COLD_TABLE:-dv.g.d610}"
COLD_TAG="${COLD_TAG:-d610}"
COLD_COLS="${COLD_COLS:-1 20}"

[[ -w /proc/sys/vm/drop_caches ]] || { echo "drop_caches 쓰기 불가 — root 로 실행하세요" >&2; exit 1; }
mkdir -p "$RESULTS/profiles"

run_one() {   # $1=arm $2=rep $3=cols $4=mode(warm|cold)
  local T="${COLD_TAG}${4}c${3}"
  local PROF="${RESULTS}/profiles/${1}__${T}_c${3}_r${2}.collapsed"
  local JAR; case "$1" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;; esac
  local FLAG=""; [[ "$4" == "cold" ]] && FLAG="--cold"

  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then echo "      skip $1 r$2"; return; fi

  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$COLD_TABLE" --label "${1}_${T}_r${2}" \
      --cols "$3" $FLAG --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${1}__${T}_r${2}.json" \
    2>&1 | grep -E "median=" | sed "s/^/      $1 r$2 ($4): /" || true
}

START=$(date +%s)
echo "콜드/웜 × ${COLD_COLS} 컬럼 × 2 arm × ${REPS}회"
echo "FLIP_MODE=${FLIP_MODE}  FLIP_FROM=${FLIP_FROM}  SCAN_ITERS=${SCAN_ITERS}"
echo
for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  for C in $COLD_COLS; do
    case "$FLIP_MODE" in
      alternate) if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi ;;
      block)     if (( R >= FLIP_FROM )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi ;;
      *) echo "unknown FLIP_MODE=$FLIP_MODE (block|alternate)" >&2; exit 1 ;;
    esac
    for MODE in warm cold; do
      echo "  ${C}컬럼 ${MODE}  [순서: ${ARMS[*]}]"
      for A in "${ARMS[@]}"; do
        run_one "$A" "$R" "$C" "$MODE"
      done
    done
  done
  echo
done
echo "✅ 완료 — $(( $(date +%s) - START ))초"
