#!/usr/bin/env bash
# 경계 **위**에서도 클러스터링이 먹히는가 — F-009 의 빈칸.
#
# 왜 이 축인가:
#   F-009 는 "삭제 키로 정렬하라" 처방의 근거를 만들었다. 밀도를 d=0.5% 로 고정하고
#   평균 run 길이 L 만 스윕해 비용 3.53배, 저장 1,115배를 봤다. 그런데 그건
#   **컨테이너 경계 아래 한 점**이다 (d=0.5% 는 array 영역).
#
#   경계 위(bitmap 영역)에서는 이야기가 다를 수 있다. 출발점이 이미 싸기 때문이다 —
#   F-001 이 array 16.0ns / bitmap 4.0ns 를 쟀다. 이미 4배 싼 데서 시작하면
#   클러스터링으로 더 짜낼 여지가 그만큼 적다.
#
#   이건 처방의 적용 범위 문제다. "삭제가 많은 테이블(d>6.25%)에도 정렬이 유효한가" 는
#   실무자가 당연히 물을 질문이고, 우리는 지금 답이 없다.
#
# 통제:
#   밀도를 d=12%(1200bp) 로 **고정**하고 L 만 바꾼다. 삭제 행 수가 거의 같고
#   contains() 호출 횟수도 800만으로 동일하다. 남는 차이는 컨테이너 구조뿐이다.
#   (F-009 와 완전히 같은 설계를, 경계 반대편에서 반복하는 것이다.)
#   arm 순서는 라운드마다 교대한다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   B1. [구조] L=1 이면 bitmap 이고, L 이 커지면 run 으로 넘어간다.
#       넘어가는 지점은 대략 nRuns x 4바이트 < 8KB, 즉 **nRuns < 2048** 근처다.
#       d=12% 면 청크 카디널리티가 65536 x 0.12 = 7,864 이므로
#       L=1 의 nRuns 는 약 6,900 (bitmap 이 이긴다), L=8 이면 약 980 (run 이 이긴다).
#       => 전환은 L=4 와 L=8 사이에서 일어난다.
#
#   B2. [판정용] **경계 위에서의 이득이 F-009 의 3.53배보다 작다. 2배 미만.**
#       출발점이 이미 bitmap(싼 쪽)이기 때문이다.
#       깨져서 3.53배 이상 나오면 처방은 경계 위에서 **더** 강력한 것이고,
#       공개 글의 처방 문구에서 밀도 조건을 뺄 수 있다.
#
#   B3. [저장] 저장 이득은 경계 위에서 **더 크다.** bitmap 은 밀도와 무관하게
#       청크당 8KB 고정인데 run 은 수십 바이트까지 내려가므로, F-009 의 1,115배보다
#       큰 배율이 나온다.
#
# ⚠️ 이 실험이 재지 '못하는' 것: 실제 테이블을 정렬했을 때. 여기서는 F-009 처럼
#    **술어를 조작해** 합성 클러스터링을 만든다. 진짜 정렬은 F-023/F-026 이 쟀다.
set -euo pipefail
cd "$(dirname "$0")/.."

# ⚠️ config.env 가 값을 채운 뒤라 source 뒤의 `${VAR:-기본값}` 은 죽은 코드다 (일곱 번 밟았다).
_REPS_CALLER="${REPS:-}"
_ITERS_CALLER="${SCAN_ITERS:-}"
source ./config.env
REPS="${_REPS_CALLER:-3}"
export SCAN_ITERS="${_ITERS_CALLER:-30}"
export SCAN_WARMUP="${SCAN_WARMUP:-3}"

HC_DENSITY_BP="${HC_DENSITY_BP:-1200}"
HC_LENGTHS="${HC_LENGTHS:-1 8 64 512 4096}"
HC_COLS="${HC_COLS:-1}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

jar_of() { case "$1" in baseline) echo "$BASELINE_JAR";; patched) echo "$PATCHED_JAR";; esac; }

