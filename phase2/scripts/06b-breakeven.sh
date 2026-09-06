#!/usr/bin/env bash
# 손익분기 컬럼 수 — 1컬럼과 20컬럼 사이를 채운다.
#
# 06-widescan.sh 를 WIDE_COLS=3,5,10 으로 세 번 돌리는 얇은 드라이버다.
# 예측(Q5~Q7)과 측정 로직은 전부 06-widescan.sh 헤더에 있다. 여기 적지 않는다 —
# 예측이 두 군데 있으면 사후에 어느 쪽을 고쳤는지 알 수 없게 된다.
#
# FLIP_MODE=alternate 인 이유: w20 은 전반 3라운드/후반 3라운드로 순서를 나눴는데,
# 그러면 '순서' 와 '측정 시각' 이 얽힌다(F-015 의 라운드 1 오염이 그 예다).
# 신규 축은 라운드마다 뒤집어 두 교란을 분리한다.
set -euo pipefail
cd "$(dirname "$0")/.."

WIDTHS="${WIDTHS:-3 5 10}"
export REPS="${REPS:-6}"
export FLIP_MODE="${FLIP_MODE:-alternate}"

START=$(date +%s)
for C in $WIDTHS; do
  echo "==================== WIDE_COLS=${C} ===================="
  WIDE_COLS="$C" ./scripts/06-widescan.sh
done
echo "✅ 손익분기 전체 완료 — $(( $(date +%s) - START ))초"
