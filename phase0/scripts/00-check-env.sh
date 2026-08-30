#!/usr/bin/env bash
# Phase 0 환경 점검. 여기서 실패하는 항목은 뒤에서 더 비싸게 실패한다.
set -uo pipefail
cd "$(dirname "$0")/.."
source ./config.env

FAIL=0
ok()   { printf '  \033[32m[ok]\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33m[warn]\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m[FAIL]\033[0m %s\n' "$1"; FAIL=1; }

echo "=== Phase 0 환경 점검 ==="

# --- OS ---
if [[ "$(uname -s)" != "Linux" ]]; then
  bad "Linux 가 아닙니다 ($(uname -s)). async-profiler/perf 는 Linux 전용 — WSL2 를 쓰세요."
else
  ok "OS: Linux $(uname -r)"
  grep -qi microsoft /proc/version 2>/dev/null && \
    warn "WSL2 감지 — 하드웨어 PMU 없음. AP_EVENT=ctimer 로 진행합니다 (Phase 0 은 문제 없음)."
fi

# --- Java ---
if command -v java >/dev/null 2>&1; then
  JV=$(java -version 2>&1 | head -1)
  JMAJ=$(java -version 2>&1 | head -1 | sed -E 's/.*"([0-9]+).*/\1/')
  if [[ "${JMAJ:-0}" -ge 17 ]]; then ok "Java: $JV"
  else bad "Java $JMAJ — Spark 4.0 은 17 이상 필요"; fi
else
  bad "java 없음:  sudo apt install -y openjdk-17-jdk"
fi

# --- Spark ---
if command -v spark-submit >/dev/null 2>&1; then
  ok "spark-submit: $(command -v spark-submit)"
  SV=$(spark-submit --version 2>&1 | grep -oE 'version [0-9.]+' | head -1 | awk '{print $2}')
  [[ -n "${SV:-}" ]] && ok "Spark 버전: $SV"
  case "${SV:-}" in
    4.*) [[ "$SCALA_BIN" == "2.13" ]] || bad "Spark 4.x 인데 SCALA_BIN=$SCALA_BIN (2.13 이어야 함)";;
    3.5*) [[ "$SCALA_BIN" == "2.12" ]] || warn "Spark 3.5 + Scala $SCALA_BIN — 배포판 스칼라 버전과 일치하는지 확인";;
  esac
else
  bad "spark-submit 없음. SPARK_HOME/bin 을 PATH 에 추가하세요 (docs/SETUP.md)"
fi

# --- PySpark ---
if command -v python3 >/dev/null 2>&1; then ok "python3: $(python3 --version 2>&1)"
else bad "python3 없음"; fi

# --- Iceberg jar / packages ---
if [[ -n "${ICEBERG_JAR:-}" ]]; then
  ok "Iceberg jar (오프라인): $ICEBERG_JAR"
else
  warn "로컬 Iceberg jar 없음 -> --packages 로 Ivy 해석합니다."
  warn "  매 submit 마다 해석 시간이 프로파일 분모에 섞입니다. 가능하면 jar 를 받아두세요:"
  warn "  curl -L -o \$HOME/opt/jars/${ICEBERG_JAR_NAME} \\"
  warn "    https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-${SPARK_MAJOR}_${SCALA_BIN}/${ICEBERG_VERSION}/${ICEBERG_JAR_NAME}"
fi

# --- warehouse 위치 (WSL2 I/O 함정) ---
if [[ "$WAREHOUSE" == /mnt/* ]]; then
  bad "warehouse 가 /mnt 아래입니다: $WAREHOUSE
         WSL2 에서 Windows 드라이브는 I/O 가 수십 배 느려 프로파일이 I/O 에 잠식됩니다.
         DV_WAREHOUSE 를 Linux 네이티브 경로로 설정하세요."
else
  ok "warehouse: $WAREHOUSE"
fi

# --- async-profiler ---
if [[ -f "$AP_LIB" ]]; then
  ok "async-profiler: $AP_LIB"
else
  bad "async-profiler 없음: $AP_LIB   (AP_HOME 을 확인하거나 docs/SETUP.md 참조)"
fi

# --- perf (Phase 0 에는 불필요, Phase 2 대비 정보성) ---
if command -v perf >/dev/null 2>&1; then
  if perf stat -e cycles,instructions sleep 0.1 2>&1 | grep -q "not supported"; then
    warn "perf 는 있으나 하드웨어 카운터 미지원 (가상화 환경). Phase 0 은 무관, Phase 2 는 .metal 필요."
  else
    ok "perf 하드웨어 카운터 사용 가능 — Phase 2 를 여기서 돌려도 됩니다."
  fi
else
  warn "perf 없음 (Phase 0 은 불필요)."
fi

# --- 디스크 ---
AVAIL_KB=$(df -Pk . | tail -1 | awk '{print $4}')
AVAIL_GB=$(( AVAIL_KB / 1024 / 1024 ))
if [[ $AVAIL_GB -ge 20 ]]; then ok "디스크 여유: ${AVAIL_GB}GB"
else bad "디스크 여유 ${AVAIL_GB}GB — 20GB 이상 권장"; fi

# --- 메모리 ---
MEM_GB=$(( $(grep MemTotal /proc/meminfo | awk '{print $2}') / 1024 / 1024 ))
if [[ $MEM_GB -ge 15 ]]; then ok "RAM: ${MEM_GB}GB"
else warn "RAM ${MEM_GB}GB — DRIVER_MEM=$DRIVER_MEM 를 낮추거나 .wslconfig 로 상향하세요"; fi

# --- 데이터 규모 sanity ---
CHUNKS=$(python3 -c "print(f'{$ROWS_PER_FILE/65536:.1f}')")
echo
echo "  설정: ${NUM_FILES} 파일 x ${ROWS_PER_FILE} 행 = 파일당 ${CHUNKS} 개 Roaring 청크"
python3 -c "
c=$ROWS_PER_FILE/65536
if c < 10: print('  \033[33m[warn]\033[0m 파일당 청크가 %.1f 개뿐 — 청크 단위 통계가 불안정합니다.'%c)
" 2>/dev/null

echo
if [[ $FAIL -eq 0 ]]; then
  echo "  ✅ 통과. ./scripts/run-all.sh 로 진행하세요."
else
  echo "  ❌ 실패 항목이 있습니다. docs/SETUP.md 를 보고 해결 후 다시 실행하세요."
  exit 1
fi
