#!/usr/bin/env bash
# Phase 3-A · 레이아웃 처방 — "삭제 키로 정렬하라" 가 실제 테이블에서도 성립하는가.
#
# 왜 이 축인가:
#   지금까지의 결론은 전부 "이 코드가 비싸다 / 고치면 이만큼 준다" 다. 그런데 패치가
#   업스트림에 들어가 릴리스로 풀리기 전까지 **사용자가 오늘 할 수 있는 일**은 없다.
#   글이 "버그가 있으니 기다리세요" 로 끝나면 실무자에게 쓸모가 절반이다.
#
#   F-009 가 근거를 이미 줬다 — 삭제율을 d=0.5% 로 고정하고 평균 run 길이 L 만 바꿨더니
#   비용 3.53배, 저장 1,115배 차이가 났다. 그런데 그건 **술어를 조작해서** 만든 합성
#   클러스터링이다. "테이블을 삭제 키로 정렬해 두면" 실제로 그 효과가 나오는지는 안 쟀다.
#
# 이 실험이 반드시 분리해야 하는 것 — 파일 스킵:
#   정렬하면 파일 min/max 통계가 좁아져 **술어가 있는 쿼리는 파일을 통째로 건너뛴다.**
#   그건 DV 와 아무 상관 없는 일반론이고, 섞이면 "정렬이 DV 를 싸게 한다" 를 주장할 수 없다.
#   => 그래서 스캔 쿼리에 **술어를 걸지 않는다.** 두 테이블 모두 전 파일을 읽는다.
#      파일 스킵이 원천적으로 불가능한 조건에서 비교한다.
#
# 무엇을 통제하는가:
#   - 삭제 술어가 두 테이블에서 **동일**하다 (정렬 컬럼 k04 에 건다).
#     같은 데이터에 같은 술어이므로 **삭제되는 행 자체가 같다.**
#   - 총 행 수, 파일 수, 컬럼, 시드 전부 같다.
#   - 달라지는 것은 그 행들이 파일 안 **어느 position 에 놓이는가** 뿐이다.
#   - 두 arm(baseline/patched)을 다 재서, 패치와 처방이 겹치는지도 본다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   L1. [판정용] baseline 에서 정렬 테이블의 DV 체크 CPU 가 무정렬 대비 **2배 이상 낮다.**
#       F-009 가 합성 클러스터링으로 3.53배를 봤으니, 진짜 정렬에서도 같은 방향이어야 한다.
#       깨지면 F-009 의 처방은 **합성 축에서만 성립하는 것**이고, 공개 글에서 레이아웃
#       처방을 빼야 한다.
#
#   L2. 컨테이너 타입이 갈린다. 무정렬은 array 위주, 정렬은 run 위주이거나
#       **DV 가 아예 없는 파일**이 다수 생긴다. dv_inspect 로 파일별로 확인한다.
#
#   L3. [통제 확인] 두 테이블의 **non-DV 스캔 샘플(= Parquet 디코딩)이 같다.**
#       읽는 행 수와 컬럼이 같으므로 디코딩 비용도 같아야 한다.
#       다르면 통제가 실패한 것이고 L1 을 해석하면 안 된다.
#
#   L4. [글에 제일 중요] **패치를 적용하면 정렬의 이득이 줄어든다.**
#       패치가 이미 DV 체크를 싸게 만들었으므로 두 처방이 겹치기 때문이다.
#       즉 baseline 에서의 정렬 이득 > patched 에서의 정렬 이득.
#       깨져서 patched 에서도 이득이 그대로면, 두 처방이 **독립**이라는 뜻이고
#       그건 더 좋은 소식이다 (둘 다 하면 둘 다 먹는다).
#
#   ⚠️ 이 실험이 재지 '못하는' 것: 파일 스킵의 이득 그 자체. 일부러 뺐다.
#      실무에서는 정렬이 파일 스킵으로 더 큰 이득을 주지만, 그건 DV 이야기가 아니다.
#      "정렬하면 좋다" 의 전체 크기가 아니라 **그중 DV 몫**만 재는 실험이다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
source ./config.env
REPS="${_REPS_CALLER:-6}"

LAY_DENSITY_BP="${LAY_DENSITY_BP:-610}"
LAY_SORT_COL="${LAY_SORT_COL:-k04}"
LAY_COLS="${LAY_COLS:-1}"
export SCAN_ITERS="${SCAN_ITERS:-30}"
export SCAN_WARMUP="${SCAN_WARMUP:-3}"

UNSORTED="dv.g.lay_uns"
SORTED="dv.g.lay_srt"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

