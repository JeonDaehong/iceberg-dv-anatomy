#!/usr/bin/env bash
# 격자 전체의 컨테이너 분포를 덤프하고 밀도 곡선을 만든다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

mkdir -p "$RESULTS"
INSPECT="../tools/dv_inspect.py"
export PYTHONIOENCODING=utf-8

echo "밀도별 컨테이너 덤프..."
for BP in $DENSITIES_BP; do
  DIR="${WAREHOUSE}/g/d${BP}/data"
  shopt -s nullglob
  PUFFINS=( "$DIR"/*.puffin )
  if [[ ${#PUFFINS[@]} -eq 0 ]]; then
    echo "  d=${BP}bp: puffin 없음 (건너뜀)"
    continue
  fi
  python3 "$INSPECT" "${PUFFINS[@]}" \
      --csv "${RESULTS}/containers_${BP}.csv" \
      --limit 0 > /dev/null 2>&1 \
    || { echo "  d=${BP}bp: 파싱 실패"; continue; }
  N=$(( $(wc -l < "${RESULTS}/containers_${BP}.csv") - 1 ))
  echo "  d=${BP}bp: 컨테이너 ${N}개 -> containers_${BP}.csv"
done

echo
python3 ./tools/container_curve.py "$RESULTS" "$ROWS_PER_FILE" | tee "${RESULTS}/container_curve.txt"
