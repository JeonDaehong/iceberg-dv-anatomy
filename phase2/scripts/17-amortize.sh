#!/usr/bin/env bash
# F-018 보정 0.53 은 워크로드의 성질인가, 내 측정 설계의 성질인가.
#
# 무엇을 알게 됐나 (탐색적 분해, tools/outside_scan.py):
#   프로파일 전체 샘플의 **70.7% 가 스캔 밖**이고, 그중 가장 큰 것이 **JIT 컴파일 41.6%**,
#   그다음이 클래스 로딩 10.5% 와 Spark 실행 골격 10.6% 다. 전부 패치가 못 건드리는 고정비다.
#
#   그리고 그 고정비는 크기만 한 게 아니라 **흔들린다** — baseline 전체 샘플이 라운드마다
#   170,083 / 180,064 / 222,972 로 31% 퍼진다. 그래서 스캔에서 31.6% 를 아껴도
#   전체에서는 그게 안 보인다 (3라운드 중 2라운드에서 오히려 음수가 나왔다).
#
# 왜 이게 중요한가:
#   우리는 **JVM 을 새로 띄워 30회만 돌린다.** JIT 은 그 30회에만 상각된다.
#   프로덕션 익스큐터는 오래 살면서 수천 번 돌므로 JIT 몫이 훨씬 작다.
#   그렇다면 F-018 의 보정 0.53 은 **워크로드가 아니라 내 벤치 설계의 성질**이고,
#   실무에서의 벽시계 이득은 우리가 보고한 것보다 **좋을** 수 있다.
#   이건 이슈에 그대로 실릴 문장이라 추측으로 두면 안 된다.
#
# 통제:
#   iters 말고는 전부 같다 — 같은 테이블, 같은 jar 쌍, 같은 컬럼, 같은 프로파일러 이벤트.
#   arm 순서를 라운드마다 교대한다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   A1. iters 를 30 -> 300 으로 올리면 JIT 이 프로파일에서 차지하는 몫이 **10% 미만**으로
#       떨어진다. (30회에 41.6% 였으니 10배 돌리면 대략 그 근처여야 한다. 컴파일 작업량
#       자체는 거의 안 변하고 분모만 10배가 되므로.)
#
#   A2. [판정용] 그러면 희석이 풀린다. **전체절감 / 스캔절감 >= 0.5.**
#       (30회에서는 -0.26 으로 계산조차 안 됐다.)
#       깨지면 고정비 희석은 보정 0.53 의 설명이 아니고, 범인을 다시 찾아야 한다.
#
#   A3. 라운드 간 전체 샘플의 퍼짐이 준다: **15% 미만** (30회에서 31%).
#       고정비가 상각되면 분모가 안정되기 때문이다.
#
# ⚠️ 이 실험이 재지 '못하는' 것: 진짜 프로덕션 익스큐터. 우리는 여전히 단일 JVM 이고
#    iters 를 늘려 JIT 을 상각시킬 뿐이다. 방향은 보여주되 크기는 주장하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."

# ⚠️ config.env 는 `SCAN_ITERS="${SCAN_ITERS:-30}"` 로 값을 **채운다.** 그래서 source 뒤에
#    `SCAN_ITERS="${SCAN_ITERS:-300}"` 라고 쓰면 그 기본값은 절대 안 먹는다 (죽은 코드).
#    이 축은 iters 를 바꾸는 게 전부라서 이걸 놓치면 측정이 통째로 무의미해진다 —
#    실제로 처음 돌렸을 때 30 회로 돌았고, 전체 샘플이 오히려 **줄어서** 겨우 알아챘다.
#    호출자 값을 source **앞에서** 붙잡는다.
_REPS_CALLER="${REPS:-}"
_ITERS_CALLER="${SCAN_ITERS:-}"
source ./config.env
REPS="${_REPS_CALLER:-3}"

AM_TABLE="${AM_TABLE:-dv.g.d610}"
AM_TAG="${AM_TAG:-d610am}"
AM_COLS="${AM_COLS:-1}"
export SCAN_ITERS="${_ITERS_CALLER:-300}"
export SCAN_WARMUP="${SCAN_WARMUP:-3}"

mkdir -p "$RESULTS/qperf" "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

jar_of() { case "$1" in baseline) echo "$BASELINE_JAR";; patched) echo "$PATCHED_JAR";; esac; }

run_one() {  # $1=arm $2=rep
  local PROF="${RESULTS}/profiles/${1}__qp${AM_TAG}_cycles_r${2}.collapsed"
  local SJ="${RESULTS}/qperf/scan_${1}_${AM_TAG}_r${2}.json"
  [[ -s "$PROF" && -s "$SJ" && "${FORCE:-0}" != "1" ]] && { echo "    skip ${1} r${2}"; return; }
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=cycles,collapsed,file=${PROF}" \
    --jars "$(jar_of "$1")" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$AM_TABLE" --label "am_${1}_r${2}" --cols "$AM_COLS" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "$SJ" \
    >/dev/null 2>&1 || true
  # 게이트는 산출물을 본다.
  [[ -s "$PROF" ]] || { echo "    *** 프로파일이 비었다 (${1} r${2})" >&2; return 1; }
  [[ -s "$SJ"   ]] || { echo "    *** 스캔 JSON 이 없다 (${1} r${2})" >&2; return 1; }
  # 게이트: 로그가 아니라 **산출물**에서 iters 를 확인한다. 같은 함정을 일곱 번 밟았다.
  local GOT
  GOT=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8')).get('iters'))" "$SJ")
  if [[ "$GOT" != "$SCAN_ITERS" ]]; then
    echo "    *** iters 가 ${SCAN_ITERS} 가 아니라 ${GOT} 로 돌았다 — 오버라이드가 먹혔다" >&2
    return 1
  fi
  echo "    ${1} r${2}: $(wc -l < "$PROF") 스택"
}

echo "JIT 상각 축  (${AM_TABLE}, iters=${SCAN_ITERS}, REPS=${REPS})"
if [[ "${SCAN_ITERS}" == "30" ]]; then
  echo "  ⚠️ iters 가 30 이다 — config.env 기본값 그대로다. 이 축은 iters 를 올려야 의미가 있다." >&2
fi
echo "  iters 말고는 15-qperf.sh 와 전부 같다. JIT 이 상각되면 희석이 풀리는가."
echo
for R in $(seq 1 "$REPS"); do
  if (( R % 2 == 1 )); then ARMS=(baseline patched); else ARMS=(patched baseline); fi
  echo "  라운드 $R  순서: ${ARMS[*]}"
  for A in "${ARMS[@]}"; do run_one "$A" "$R"; done
done

echo
echo "채점:"
echo "  python3 tools/outside_scan.py 'results/profiles/baseline__qp${AM_TAG}_cycles_r*.collapsed'"
echo "  QP_TAG=${AM_TAG} python3 tools/dilution.py results"
