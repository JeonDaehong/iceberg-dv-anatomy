#!/usr/bin/env bash
# 정확성 게이트 — 패치본이 베이스라인과 같은 행을 읽는지 먼저 확인한다.
#
# 빨라졌는데 답이 다르면 아무 의미가 없다. 성능 측정보다 이게 먼저다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

mkdir -p "$RESULTS"

TABLES="dv.g.d0,dv.g.d50,dv.g.d610,dv.g.d700,dv.g.d5000,dv.g.d50L1,dv.g.d50L4096"

run_verify() {   # $1=label  $2=jar
  echo "### $1  ($2)"
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --jars "$2" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ./spark/verify.py \
      --warehouse "$WAREHOUSE" --tables "$TABLES" --label "$1" \
      --out-json "${RESULTS}/verify_${1}.json" \
    2>&1 | grep -E "^  dv\.|->" || true
}

run_verify baseline "$BASELINE_JAR"
echo
run_verify patched "$PATCHED_JAR"

echo
python3 - "$RESULTS" <<'PY'
import json, sys, os
r = sys.argv[1]
a = json.load(open(os.path.join(r, "verify_baseline.json")))["tables"]
b = json.load(open(os.path.join(r, "verify_patched.json")))["tables"]

print("=" * 78)
print(" 정확성 게이트 — 베이스라인 vs 패치본")
print("=" * 78)
bad = 0
for t in sorted(a):
    x, y = a[t], b.get(t)
    if y is None:
        print(f"  {t:16} ❌ 패치본 결과 없음"); bad += 1; continue
    same = all(x[k] == y[k] for k in ("count", "sum_id", "min_id", "max_id"))
    vec = "벡터화" if x["columnar"] and y["columnar"] else "⚠️ 행경로"
    print(f"  {t:16} {'✅ 동일' if same else '❌ 불일치'}  count={x['count']:>9,}  {vec}")
    if not same:
        bad += 1
        for k in ("count", "sum_id", "min_id", "max_id"):
            if x[k] != y[k]:
                print(f"       {k}: {x[k]} -> {y[k]}")
print()
if bad:
    print(f"  ❌ {bad}개 테이블 불일치. 성능 측정으로 넘어가지 마세요.")
    sys.exit(1)
print("  ✅ 전 테이블 동일. 성능 측정으로 진행 가능.")
PY
