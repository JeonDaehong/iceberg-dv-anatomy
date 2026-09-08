#!/usr/bin/env bash
# 반등 구간에서 패치가 어떻게 되는가 — 헤드라인 주장에 남은 구멍.
#
# 왜 이 축인가:
#   패치 전후를 잰 밀도는 d=0.5% / 6.1% / 7.0% 세 점뿐이다(F-011, F-012, F-015).
#   전부 경계 근처이고, **반등 구간(d=8~50%)에는 patched 측정이 하나도 없다.**
#   그런데 F-037 이 방금 반등의 원인을 `if (!contains(pos))` 라는 행마다의
#   예측 불가능한 분기로 지목했다. 패치는 정확히 그 분기를 없애는 변경이다
#   (삭제 position 만 순회하고 그 사이를 무조건 루프로 채운다 —
#    upstream/ColumnarBatchUtil.patched.java 의 RowIdMappingBuilder).
#   그러면 "삭제가 많은 테이블에서 패치가 어떻게 되나" 에 답이 있어야 한다.
#
# 무엇을 통제하는가:
#   두 테이블 모두 bitmap 컨테이너다 (d=8% -> 청크 카디널리티 5,243,
#   d=50% -> 32,768, 경계 4,096). 컨테이너 타입 축은 죽어 있고 삭제 비율만 다르다.
#   같은 jar 두 개, 같은 스캔, 라운드마다 arm 순서 교대.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   T1. [설계 검증] baseline 의 반등이 이 조건에서도 재현된다 —
#       DV 샘플이 d=8% -> d=50% 에서 늘고 범위가 안 겹친다.
#       F-008 은 밀도 스윕에서, F-032 는 cycles 로 봤다. 여기서 한 번 더 확인한다.
#       깨지면 아래 둘은 판정할 대상이 없다.
#
#   T2. [판정용] **패치는 반등을 없앤다** — patched 의 DV 샘플이 두 밀도에서
#       노이즈 바닥(9.7%) 안으로 같다.
#       근거: F-037 이 지목한 원인(행마다의 데이터 의존 분기)이 패치 경로에는
#       아예 없다. 채움 루프는 카운티드 루프라 분기가 예측 가능하다.
#       깨지면 — patched 도 반등하면 — 원인이 분기 말고 더 있다는 뜻이고
#       F-037 의 결론을 좁혀야 한다.
#
#   T3. 패치의 개선 **배율**은 d=50% 에서 d=8% 보다 **작아진다.**
#       근거: 패치의 일량은 삭제 행 수에 비례한다. 배치 5,000행에서 d=8% 면
#       콜백 400회인데 d=50% 면 2,500회이고, 콜백 사이의 채움 루프는 길이 1 이다.
#       벌크의 이점이 사라진다.
#       T2 와 T3 이 같이 서면 처방은 "패치는 반등을 없애지만, 삭제가 아주 많으면
#       이득 자체가 작다" 가 된다.
#       T3 이 깨지면(배율이 오히려 커지면) 반등으로 baseline 이 비싸진 만큼을
#       패치가 전부 회수한 것이다 — 그쪽이 더 좋은 소식이다.
#
#   ⚠️ 재지 못하는 것: 1컬럼 좁은 투영이다. 넓은 투영에서의 wall-clock 은 F-015 를 볼 것.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
_ITERS_CALLER="${SCAN_ITERS:-}"
source ./config.env
REPS="${_REPS_CALLER:-4}"
export SCAN_ITERS="${_ITERS_CALLER:-30}"
export SCAN_WARMUP="${SCAN_WARMUP:-3}"

RP_COLS="${RP_COLS:-1}"
# tag:table — 둘 다 bitmap 이어야 한다.
RP_CASES="${RP_CASES:-rp800:dv.g.d800 rp5000:dv.g.d5000}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음" >&2; exit 1; }

for C in $RP_CASES; do
  T="${C##*:}"; D="${WAREHOUSE}/g/${T#dv.g.}"
  [[ -d "$D" ]] || { echo "테이블이 없다: $D (phase1/scripts/01-gen-grid.sh 로 만든다)" >&2; exit 1; }
done

run_one() {  # $1=arm $2=tag $3=table $4=rep
  local PROF="${RESULTS}/profiles/${1}__${2}_c${RP_COLS}_r${4}.collapsed"
  local SJ="${RESULTS}/scan_${1}__${2}_r${4}.json"
  local JAR; case "$1" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;;
    *) echo "unknown arm $1" >&2; return 1 ;; esac
  [[ -s "$PROF" && -s "$SJ" && "${FORCE:-0}" != "1" ]] && { echo "      skip $1 $2 r$4"; return; }
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$3" --label "${1}_${2}_r${4}" --cols "$RP_COLS" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "$SJ" \
    >/dev/null 2>&1 || true
  [[ -s "$PROF" ]] || { echo "      *** 프로파일이 비었다 ($1 $2 r$4)" >&2; return 1; }
  [[ -s "$SJ"   ]] || { echo "      *** 스캔 JSON 이 없다 ($1 $2 r$4)" >&2; return 1; }
  echo "      $1 $2 r$4: ok"
}

echo "반등 구간의 패치  (${RP_COLS}컬럼, iters=${SCAN_ITERS}, REPS=${REPS})"
for C in $RP_CASES; do echo "  ${C%%:*} <- ${C##*:}"; done
echo

START=$(date +%s); N=0
for R in $(seq 1 "$REPS"); do
  echo "  라운드 $R / $REPS"
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for C in $RP_CASES; do
    for A in "${ARMS[@]}"; do run_one "$A" "${C%%:*}" "${C##*:}" "$R" || true; N=$((N+1)); done
  done
done

echo
echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
echo "채점: python3 tools/reboundpatch.py results"
