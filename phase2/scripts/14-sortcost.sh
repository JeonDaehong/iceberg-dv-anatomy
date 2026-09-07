#!/usr/bin/env bash
# Phase 3-B · 정렬 처방의 **값** — 쓰기에 얼마를 더 내야 하는가.
#
# 왜 이 축인가:
#   F-023 과 F-026 이 "삭제 키로 정렬해 두면 DV 체크가 2.80배 싸진다" 는 처방을 내놨다.
#   그런데 **그 정렬이 얼마나 비싼지를 안 쟀다.** 실무자에게 "이렇게 하세요" 라고 내보내는
#   글에서 이건 반드시 나올 질문이고, 답이 없으면 처방을 쓸지 말지 판단할 수가 없다.
#   이득만 재고 비용을 안 재면 그건 처방이 아니라 광고다.
#
# 무엇을 재는가:
#   같은 데이터를 (a) 무정렬로 쓸 때와 (b) 삭제 키로 전역 정렬해 쓸 때의 **쓰기 벽시계**.
#   그리고 그 차이를 스캔 1회당 절감으로 나눠 **몇 번 읽어야 본전인가**를 낸다.
#
# 통제:
#   - 같은 시드, 같은 행 수, 같은 파일 수, 같은 컬럼. 달라지는 건 정렬 여부뿐이다.
#   - arm 순서를 라운드마다 **교대**한다 (JIT 워밍업과 페이지 캐시가 첫 arm 에 불리하다).
#   - 매 라운드 테이블을 지우고 새로 쓴다 (FORCE=1). 캐시된 결과를 재지 않는다.
#
# ⚠️ 이 측정이 재지 '못하는' 것:
#   소스 데이터 생성(spark.range + selectExpr) 비용이 두 arm 에 **똑같이** 포함된다.
#   실무 ETL 은 소스가 Kafka 든 다른 테이블이든 우리와 다르므로, **비율(x배)은 우리
#   환경의 값**이고 일반화되는 것은 **차이(초)** 와 **행당 추가 비용**이다. 둘 다 낸다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   W1. 정렬 쓰기가 더 비싸다. repartitionByRange 는 범위 경계를 정하려고 **소스를 한 번
#       샘플링**하고, 그다음 **전체 셔플**을 하고, 파티션 안에서 **정렬**한다. 세 가지가
#       다 추가된다. 차이는 무정렬 쓰기 대비 **+50% ~ +200%** 로 예측한다.
#       깨져서 차이가 거의 없으면 처방의 값이 공짜라는 뜻이고 글이 훨씬 쉬워진다.
#
#   W2. [판정용] **본전 회수 스캔 횟수가 10회 미만이다.**
#       F-023 에서 정렬 테이블의 스캔 wall 이 20.5% 빨랐다. 추가 쓰기 비용을 스캔 1회당
#       절감으로 나눈다. 10회 미만이면 "한 번 쓰고 여러 번 읽는" 분석 테이블에서 처방이
#       확실히 남는 장사다. 100회를 넘으면 처방에 "읽기가 아주 많을 때만" 이라는 조건을
#       하나 더 달아야 한다.
#
#   W3. [덤이자 통제] 정렬 테이블의 **데이터 파일이 더 작다.** 정렬 컬럼이 이웃끼리
#       비슷해지므로 Parquet 의 딕셔너리/RLE 인코딩이 잘 먹는다. 작아진다면 그건 DV 와
#       무관한 **추가 이득**이므로 본전 계산에 반영해야 한다 (읽을 바이트가 준다).
#       ⚠️ 반대로 크게 **커지면** 통제 실패를 의심해야 한다 — 같은 데이터인데 커질 이유가 없다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
source ./config.env
REPS="${_REPS_CALLER:-4}"

SC_DENSITY_BP="${SC_DENSITY_BP:-610}"
SC_SORT_COL="${SC_SORT_COL:-k04}"

UNSORTED="dv.g.sc_uns"
SORTED="dv.g.sc_srt"

mkdir -p "$RESULTS"

gen_one() {   # $1=table $2=sort컬럼(빈문자열이면 무정렬) $3=rep
  local TAB="$1" SORT="$2" REP="$3" TAG
  TAG=$(echo "$TAB" | sed 's/.*\.//')
  local OUT="${RESULTS}/sortcost_${TAG}_r${REP}.json"
  if [[ -f "$OUT" && "${FORCE:-0}" != "1" ]]; then
    echo "    skip ${TAG} r${REP} (결과 있음)"; return
  fi
  local EXTRA=(--delete-key "$SC_SORT_COL")
  [[ -n "$SORT" ]] && EXTRA+=(--sort-by "$SORT")
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase1/spark/gen_grid.py \
      --warehouse "$WAREHOUSE" --table "$TAB" \
      --density-bp "$SC_DENSITY_BP" "${EXTRA[@]}" \
      --rows-per-file "$ROWS_PER_FILE" --num-files "$NUM_FILES" --seed "$SEED" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "$OUT" \
    2>&1 | grep -E "^===|쓰기 |데이터 파일|정렬:|삭제 " || true

  # 게이트: 로그가 아니라 **산출물**을 본다. (같은 실수를 네 번 밟았다)
  if [[ ! -f "$OUT" ]]; then
    echo "    *** ${TAG} r${REP}: 결과 JSON 이 없다 — 쓰기가 실패했다" >&2; return 1
  fi
  python3 -c "
import json,sys
d=json.load(open(r'''$OUT''',encoding='utf-8'))
if not d.get('write_secs'): print('    *** write_secs 가 비었다'); sys.exit(1)
" || return 1
}

echo "Phase 3-B · 정렬 쓰기 비용  (d=${SC_DENSITY_BP}bp, 정렬 컬럼 ${SC_SORT_COL}, REPS=${REPS})"
echo "  같은 데이터를 무정렬/정렬로 각각 쓰고 벽시계를 잰다. arm 순서는 라운드마다 교대."
echo

for r in $(seq 1 "$REPS"); do
  echo "── 라운드 $r"
  if (( r % 2 == 1 )); then ORDER=("uns" "srt"); else ORDER=("srt" "uns"); fi
  echo "   순서: ${ORDER[*]}"
  for a in "${ORDER[@]}"; do
    # gen_grid.py 가 매번 DROP TABLE ... PURGE 후 CTAS 하므로 테이블은 항상 새로 쓴다.
    # 여기의 skip 은 '이미 끝난 라운드를 다시 재지 않는다' 는 뜻이지 캐시 재사용이 아니다.
    if [[ "$a" == "srt" ]]; then gen_one "$SORTED" "$SC_SORT_COL" "$r"; else gen_one "$UNSORTED" "" "$r"; fi
  done
done

echo
echo "채점: python3 tools/sortcost.py results"