# ---------------------------------------------------------------- 테이블
gen_one() {   # $1=table $2=sort컬럼(빈문자열이면 무정렬)
  local TAB="$1" SORT="$2" TAG
  TAG=$(echo "$TAB" | sed 's/.*\.//')
  if [[ -f "${RESULTS}/gen_${TAG}.json" && -d "${WAREHOUSE}/g/${TAG}" && "${FORCE:-0}" != "1" ]]; then
    echo "  skip ${TAB} (테이블·결과 모두 있음)"; return
  fi
  # 삭제 술어는 두 테이블에 **동일**하게 건다. 정렬 여부와 독립이어야
  # 삭제되는 행이 같아진다 (안 그러면 삭제 행 수가 어긋난다 — 실제로 한 번 어긋났다).
  local EXTRA=(--delete-key "$LAY_SORT_COL")
  [[ -n "$SORT" ]] && EXTRA+=(--sort-by "$SORT")
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase1/spark/gen_grid.py \
      --warehouse "$WAREHOUSE" --table "$TAB" \
      --density-bp "$LAY_DENSITY_BP" "${EXTRA[@]}" \
      --rows-per-file "$ROWS_PER_FILE" --num-files "$NUM_FILES" --seed "$SEED" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/gen_${TAG}.json" \
    2>&1 | grep -E "^===|predicate|정렬|삭제 |DV |파일별" || true
}

echo "Phase 3-A · 레이아웃 처방  (d=${LAY_DENSITY_BP}bp, 정렬 컬럼 ${LAY_SORT_COL})"
echo "  술어는 두 테이블에 동일 — 삭제되는 행이 같고 물리 배치만 다르다."
echo "  스캔 쿼리에 술어를 안 건다 — 파일 스킵을 원천 차단한다."
echo
echo "① 테이블 생성"
gen_one "$UNSORTED" ""
gen_one "$SORTED"   "$LAY_SORT_COL"

# 삭제 행 수가 같아야 비교가 성립한다. 다르면 통제 실패다.
python3 - "$RESULTS" <<'PY'
import json, sys, os
r = sys.argv[1]
try:
    u = json.load(open(os.path.join(r, "gen_lay_uns.json"), encoding="utf-8"))
    s = json.load(open(os.path.join(r, "gen_lay_srt.json"), encoding="utf-8"))
except Exception as e:
    print("  ⚠️ gen json 을 못 읽었다: %s" % e); sys.exit(0)
print("  무정렬: 행 %s, 삭제 %s (%.3f%%), DV %d개 %s B"
      % (f"{u['total_rows']:,}", f"{u['deleted_rows']:,}", u['actual_density']*100,
         u['dv_count'], f"{u['dv_bytes']:,}"))
print("  정렬  : 행 %s, 삭제 %s (%.3f%%), DV %d개 %s B"
      % (f"{s['total_rows']:,}", f"{s['deleted_rows']:,}", s['actual_density']*100,
         s['dv_count'], f"{s['dv_bytes']:,}"))
d = abs(u['deleted_rows'] - s['deleted_rows'])
rel = d / max(u['deleted_rows'], 1) * 100
print("  삭제 행 수 차이: %s (%.2f%%)  %s"
      % (f"{d:,}", rel, "✅ 같다고 볼 수 있음" if rel < 1.0 else "⚠️ 통제 실패 — L1 해석 불가"))
PY
echo

# ---------------------------------------------------------------- 측정
run_one() {   # $1=arm $2=rep $3=layout(uns|srt) $4=cols
  local TAB TAG
  if [[ "$3" == "srt" ]]; then TAB="$SORTED"; else TAB="$UNSORTED"; fi
  TAG="lay${3}c${4}"
  local PROF="${RESULTS}/profiles/${1}__${TAG}_c${4}_r${2}.collapsed"
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
      --warehouse "$WAREHOUSE" --table "$TAB" --label "${1}_${TAG}_r${2}" \
      --cols "$4" --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${1}__${TAG}_r${2}.json" \
    2>&1 | grep -E "median=" | sed "s|^|      $1 r$2 ($3)\: |" || true
}

echo "② 측정 — 레이아웃 2 × arm 2 × 폭 ${LAY_COLS} × ${REPS}회"
START=$(date +%s); N=0
for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for C in $LAY_COLS; do
    for LAYOUT in uns srt; do
      echo "  ${LAYOUT} ${C}컬럼  [순서: ${ARMS[*]}]"
      for A in "${ARMS[@]}"; do
        run_one "$A" "$R" "$LAYOUT" "$C"; N=$((N+1))
      done
    done
  done
  echo
done
echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
