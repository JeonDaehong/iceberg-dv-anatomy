#!/usr/bin/env bash
# 밀도 격자 테이블 생성.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

mkdir -p "$WAREHOUSE" "$RESULTS"
echo "warehouse: $WAREHOUSE"
echo "밀도(bp) : $DENSITIES_BP"
echo

START=$(date +%s)
for BP in $DENSITIES_BP; do
  TABLE="dv.g.d${BP}"
  if [[ -f "${RESULTS}/gen_${BP}.json" && "${FORCE:-0}" != "1" ]]; then
    echo "  skip d=${BP}bp (이미 생성됨. 다시 하려면 FORCE=1)"
    continue
  fi
  spark-submit \
    --master "local[${LOCAL_CORES}]" \
    --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ./spark/gen_grid.py \
      --warehouse "$WAREHOUSE" \
      --table "$TABLE" \
      --density-bp "$BP" \
      --rows-per-file "$ROWS_PER_FILE" \
      --num-files "$NUM_FILES" \
      --seed "$SEED" \
      --cores "$LOCAL_CORES" \
      --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/gen_${BP}.json" \
    2>&1 | grep -E "^===|predicate|삭제 |DV |레이아웃"
done

echo
echo "✅ 생성 완료 ($(( $(date +%s) - START ))초)"
python3 - "$RESULTS" <<'PY'
import glob, json, os, sys
rows = []
for f in glob.glob(os.path.join(sys.argv[1], "gen_*.json")):
    rows.append(json.load(open(f)))
rows.sort(key=lambda r: r["density_bp"])
print(f"\n  {'d(bp)':>7} {'목표':>8} {'실제':>8} {'삭제행':>12} {'DV bytes':>11} {'기대 청크카디널리티':>16}")
print("  " + "-" * 70)
for r in rows:
    print(f"  {r['density_bp']:>7} {r['target_density']*100:>7.2f}% {r['actual_density']*100:>7.3f}% "
          f"{r['deleted_rows']:>12,} {r['dv_bytes']:>11,} {r['expected_chunk_cardinality']:>16,.0f}")
print(f"\n  array/bitmap 임계 카디널리티 = 4,096")
PY
