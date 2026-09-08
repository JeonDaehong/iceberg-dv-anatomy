#!/usr/bin/env bash
# 실제 쿼리 경로의 하드웨어 카운터 — F-028 을 마이크로벤치 밖으로 꺼낸다.
#
# 왜 이 축인가:
#   F-028 은 JMH 마이크로벤치다. 거기서 "비용은 분기도 캐시도 아니고 명령어 수" 라는 답이
#   나왔는데, 그건 5,000행 배치를 도는 순수 루프였다. 진짜 Spark 스캔에는 Parquet 디코딩,
#   벡터화 배치, 코드젠, GC 가 다 섞여 있다. 같은 답이 나오는지는 재봐야 안다.
#
#   그리고 이걸로 그동안 "PMU 가 없어서 못 한다" 고 미뤄둔 두 가지를 같이 친다:
#     · F-008 의 bitmap 구간 반등(1.26배) 기전 — 분기 가설이었고 F-028 에서 약해졌다.
#     · F-018 의 샘플→wall 보정 0.53 기전 — "perfnorm + 태스크 분해 필요" 라고 적어뒀다.
#
# 무엇을 재는가 (두 층):
#   ① 총량  : `perf stat` 로 spark-submit 전체의 cycles/instructions/branch-misses.
#             JIT·GC·소스생성이 다 들어가므로 **arm 간 차이**만 의미가 있다. 절대값은 못 쓴다.
#   ② 귀속  : async-profiler 의 event 를 ctimer 대신 **cycles / branch-misses** 로 바꿔
#             메서드별로 귀속시킨다. 이게 진짜 원하는 것이다 —
#             "buildRowIdMapping 안에서 분기가 얼마나 틀리는가" 를 직접 본다.
#
# 통제:
#   - 같은 테이블, 같은 쿼리, 같은 iters. 바뀌는 것은 이벤트 종류와 arm 뿐이다.
#   - arm 순서를 라운드마다 교대한다.
#   - ①과 ②를 같이 켜지 않는다. 프로파일러가 PMU 를 잡으면 perf stat 과 카운터를 다툰다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   Q_P1. [F-028 이식] 실제 스캔에서도 **분기 예측 실패는 패치 전후 차이를 설명하지 못한다.**
#         baseline 과 patched 의 branch-misses 차이에 18 cycle 을 곱한 값이, 두 arm 의
#         cycles 차이의 **20% 미만**이다. 넘으면 마이크로벤치와 실제 경로가 다른 것이고
#         F-028 의 결론을 "마이크로벤치 한정" 으로 좁혀야 한다.
#
#   Q_P2. [F-018] **cycles 로 귀속한 DV 비중이 ctimer 로 귀속한 비중과 5%p 이내로 같다.**
#         같으면 보정 0.53 은 샘플링 방식의 문제가 아니다 (스캔 밖 고정비용이 범인이다).
#         다르면 — 특히 cycles 쪽이 낮으면 — DV 코드가 IPC 가 높아서 ctimer 가 과대평가한
#         것이고, 그게 0.53 의 일부를 설명한다.
#
#   Q_P3. [F-008] **d=6.10%(반등 정점)의 분기 실패율이 이웃 밀도보다 높지 않다.**
#         F-028 에서 분기 가설이 이미 약해졌으므로, 실제 경로에서도 아닐 것으로 본다.
#         반등의 범인이 분기가 아니라면 후보는 명령어 수(= 컨테이너 전환 구간에서 섞인
#         컨테이너를 둘 다 타는 비용)다. instructions 로 갈린다.
#
#   Q_P4. [규모 감각] 실제 스캔의 분기 실패율은 마이크로벤치(0.01~0.08%)보다 **훨씬 높다** —
#         1% 이상. 코드 풋프린트가 크고 가상 호출이 많기 때문이다. 이건 판정용이 아니라
#         "마이크로벤치 숫자를 실제 경로에 그대로 옮기면 안 된다" 를 확인하는 감각 확인이다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
source ./config.env
REPS="${_REPS_CALLER:-3}"

QP_TABLE="${QP_TABLE:-dv.g.d610}"
QP_TAG="${QP_TAG:-d610}"
QP_COLS="${QP_COLS:-1}"
PERF_DIR="${PERF_DIR:-/usr/lib/linux-tools-6.8.0-139}"
export PATH="${PERF_DIR}:${PATH}"
export SCAN_ITERS="${SCAN_ITERS:-30}"
export SCAN_WARMUP="${SCAN_WARMUP:-3}"

mkdir -p "$RESULTS/qperf" "$RESULTS/profiles"
command -v perf >/dev/null || { echo "perf 없음: $PERF_DIR" >&2; exit 1; }
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

jar_of() { case "$1" in baseline) echo "$BASELINE_JAR";; patched) echo "$PATCHED_JAR";; esac; }

