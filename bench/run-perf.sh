#!/usr/bin/env bash
# WSL 에서 JMH 를 돌리며 PMU 카운터(-prof perfnorm)를 같이 잡는다.
#
# 왜 이 스크립트인가:
#   이 프로젝트는 F-008 부터 "PMU 없음" 을 전제로 적어왔다 (발견 8개 + 이슈 초안 caveat).
#   그런데 최근 WSL2 커널(6.18)은 Hyper-V vPMU 를 노출한다 —
#   /sys/bus/event_source/devices/cpu/events/ 에 branch-misses 가 있고 실제로 값이 나온다.
#   즉 기전 확증에 베어메탈이 필요 없다. 전제가 낡았던 것이다.
#
# 무엇을 확증하려는가:
#   F-001 은 "array 컨테이너는 이진 탐색이라 분기 예측 실패가 많아 비싸다" 고 적었지만
#   근거는 비용 곡선의 모양뿐이었다. `perfnorm` 은 연산당 branch-misses 를 직접 센다.
#   패턴별로 비교하면 그 설명이 맞는지 숫자로 갈린다.
#
# ── 예측 (측정 전 기록) ────────────────────────────────────────────────
#   통제군 먼저: perf/BranchControl.java 로 sorted-vs-shuffled 를 재서
#   vPMU 가 예측 실패를 실제로 세는지 확인했다 → 9.2M vs 108.6M (11.8배). 센다.
#   ※ 정직하게 밝힌다: SPARSE_0_5 한 패턴은 이 예측을 적기 전에 스모크로 이미 봤다
#     (39.882 misses/op). 아래 예측은 나머지 패턴과 '판정 기준'에 대한 것이다.
#
#   P-B1  array 패턴(SPARSE_0_5, MEDIUM_5)의 op 당 branch-misses 가
#         bitmap 패턴(DENSE_12)보다 2배 이상 크다.
#   P-B2  MEDIUM_5 > SPARSE_0_5. 카디널리티 327 -> 3,276 이면 이진 탐색이
#         log2 로 8.4 -> 11.7 단계가 되므로 실패 횟수도 같은 방향으로 는다.
#   P-B3  ★기전 판정★ F-001 은 array 가 행당 16.0ns, bitmap 이 4.0ns 라고 쟀다.
#         차이 12ns 는 4.45GHz 에서 약 53 cycle. 분기 예측 실패 1회를 15~20 cycle
#         로 보면 이 차이를 분기로 설명하려면 array 가 bitmap 보다
#         **행당 2.7~3.6회** 더 실패해야 한다 (배치 5000행 기준 op 당 13,000~18,000회).
#         그만큼 안 나오면 F-001 의 기전 설명은 반증된 것이다.
#   P-B4  대안 가설. 비용 차이가 instructions/op 로 설명된다면
#         (array 가 bitmap 보다 명령어를 훨씬 많이 쓴다면) 답은 "분기 예측이 아니라
#         그냥 일을 더 한다" 이다. P-B3 이 깨지고 P-B4 가 서면 F-001 기전을 갈아끼운다.
# ──────────────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(dirname "$0")"

LIB="${LIB:-/mnt/d/dv-tools/bench-libs}"
PERF_DIR="${PERF_DIR:-/usr/lib/linux-tools-6.8.0-139}"
FILTER="${1:-a_baseline}"; [[ $# -gt 0 ]] && shift  # "$@" 에 필터가 중복으로 들어가지 않게
PATTERNS="${PATTERNS:-SPARSE_0_5,MEDIUM_5,DENSE_12,RUN_PARTIAL}"
FORKS="${FORKS:-1}"
WI="${WI:-2}"
IT="${IT:-3}"

# PATH 에서 Windows 경로를 걷어낸다 (공백 때문에 export 가 깨진다)
# ⚠ JAVA_HOME 은 이미 쉘이 export 해둔다 (java-17). `${JAVA_HOME:-...}` 로 쓰면
#   기본값이 죽은 코드가 된다 — config.env 오버라이드 함정과 같은 모양이다.
#   벤치 클래스는 JDK 21 로 컴파일돼 있다 (Spark 측정은 JDK 17 — 다른 축이다).
BENCH_JAVA="${BENCH_JAVA:-/usr/lib/jvm/java-21-openjdk-amd64}"
export JAVA_HOME="$BENCH_JAVA"
export PATH="${BENCH_JAVA}/bin:${PERF_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

command -v perf >/dev/null || { echo "perf 없음: $PERF_DIR" >&2; exit 1; }
sysctl -w kernel.perf_event_paranoid=-1 >/dev/null 2>&1 || true

CP="build/classes"
for j in "$LIB"/*.jar; do CP="$CP:$j"; done
[[ -d build/classes ]] || { echo "build/classes 없음" >&2; exit 1; }

echo "필터   : $FILTER"
echo "패턴   : $PATTERNS"
echo "perf   : $(perf --version 2>&1 | head -1)"
echo "java   : $(java -version 2>&1 | head -1)"
echo "CPU    : $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)"
echo

java -cp "$CP" org.openjdk.jmh.Main "$FILTER" \
  -p "pattern=$PATTERNS" \
  -prof perfnorm \
  -f "$FORKS" -wi "$WI" -i "$IT" -r 2s -w 2s \
  "$@"
