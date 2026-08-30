#!/usr/bin/env bash
# A1 — equality delete 테이블 두 개를 만든다.
#
#   eq0   : DV 없음 + equality delete 만
#   eq50  : DV 0.5% + equality delete  ← 게이트가 빠른 경로를 '거부'하는 경우
#
# eq50 이 핵심이다. 패치는 equality delete 가 있으면 무조건 기존 루프로 폴백하는데,
# 그때 추가된 것은 배치당 hasEqDeletes() 호출 하나뿐이다. 그게 정말 공짜인지 잰다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

mkdir -p "$RESULTS"

TOTAL=$(( ROWS_PER_FILE * NUM_FILES ))
# 8,000,000 / 200 = 40,000 행을 equality delete 로 지운다 (0.5%).
# DV 쪽 0.5% 와 겹치지 않게 서로 다른 술어를 쓴다 (DV 는 hash 기반, eq 는 id 배수).
EQ_MODULUS=200

gen() {   # $1=table  $2=density_bp
  echo "### $1  (DV density=${2}bp)"
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase1/spark/gen_grid.py \
      --warehouse "$WAREHOUSE" --table "$1" \
      --density-bp "$2" --run-length 1 --occupancy-bp 10000 \
      --rows-per-file "$ROWS_PER_FILE" --num-files "$NUM_FILES" --seed "$SEED" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/gen_$(basename "$1").json" \
    2>&1 | grep -E "^===|deleted|삭제|위반" || true
}

gen dv.g.eq0  0
gen dv.g.eq50 50

# ---------- equality delete 파일 추가 ----------
# Spark 은 equality delete 를 못 쓴다. Iceberg Java API 로 직접 만든다.
BUILD="${RESULTS}/eqbuild"
mkdir -p "$BUILD"

# 클래스패스: 런타임 fat jar + Spark 의 hadoop 계열
CP="${BASELINE_JAR}:${SPARK_HOME}/jars/*"

echo
echo "### AddEqualityDeletes 컴파일"
javac -nowarn -cp "$CP" -d "$BUILD" ./tools/AddEqualityDeletes.java

for T in dv.g.eq0 dv.g.eq50; do
  echo
  echo "### $T 에 equality delete 추가 (id % ${EQ_MODULUS} == 0)"
  # 네임스페이스는 g, 카탈로그(dv)는 HadoopCatalog 가 warehouse 경로로 대신한다
  java -cp "${BUILD}:${CP}" AddEqualityDeletes \
    "$WAREHOUSE" "g.${T#dv.g.}" "$TOTAL" "$EQ_MODULUS"
done

echo
echo "✅ 완료"