# ── ① 총량: perf stat 로 감싼다 (프로파일러는 끈다) ────────────────────────
stat_one() {  # $1=arm $2=rep
  local OUT="${RESULTS}/qperf/stat_${1}_${QP_TAG}_r${2}.txt"
  [[ -s "$OUT" && "${FORCE:-0}" != "1" ]] && { echo "    skip stat ${1} r${2}"; return; }
  perf stat -x, -e cycles,instructions,branches,branch-misses,cache-misses \
    -o "$OUT" -- \
    spark-submit \
      --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
      --jars "$(jar_of "$1")" \
      --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
      ../phase0/spark/scan.py \
        --warehouse "$WAREHOUSE" --table "$QP_TABLE" --label "qp_${1}_r${2}" --cols "$QP_COLS" \
        --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
        --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
        --out-json "${RESULTS}/qperf/scan_${1}_${QP_TAG}_r${2}.json" \
    >/dev/null 2>&1 || true
  # 게이트: 로그가 아니라 산출물. perf 는 대상이 죽어도 통계를 남기므로 스캔 결과도 같이 본다.
  [[ -s "$OUT" ]] || { echo "    *** perf 출력이 비었다 (${1} r${2})" >&2; return 1; }
  [[ -s "${RESULTS}/qperf/scan_${1}_${QP_TAG}_r${2}.json" ]] \
    || { echo "    *** 스캔 결과 JSON 이 없다 — 쿼리가 실패했다 (${1} r${2})" >&2; return 1; }
  echo "    stat ${1} r${2}: $(grep -E '^[0-9]+,.*,cycles' "$OUT" | cut -d, -f1 | head -1) cycles"
}

# ── ② 귀속: async-profiler 이벤트를 바꿔 메서드별로 센다 ──────────────────
prof_one() {  # $1=arm $2=event $3=rep
  local EV="$2" SAFE PROF
  SAFE=$(echo "$EV" | tr -c 'a-zA-Z0-9' '_')
  PROF="${RESULTS}/profiles/${1}__qp${QP_TAG}_${SAFE}_r${3}.collapsed"
  [[ -s "$PROF" && "${FORCE:-0}" != "1" ]] && { echo "    skip prof ${1}/${EV} r${3}"; return; }
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${EV},collapsed,file=${PROF}" \
    --jars "$(jar_of "$1")" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$QP_TABLE" --label "qp_${1}_${SAFE}_r${3}" --cols "$QP_COLS" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/qperf/scan_${1}_${QP_TAG}_${SAFE}_r${3}.json" \
    >/dev/null 2>&1 || true
  if [[ ! -s "$PROF" ]]; then
    echo "    *** 프로파일이 비었다 (${1}/${EV} r${3}) — 이 이벤트를 못 쓰는 것일 수 있다" >&2
    return 1
  fi
  echo "    prof ${1}/${EV} r${3}: $(wc -l < "$PROF") 스택"
}

EVENTS="${QP_EVENTS:-cycles branch-misses ctimer}"

echo "실제 쿼리 경로 PMU  (테이블 ${QP_TABLE}, ${QP_COLS}컬럼, iters=${SCAN_ITERS}, REPS=${REPS})"
echo "  이벤트: ${EVENTS}"
echo

# 이벤트 지원 여부를 **본 측정 전에** 확인한다. 안 되는 이벤트로 라운드를 다 돌면 시간만 버린다.
echo "① 이벤트 지원 확인 (짧은 JVM 하나로)"
for EV in $EVENTS; do
  T="${RESULTS}/qperf/_probe_$(echo "$EV" | tr -c 'a-zA-Z0-9' '_').collapsed"
  rm -f "$T"
  # 프로브 워크로드는 이벤트 성격에 맞아야 한다. `-version` 은 CPU 시간을 거의 안 써서
  # ctimer 가 빈 프로파일을 내고 **거짓 음성**이 뜬다 (실제로 그렇게 떴다).
  #
  # ⚠️ 2026-09-09 정정 — 여기 있던 `java -e '...'` 는 **유효한 JDK 플래그가 아니다.**
  #    `Unrecognized option: -e` 로 JVM 이 뜨지도 않았고, 그래서 이 게이트는
  #    **어떤 이벤트에도 항상 ❌ 를 냈다.** 위 주석("워크로드가 안 맞아서")은
  #    그 ❌ 를 잘못 해석한 것이다. 진짜 원인은 플래그였다.
  #    단일 파일 소스 런처(java Foo.java)로 바꾼다 — JDK 11+ 에서 동작한다.
  cat > /tmp/_ApProbe.java <<'JAVA'
public class _ApProbe {
  public static void main(String[] a) {
    long s = 0;
    for (long i = 0; i < 400000000L; i++) { s += i; }
    System.out.println(s);
  }
}
JAVA
  java -agentpath:"${AP_LIB}=start,event=${EV},collapsed,file=${T}" \
       /tmp/_ApProbe.java >/dev/null 2>&1 || true
  if [[ -s "$T" ]]; then echo "   ✅ ${EV}"; else echo "   ❌ ${EV} — 프로파일이 비었다"; fi
done
echo

echo "② 총량 (perf stat)"
for R in $(seq 1 "$REPS"); do
  if (( R % 2 == 1 )); then ARMS=(baseline patched); else ARMS=(patched baseline); fi
  echo "  라운드 $R  순서: ${ARMS[*]}"
  for A in "${ARMS[@]}"; do stat_one "$A" "$R"; done
done
echo

echo "③ 귀속 (async-profiler)"
for R in $(seq 1 "$REPS"); do
  if (( R % 2 == 1 )); then ARMS=(baseline patched); else ARMS=(patched baseline); fi
  echo "  라운드 $R  순서: ${ARMS[*]}"
  for A in "${ARMS[@]}"; do
    for EV in $EVENTS; do prof_one "$A" "$EV" "$R" || true; done
  done
done

echo
echo "채점: python3 tools/qperf.py results"
