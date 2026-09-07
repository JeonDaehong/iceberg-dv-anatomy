#!/usr/bin/env bash
# 정렬 키 카디널리티 축 — "삭제 키로 정렬하라" 가 언제 안 먹는가.
#
# 왜 이 축인가:
#   F-023 이 정렬 처방을 세웠지만(DV 체크 2.80배) 정렬 키가 `k04` 하나였다.
#   `k04` 는 고유값이 1,000개뿐이라 8백만 행을 정렬하면 값 하나가 **8,000행**을 덮는다.
#   삭제 술어가 값의 6.1% 를 고르므로 삭제된 행은 **8,000행짜리 덩어리 61개**가 된다 —
#   run 컨테이너가 나오기에 이상적인 조건이다.
#
#   고유값이 많은 키로 정렬하면 이야기가 달라져야 한다. 고유값이 행 수에 가까우면
#   값 하나가 1행이고, 그 값들의 6.1% 를 골라도 **덩어리가 안 생긴다** — 정렬해도
#   삭제 위치는 무정렬과 다를 바 없다.
#
#   즉 처방에는 **조건이 붙는다.** 그 조건을 안 적으면 "정렬하면 2.8배" 가
#   자기 테이블에서 안 나오는 사람이 생긴다.
#
# 축 (모두 같은 술어, 같은 삭제 행 수 아님 — 아래 주의 참조):
#   uns   무정렬 (기준)
#   c1k   k04 로 정렬   고유값 ~1,000       → 값당 ~8,000행
#   c1m   k15 로 정렬   고유값 ~999,983     → 값당 ~8행
#   c8m   k01 로 정렬   고유값 ~10억(사실상 유일) → 값당 1행
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   S_C1. [판정용] DV 체크 CPU 감소가 **고유값 수에 따라 사라진다.**
#         k04 정렬 ≈ 2.8배(F-023 재현), k15 정렬은 1.5배 미만, k01 정렬은 1.1배 미만.
#         깨져서 고유값이 많아도 이득이 유지되면, 처방이 내 생각보다 넓게 적용된다는
#         뜻이고 좋은 소식이다. 그러면 기전을 다시 설명해야 한다.
#
#   S_C2. DV 파일 크기가 같은 순서로 커진다. k04 정렬은 2,533B 였다.
#         k01 정렬은 무정렬(863,982B)과 비슷할 것이다.
#
#   S_C3. 컨테이너 타입이 run → array 로 되돌아간다 (dv_inspect 로 확인).
#
#   ⚠️ 주의: 술어를 각 정렬 키에 걸므로 **삭제되는 행 자체가 축마다 다르다.**
#      F-023 은 두 테이블에 같은 술어를 걸어 같은 행을 지웠지만, 여기서는 "그 키로
#      정렬했을 때" 를 보려면 술어도 그 키에 걸어야 한다. 그래서 이 축의 비교는
#      **삭제 행 수가 비슷한지 확인한 뒤**에만 유효하다. 스크립트가 찍어준다.
#      크게 어긋나면 그 셀은 해석하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
_ITERS_CALLER="${SCAN_ITERS:-}"
source ./config.env
REPS="${_REPS_CALLER:-6}"
export SCAN_ITERS="${_ITERS_CALLER:-$SCAN_ITERS}"

SK_DENSITY_BP="${SK_DENSITY_BP:-610}"
# 이름:정렬컬럼(빈문자열=무정렬)
SK_SETS="${SK_SETS:-uns: c1k:k04 c1m:k15 c8m:k01}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }

# ---------------------------------------------------------------- 테이블
echo "① 테이블 생성 (정렬 비용도 같이 잰다)"
for S in $SK_SETS; do
  NAME="${S%%:*}"; SORT="${S#*:}"
  TAB="dv.g.sk_${NAME}"
  if [[ -f "${RESULTS}/gen_sk_${NAME}.json" && -d "${WAREHOUSE}/g/sk_${NAME}" && "${FORCE:-0}" != "1" ]]; then
    echo "  skip sk_${NAME}"; continue
  fi
  # 술어는 그 축의 정렬 키에 건다 (무정렬은 k04 로 — F-023 무정렬과 같은 조건).
  DK="${SORT:-k04}"
  EXTRA=(--delete-key "$DK")
  [[ -n "$SORT" ]] && EXTRA+=(--sort-by "$SORT")
  T0=$(date +%s)
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase1/spark/gen_grid.py \
      --warehouse "$WAREHOUSE" --table "$TAB" \
      --density-bp "$SK_DENSITY_BP" "${EXTRA[@]}" \
      --rows-per-file "$ROWS_PER_FILE" --num-files "$NUM_FILES" --seed "$SEED" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/gen_sk_${NAME}.json" \
    2>&1 | grep -E "^===|predicate|정렬|삭제 |DV " || true
  echo "  ⏱ sk_${NAME} 생성 $(( $(date +%s) - T0 ))초  (정렬 비용 포함)"
done
echo

python3 - "$RESULTS" <<'PY'
import glob, json, os, sys
r = sys.argv[1]
print("  %-6s %12s %12s %14s %12s" % ("축", "삭제 행", "실제 밀도", "DV 바이트", "삭제행당 bit"))
print("  " + "-" * 62)
base = None
for name in ("uns", "c1k", "c1m", "c8m"):
    p = os.path.join(r, "gen_sk_%s.json" % name)
    if not os.path.exists(p):
        continue
    d = json.load(open(p, encoding="utf-8"))
    if base is None:
        base = d["deleted_rows"]
    print("  %-6s %12s %11.3f%% %14s %11.4f"
          % (name, f"{d['deleted_rows']:,}", d["actual_density"] * 100,
             f"{d['dv_bytes']:,}", d["dv_bytes"] * 8 / max(d["deleted_rows"], 1)))
print("\n  ⚠️ 삭제 행 수가 축마다 다르면 DV 샘플을 직접 비교하면 안 된다.")
PY
echo

# ---------------------------------------------------------------- 측정
run_one() {   # $1=arm $2=rep $3=name
  local TAB="dv.g.sk_$3"
  local TAG="sk$3"
  local PROF="${RESULTS}/profiles/${1}__${TAG}_c1_r${2}.collapsed"
  local JAR; case "$1" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;; esac
  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then echo "      skip $1 r$2"; return; fi
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$TAB" --label "${1}_${TAG}_r${2}" \
      --cols 1 --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${1}__${TAG}_r${2}.json" \
    2>&1 | grep -E "median=" | sed "s|^|      $1 r$2 ($3)\: |" || true
}

NAMES=(); for S in $SK_SETS; do NAMES+=("${S%%:*}"); done
echo "② 측정 — 축 ${#NAMES[@]}개 × 2 arm × ${REPS}회 = $(( ${#NAMES[@]} * 2 * REPS )) 프로파일"
START=$(date +%s); N=0
for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for NAME in "${NAMES[@]}"; do
    echo "  ${NAME}  [순서: ${ARMS[*]}]"
    for A in "${ARMS[@]}"; do
      run_one "$A" "$R" "$NAME"; N=$((N+1))
    done
  done
  echo
done
echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
