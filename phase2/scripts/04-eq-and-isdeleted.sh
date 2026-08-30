#!/usr/bin/env bash
# A1 + A2 — 패치가 '건드리지 않은' 경로와 '건드렸지만 안 잰' 경로를 확인한다.
#
#   A1  equality delete 가 있는 테이블            패치는 기존 루프로 폴백한다.
#                                                 "안 건드렸다" != "안 느려졌다"
#       eq0   : eq delete 만 (DV 없음)
#       eq50  : DV 0.5% + eq delete   ← 게이트가 빠른 경로를 적극적으로 '거부'하는 경우
#
#   A2  _deleted 메타컬럼 투영                    buildIsDeleted 경로.
#       d50isdel / d610isdel                      같이 고쳤지만 F-011 에서 안 쟀다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR" && -f "$BASELINE_JAR" ]] || { echo "jar 없음" >&2; exit 1; }

jar_of() { case "$1" in baseline) echo "$BASELINE_JAR";; patched) echo "$PATCHED_JAR";; esac; }

# ───────────────────────── 정확성 먼저 ─────────────────────────
verify() {   # $1=arm  $2=suffix  $3=tables  $4...=extra args
  local ARM="$1" SUF="$2" TABLES="$3"; shift 3
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --jars "$(jar_of "$ARM")" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ./spark/verify.py \
      --warehouse "$WAREHOUSE" --tables "$TABLES" --label "${ARM}_${SUF}" \
      --out-json "${RESULTS}/verify_${SUF}_${ARM}.json" "$@" \
    2>&1 | grep -E "^  dv\." || true
}

echo "### 정확성 — equality delete"
for ARM in baseline patched; do
  echo "  $ARM"; verify "$ARM" eq "dv.g.eq0,dv.g.eq50"
done

echo
echo "### 정확성 — _deleted 투영"
for ARM in baseline patched; do
  echo "  $ARM"; verify "$ARM" isdel "dv.g.d50,dv.g.d610" --is-deleted
done

echo
export PYTHONIOENCODING=utf-8
python3 - "$RESULTS" <<'PY'
import json, os, sys
r = sys.argv[1]
bad = 0
for suf, title in [("eq", "equality delete"), ("isdel", "_deleted 투영")]:
    a = json.load(open(os.path.join(r, f"verify_{suf}_baseline.json")))["tables"]
    b = json.load(open(os.path.join(r, f"verify_{suf}_patched.json")))["tables"]
    print("=" * 84)
    print(f" 정확성 게이트 — {title}")
    print("=" * 84)
    for t in sorted(a):
        x, y = a[t], b.get(t, {})
        keys = [k for k in x if k != "columnar"]
        diff = [k for k in keys if x[k] != y.get(k)]
        vec = "벡터화" if x.get("columnar") and y.get("columnar") else "⚠️ 행경로"
        print(f"  {t:14} {'✅ 동일' if not diff else '❌ 불일치'}  "
              f"count={x['count']:>9,}" +
              (f"  deleted={x['n_deleted']:>8,}" if "n_deleted" in x else "") +
              f"  {vec}")
        for k in diff:
            print(f"       {k}: {x[k]} -> {y.get(k)}")
        bad += len(diff)
    print()
if bad:
    print("  ❌ 불일치가 있습니다. 성능 측정으로 넘어가지 마세요.")
    sys.exit(1)
print("  ✅ 전부 동일. 성능 측정으로 진행합니다.")
PY

# ───────────────────────── 프로파일 ─────────────────────────
run_one() {   # $1=table  $2=tag  $3=arm  $4=rep  $5=extra(scan.py 인자)
  local PROF="${RESULTS}/profiles/${3}__${2}_c${SCAN_COLS}_r${4}.collapsed"
  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then echo "      skip ${3} r${4}"; return; fi
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$(jar_of "$3")" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$1" --label "${3}_${2}_r${4}" --cols "$SCAN_COLS" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${3}__${2}_r${4}.json" ${5:-} \
    2>&1 | grep -E "median=" | sed "s/^/      ${3} r${4}: /" || true
}

# table : tag : extra
CONFIGS=(
  "dv.g.eq0:eq0:"
  "dv.g.eq50:eq50:"
  "dv.g.d50:d50isdel:--is-deleted"
  "dv.g.d610:d610isdel:--is-deleted"
)

echo
echo "설정 ${#CONFIGS[@]}개 × 2 arm × ${REPS}회 = $(( ${#CONFIGS[@]} * 2 * REPS )) 프로파일"
START=$(date +%s)
for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  for C in "${CONFIGS[@]}"; do
    IFS=':' read -r TABLE TAG EXTRA <<< "$C"
    echo "  $TAG"
    run_one "$TABLE" "$TAG" baseline "$R" "$EXTRA"
    run_one "$TABLE" "$TAG" patched  "$R" "$EXTRA"
  done
  echo
done
echo "✅ 완료 — $(( $(date +%s) - START ))초"

python3 ./tools/compare.py "$RESULTS" | tee "${RESULTS}/compare.txt"
