#!/usr/bin/env bash
# `p` 축 재설계 — F-009 의 3.53배가 'run 구조' 덕인가 '컨테이너 개수' 덕인가.
#
# 왜 이 축인가 (F-009 에 남은 교란):
#   F-009 는 L 을 키워 비용 3.53배를 봤는데, L 을 키우면 삭제가 소수 청크에 뭉쳐서
#   **컨테이너 개수도 같이 줄었다** (L=1 → 124개, L=4096 → 12개).
#   그래서 그 3.53배가 run 컨테이너의 **구조** 덕인지, 그냥 컨테이너가 **적어서**인지
#   구분이 안 된다. 처방("삭제 키로 정렬하라")의 근거가 여기 걸려 있다.
#
#   결정적 비교: p(점유 청크 비율)만 줄이면 컨테이너 개수는 F-009 의 L=4096 과 같은
#   12개가 되는데 타입은 여전히 **array** 다. 나란히 놓으면 개수 효과와 구조 효과가 갈린다.
#
# 왜 처음에 실패했나 (F-010):
#   삭제 술어가 `pmod(hash(chunk_idx, seed), 10000) < p` 인데 파일당 청크가 30개뿐이라
#   해상도가 3.3%p 단위였다. p 를 바꿔도 **같은 청크 집합**이 뽑혀 서로 다른 테이블이
#   안 만들어졌다. 그래서 통째로 날아갔다.
#   => ROWS_PER_FILE 을 2M -> 8M 으로 올려 파일당 청크를 122개로 만든다.
#      (8,000,000 / 65,536 = 122.07)
#
# ⚠️ config.env 의 ROWS_PER_FILE 은 원래 하드코딩(`ROWS_PER_FILE=2000000`)이라
#    환경변수를 조용히 먹었다 — 오버라이드 함정 변종 A. config 를 고쳤고,
#    여기서도 호출자 값을 source 앞에서 붙잡는다. 그리고 **산출물로 검증**한다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   P1. [설계 검증 · 먼저 통과해야 나머지가 의미 있다]
#       p 를 바꾸면 실제로 **서로 다른 테이블**이 나온다. 인접한 p 값끼리
#       삭제 행 수가 5% 이상 다르고, 컨테이너 개수가 단조 감소한다.
#       깨지면 재설계도 실패한 것이고 여기서 멈춘다 (F-010 을 반복하지 않는다).
#
#   P2. [판정용] **컨테이너 개수만 줄이면 비용이 별로 안 준다.**
#       p=100% → 10% 로 컨테이너를 122개 → 12개로 줄여도 DV 비용이 **1.5배 미만**만
#       싸진다. F-009 의 3.53배 대부분은 **run 구조** 덕이라는 뜻이 된다.
#       깨져서 3배 가까이 나오면 F-009 의 해석이 틀린 것이고,
#       처방의 근거를 "정렬하면 컨테이너가 준다" 로 다시 써야 한다.
#
#   P3. [구조 확인] p 를 줄여도 컨테이너 **타입은 array 로 유지**된다.
#       청크 안의 밀도(d=0.5%)를 고정했으므로 청크당 카디널리티가 안 변한다.
#       바뀌는 건 '삭제가 있는 청크가 몇 개냐' 뿐이다.
#
# ⚠️ 이 실험이 재지 '못하는' 것: 실제 테이블. p 축은 술어를 조작한 합성 축이다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
_ITERS_CALLER="${SCAN_ITERS:-}"
_RPF_CALLER="${ROWS_PER_FILE:-}"
source ./config.env
REPS="${_REPS_CALLER:-3}"
export SCAN_ITERS="${_ITERS_CALLER:-30}"
export SCAN_WARMUP="${SCAN_WARMUP:-3}"
ROWS_PER_FILE="${_RPF_CALLER:-8000000}"

PX_DENSITY_BP="${PX_DENSITY_BP:-50}"
PX_OCCUPANCIES="${PX_OCCUPANCIES:-10000 5000 2500 1000}"
PX_COLS="${PX_COLS:-1}"
CHUNK=65536

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