gen_one() {  # $1=L
  local TAG="d${HC_DENSITY_BP}L$1"
  local OUT="${RESULTS}/gen_${TAG}.json"
  # 게이트: 영수증이 아니라 **테이블 자체**가 있어야 skip 한다 (공개 저장소 재현 버그였다).
  if [[ -f "$OUT" && -d "${WAREHOUSE}/g/${TAG}" && "${FORCE:-0}" != "1" ]]; then
    echo "    skip gen ${TAG}"; return
  fi
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase1/spark/gen_grid.py \
      --warehouse "$WAREHOUSE" --table "dv.g.${TAG}" \
      --density-bp "$HC_DENSITY_BP" --run-length "$1" \
      --rows-per-file "$ROWS_PER_FILE" --num-files "$NUM_FILES" --seed "$SEED" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "$OUT" \
    2>&1 | grep -E "^===|삭제 |DV " || true
  [[ -s "$OUT" ]] || { echo "    *** gen 결과가 없다 (${TAG})" >&2; return 1; }
}

run_one() {  # $1=arm $2=L $3=rep
  local TAG="d${HC_DENSITY_BP}L$2"
  local PROF="${RESULTS}/profiles/${1}__hc${TAG}_c${HC_COLS}_r${3}.collapsed"
  local SJ="${RESULTS}/scan_${1}__hc${TAG}_r${3}.json"
  [[ -s "$PROF" && -s "$SJ" && "${FORCE:-0}" != "1" ]] && { echo "      skip ${1} L$2 r${3}"; return; }
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$(jar_of "$1")" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "dv.g.${TAG}" --label "hc_${1}_${TAG}_r${3}" --cols "$HC_COLS" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "$SJ" \
    >/dev/null 2>&1 || true
  [[ -s "$PROF" ]] || { echo "      *** 프로파일이 비었다 (${1} L$2 r${3})" >&2; return 1; }
  [[ -s "$SJ"   ]] || { echo "      *** 스캔 JSON 이 없다 (${1} L$2 r${3})" >&2; return 1; }
  echo "      ${1} L$2 r${3}: ok"
}

echo "경계 위 클러스터링  (d=${HC_DENSITY_BP}bp, L=${HC_LENGTHS}, iters=${SCAN_ITERS}, REPS=${REPS})"
echo "  F-009 와 같은 설계를 컨테이너 경계 **반대편**에서 반복한다."
echo
echo "① 테이블 생성"
for L in $HC_LENGTHS; do gen_one "$L"; done

echo
echo "② 컨테이너 구조 확인 (B1)"
# dv_inspect.py 는 puffin 파일들을 인자로 받아 컨테이너를 CSV 로 덤프한다.
export PYTHONIOENCODING=utf-8
for L in $HC_LENGTHS; do
  TAG="d${HC_DENSITY_BP}L${L}"
  CSV="${RESULTS}/containers_hc_${TAG}.csv"
  shopt -s nullglob
  PUFFINS=( "${WAREHOUSE}/g/${TAG}/data"/*.puffin )
  if [[ ${#PUFFINS[@]} -eq 0 ]]; then echo "    L${L}: puffin 없음"; continue; fi
  python3 ../tools/dv_inspect.py "${PUFFINS[@]}" --csv "$CSV" --limit 0 >/dev/null 2>&1 \
    || { echo "    L${L}: 파싱 실패"; continue; }
  python3 tools/container_summary.py "$CSV" "L${L}"
done

echo
echo "③ 측정"
for R in $(seq 1 "$REPS"); do
  echo "  라운드 $R"
  if (( R % 2 == 1 )); then ARMS=(baseline patched); else ARMS=(patched baseline); fi
  for L in $HC_LENGTHS; do
    for A in "${ARMS[@]}"; do run_one "$A" "$L" "$R" || true; done
  done
done

echo
echo "채점: python3 tools/hicluster.py results"
