#!/usr/bin/env bash
# 패턴별 Iceberg V3 테이블 + DV 생성.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

mkdir -p "$WAREHOUSE" "$RESULTS"

for PAT in $PATTERNS; do
  TABLE="dv.db.t_${PAT}"
  echo
  echo "########################################################################"
  echo "# 생성: $TABLE"
  echo "########################################################################"

  spark-submit \
    --master "local[${LOCAL_CORES}]" \
    --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ./spark/gen_table.py \
      --warehouse "$WAREHOUSE" \
      --table "$TABLE" \
      --pattern "$PAT" \
      --rows-per-file "$ROWS_PER_FILE" \
      --num-files "$NUM_FILES" \
      --seed "$SEED" \
      --sparse-denom "$SPARSE_DENOM" \
      --run-start-frac "$RUN_START_FRAC" \
      --run-end-frac "$RUN_END_FRAC" \
      --cores "$LOCAL_CORES" \
      --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/gen_${PAT}.json"
done

echo
echo "✅ 테이블 생성 완료. 요약:"
for PAT in $PATTERNS; do
  python3 - "$RESULTS/gen_${PAT}.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
dvs = d["deletion_vectors"]
tot = sum(x["deleted_rows"] for x in dvs)
byt = sum(x["dv_bytes"] for x in dvs)
print(f"  {d['pattern']:<8} live={d['live_rows']:>12,}  deleted={tot:>10,}  "
      f"DV={len(dvs)}개 {byt:>8,}B  chunks/file={d['chunks_per_file']:.1f}")
PY
done
