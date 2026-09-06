#!/usr/bin/env bash
# 프로파일 -> DV 비중 귀속 -> 게이트 판정.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

shopt -s nullglob
PROFS=( "${RESULTS}"/profiles/*.collapsed )
if [[ ${#PROFS[@]} -eq 0 ]]; then
  echo "프로파일이 없습니다. ./scripts/02-profile-scans.sh 를 먼저 실행하세요." >&2
  exit 1
fi

# 게이트 판정은 '좁은 스캔'(DV 비중 상한) 기준. 상한조차 낮으면 주제가 성립하지 않는다.
python3 ./tools/attribute.py "${PROFS[@]}" \
  --gate-on "_c${NARROW_COLS}." \
  --out-json "${RESULTS}/attribution.json" \
  | tee "${RESULTS}/attribution.txt"

echo
echo "--- 스캔 시간 (참고: DV 유무에 따른 wall-clock 차이) ---"
python3 - "$RESULTS" <<'PY'
import glob, json, os, sys
res = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(res, "scan_*.json"))):
    d = json.load(open(f))
    rows.append((d["label"], d["n_cols"], d["median_s"]))
base = {c: t for (l, c, t) in rows if l.startswith("none_")}
print(f"  {'label':<20} {'cols':>5} {'median(s)':>10} {'vs none':>10}")
for l, c, t in rows:
    b = base.get(c)
    delta = f"{(t/b-1)*100:+.1f}%" if b else "-"
    print(f"  {l:<20} {c:>5} {t:>10.3f} {delta:>10}")
print("\n  주의: wall-clock 차이는 DV 체크 비용만이 아니라 '삭제된 행을 안 내보내서")
print("        줄어든 하류 작업'도 포함한다. 인과 귀속은 프로파일 쪽 수치를 쓸 것.")
PY

echo
echo "결과: ${RESULTS}/attribution.txt , ${RESULTS}/attribution.json"
