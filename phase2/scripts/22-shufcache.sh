#!/usr/bin/env bash
# 셔플이 붙으면 왜 스캔 서브트리가 커지는가 — F-024 가 추론으로 남긴 마지막 기전.
#
# 무엇이 남았나:
#   F-024 는 집계가 붙으면 DV 의 스캔 내 비중이 떨어지는 것을 보고, 분자(DV)는 고정인데
#   **분모(스캔 서브트리)가 커졌다**는 데까지 갔다. 그리고 이렇게 적었다 —
#
#     "SCAN_ROOTS 는 Iceberg/Parquet 리더 패키지이므로 집계 코드가 여기 섞여 들어올 수는
#      없다. 남는 설명은 **집계 해시 테이블이 메모리·캐시를 압박해 리더 코드 자체가
#      느려진다**는 것이다. **다만 확인하지 않았다.**"
#
#   F-042 가 전용 인스턴스에서 다시 재서 Q_S1(비중 불변)을 살렸지만,
#   **스캔 서브트리가 커지는 현상 자체는 그대로다** — 로컬 +14%, m7i +19%.
#   즉 비율은 유지되는데 절대량은 둘 다 늘었고, 그 이유는 여전히 추론이다.
#
# 왜 이제 잴 수 있나:
#   F-028 이후 WSL2 vPMU 를 쓴다. async-profiler 의 event 를 바꾸면 **메서드별로**
#   cycles / instructions / cache-misses 를 귀속시킬 수 있다.
#   `perf stat` 총량으로는 못 하는 일이다 — 스캔 서브트리만 떼어내야 하기 때문.
#
# 무엇을 재는가:
#   같은 테이블에서 q0(스캔만)와 q2(GROUP BY k01, 고유값 ~800만)를 baseline arm 으로만
#   돌리고, 이벤트를 바꿔가며 **스캔 서브트리에 귀속된 샘플**을 비교한다.
#   패치는 이 질문과 무관하므로 arm 축을 뺐다 — 축이 하나여야 판정이 깨끗하다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   C1. [설계 검증] 세 이벤트(cycles, instructions, cache-misses)가 전부 비지 않은
#       프로파일을 낸다. cache-misses 가 안 잡히면 이 축은 못 한다 — 거기서 멈춘다.
#
#   C2. [★판정용★] **스캔 서브트리의 명령어 수는 q0 과 q2 에서 거의 같고
#       (±10% 안), cycles 만 늘어난다.**
#       근거: 리더가 하는 일(같은 행, 같은 컬럼, 같은 디코딩)은 안 변했다.
#       늘어난 것이 stall 이라면 명령어는 그대로고 사이클만 는다.
#       => 그러면 "집계가 리더를 느리게 만든다" 가 **일이 늘어서가 아니라 막혀서**로
#          좁혀진다.
#       깨져서 명령어도 같이 늘면, 리더가 실제로 더 많은 일을 하는 것이고
#       (코드젠 경로 차이·배치 복사 등) 캐시 가설이 아니라 **다른 설명**이 필요하다.
#
#   C3. [기전 지목] **스캔 서브트리의 cache-miss 샘플이 q2 에서 더 많다** —
#       q0 대비 1.3배 이상. 집계 해시 테이블(고유값 800만이면 수백 MB)이
#       L2/L3 를 밀어내기 때문.
#       C2 와 C3 이 같이 서면 F-024 의 추론이 확인된 것이다.
#       C2 는 서는데 C3 이 깨지면 stall 의 원인이 캐시가 아니라 다른 것
#       (메모리 대역, TLB, GC) 이고, 그건 또 다른 축이다.
#
#   ⚠️ 이 실험이 재지 못하는 것: 원인의 방향. 집계가 리더를 압박하는 것과
#      리더가 집계를 압박하는 것을 이 설계로는 못 가른다. 같이 도는 하나의 잡이다.
#      "스캔 서브트리가 stall 로 느려진다" 까지만 말할 수 있다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
source ./config.env
REPS="${_REPS_CALLER:-4}"