CHUNKS_PER_FILE=$(( ROWS_PER_FILE / CHUNK ))
echo "p 축 재설계  (d=${PX_DENSITY_BP}bp, p=${PX_OCCUPANCIES}, ROWS_PER_FILE=${ROWS_PER_FILE})"
echo "  파일당 청크 ${CHUNKS_PER_FILE}개  (F-010 이 실패한 조건은 30개였다)"
if (( CHUNKS_PER_FILE < 100 )); then
  echo "  *** 청크가 ${CHUNKS_PER_FILE}개뿐이다 — F-010 과 같은 해상도 문제가 재발한다." >&2
  echo "      ROWS_PER_FILE 이 안 먹었을 수 있다 (오버라이드 함정)." >&2
  exit 1
fi
echo

gen_one() {  # $1=p
  local TAG="px${PX_DENSITY_BP}p$1"
  local OUT="${RESULTS}/gen_${TAG}.json"
  if [[ -f "$OUT" && -d "${WAREHOUSE}/g/${TAG}" && "${FORCE:-0}" != "1" ]]; then
    echo "    skip gen ${TAG}"; return
  fi
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase1/spark/gen_grid.py \
      --warehouse "$WAREHOUSE" --table "dv.g.${TAG}" \
      --density-bp "$PX_DENSITY_BP" --occupancy-bp "$1" \
      --rows-per-file "$ROWS_PER_FILE" --num-files "$NUM_FILES" --seed "$SEED" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "$OUT" \
    2>&1 | grep -E "^===|삭제 |DV " || true
  [[ -s "$OUT" ]] || { echo "    *** gen 결과가 없다 (${TAG})" >&2; return 1; }
}

run_one() {  # $1=p $2=rep
  local TAG="px${PX_DENSITY_BP}p$1"
  local PROF="${RESULTS}/profiles/baseline__${TAG}_c${PX_COLS}_r${2}.collapsed"
  local SJ="${RESULTS}/scan_baseline__${TAG}_r${2}.json"
  [[ -s "$PROF" && -s "$SJ" && "${FORCE:-0}" != "1" ]] && { echo "      skip p$1 r${2}"; return; }
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$BASELINE_JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "dv.g.${TAG}" --label "px_${TAG}_r${2}" --cols "$PX_COLS" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "$SJ" \
    >/dev/null 2>&1 || true
  [[ -s "$PROF" ]] || { echo "      *** 프로파일이 비었다 (p$1 r${2})" >&2; return 1; }
  [[ -s "$SJ"   ]] || { echo "      *** 스캔 JSON 이 없다 (p$1 r${2})" >&2; return 1; }
  echo "      p$1 r${2}: ok"
}

echo "① 테이블 생성 (${ROWS_PER_FILE} 행/파일 x ${NUM_FILES}파일)"
for P in $PX_OCCUPANCIES; do gen_one "$P"; done

echo
echo "② 설계 검증 (P1) — p 를 바꾸면 진짜 다른 테이블이 나오는가"
export PYTHONIOENCODING=utf-8
for P in $PX_OCCUPANCIES; do
  TAG="px${PX_DENSITY_BP}p${P}"
  CSV="${RESULTS}/containers_${TAG}.csv"
  shopt -s nullglob
  PUFFINS=( "${WAREHOUSE}/g/${TAG}/data"/*.puffin )
  if [[ ${#PUFFINS[@]} -eq 0 ]]; then echo "    p${P}: puffin 없음"; continue; fi
  python3 ../tools/dv_inspect.py "${PUFFINS[@]}" --csv "$CSV" --limit 0 >/dev/null 2>&1 \
    || { echo "    p${P}: 파싱 실패"; continue; }
  python3 tools/container_summary.py "$CSV" "p${P}"
done

echo
echo "③ 측정 (baseline 만 — 이 축의 질문은 레이아웃이지 패치가 아니다)"
for R in $(seq 1 "$REPS"); do
  echo "  라운드 $R"
  for P in $PX_OCCUPANCIES; do run_one "$P" "$R" || true; done
done

echo
echo "채점: python3 tools/paxis.py results"
