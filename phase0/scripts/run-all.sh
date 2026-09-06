#!/usr/bin/env bash
# Phase 0 전체 실행. 처음이면 이것만 돌리면 된다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

START=$(date +%s)

echo "########################################################################"
echo "# Phase 0 — Go/No-Go 게이트"
echo "# $(date)"
echo "########################################################################"

./scripts/00-check-env.sh

mkdir -p "$RESULTS"
{
  echo "date          : $(date -Is)"
  echo "host          : $(uname -a)"
  echo "java          : $(java -version 2>&1 | head -1)"
  echo "spark         : $(spark-submit --version 2>&1 | grep -oE 'version [0-9.]+' | head -1)"
  echo "iceberg_pkg   : ${ICEBERG_PKG}"
  echo "rows_per_file : ${ROWS_PER_FILE}"
  echo "num_files     : ${NUM_FILES}"
  echo "seed          : ${SEED}"
  echo "ap_event      : ${AP_EVENT}"
  echo "cores         : ${LOCAL_CORES}"
  echo "driver_mem    : ${DRIVER_MEM}"
} > "${RESULTS}/env.txt"
echo; echo "환경 기록: ${RESULTS}/env.txt"

./scripts/01-gen-tables.sh
./scripts/02-profile-scans.sh
./scripts/03-attribute.sh

echo
echo "########################################################################"
echo "# 완료 ($(( $(date +%s) - START ))초)"
echo "#"
echo "# 다음 할 일:"
echo "#   1) ${RESULTS}/attribution.txt 의 게이트 판정 확인"
echo "#   2) 판정에 따라:"
echo "#      GO    -> Phase 1 (컨테이너 매핑) 착수. tools/dv-inspect 작성."
echo "#      PIVOT -> Phase 1 은 축소, README §7.1 (ColumnarBatchUtil) 로 무게중심 이동."
echo "#      STOP  -> README §5.1 의 방향 전환 계획으로."
echo "#   3) 판정과 근거를 docs/findings.md 에 기록"
echo "########################################################################"