SC_TABLE="${SC_TABLE:-dv.g.d610}"
SC_TAG="${SC_TAG:-d610}"
SC_COLS="${SC_COLS:-k01,k04}"
SC_EVENTS="${SC_EVENTS:-cycles instructions cache-misses}"
PERF_DIR="${PERF_DIR:-/usr/lib/linux-tools-6.8.0-139}"
export PATH="${PERF_DIR}:${PATH}"
export SCAN_ITERS="${SCAN_ITERS:-10}"
export SCAN_WARMUP="${SCAN_WARMUP:-2}"

NCOL=$(echo "$SC_COLS" | tr ',' '\n' | wc -l)
mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }
sysctl -w kernel.perf_event_paranoid=-1 >/dev/null 2>&1 || true

echo "셔플 캐시 축  (테이블 ${SC_TABLE}, 컬럼 ${SC_COLS}, iters=${SCAN_ITERS}, REPS=${REPS})"
echo "  이벤트: ${SC_EVENTS}"
echo

# ── C1: 이벤트 지원을 본 측정 전에 확인한다 (15-qperf.sh 의 교훈) ──────────
echo "① 이벤트 지원 확인"
for EV in $SC_EVENTS; do
  SAFE=$(echo "$EV" | tr -c 'a-zA-Z0-9' '_')
  T="${RESULTS}/profiles/_scprobe_${SAFE}.collapsed"
  rm -f "$T"
  java -agentpath:"${AP_LIB}=start,event=${EV},collapsed,file=${T}" \
       -e 'long s=0; for(long i=0;i<400000000L;i++) s+=i; System.out.println(s);' \
       >/dev/null 2>&1 || true
  if [[ -s "$T" ]]; then echo "   ✅ ${EV}"; else echo "   ❌ ${EV} — 프로파일이 비었다"; fi
done
echo

run_one() {   # $1=query(scan|aggL) $2=event $3=rep
  local SAFE PROF G=()
  SAFE=$(echo "$2" | tr -c 'a-zA-Z0-9' '_')
  PROF="${RESULTS}/profiles/baseline__sc${SC_TAG}$1_${SAFE}_c${NCOL}_r${3}.collapsed"
  case "$1" in
    scan) G=() ;;
    aggL) G=(--group-col k01) ;;
    *) echo "unknown query $1" >&2; return 1 ;;
  esac
  [[ -s "$PROF" && "${FORCE:-0}" != "1" ]] && { echo "      skip $1/$2 r$3"; return; }

  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${2},collapsed,file=${PROF}" \
    --jars "$BASELINE_JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$SC_TABLE" --label "sc_${1}_${SAFE}_r${3}" \
      --cols "$NCOL" --col-list "$SC_COLS" "${G[@]}" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_baseline__sc${SC_TAG}$1_${SAFE}_r${3}.json" \
    >/dev/null 2>&1 || true
  [[ -s "$PROF" ]] || { echo "      *** 프로파일이 비었다 ($1/$2 r$3)" >&2; return 1; }
  echo "      $1/$2 r$3: $(wc -l < "$PROF") 스택"
}

echo "② 측정 (baseline 만 — 이 축의 질문은 셔플이지 패치가 아니다)"
START=$(date +%s); N=0
for R in $(seq 1 "$REPS"); do
  echo "  라운드 $R / $REPS"
  # 쿼리 순서를 라운드마다 뒤집는다. 순서 효과와 시간 표류를 섞지 않는다 (F-015).
  if (( R % 2 == 0 )); then QS=(aggL scan); else QS=(scan aggL); fi
  for EV in $SC_EVENTS; do
    for Q in "${QS[@]}"; do run_one "$Q" "$EV" "$R" || true; N=$((N+1)); done
  done
done

echo
echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
echo "채점: python3 tools/shufcache.py results"
