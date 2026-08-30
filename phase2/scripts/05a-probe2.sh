#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env
for B in 1 5000 100000; do
  echo "### batch-size = $B"
  spark-submit --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --jars "$BASELINE_JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py --warehouse "$WAREHOUSE" --table dv.g.d610 \
      --label "probe_b${B}" --cols 1 --batch-size "$B" --warmup 1 --iters 3 \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" 2>&1 | grep -E "median=" || true
done
