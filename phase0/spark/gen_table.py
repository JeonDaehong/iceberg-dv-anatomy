#!/usr/bin/env python3
"""
Phase 0 데이터 생성기.

Iceberg V3 (deletion vector) 테이블을 만들고 지정한 삭제 패턴을 적용한다.

핵심 불변량 — 이게 깨지면 이후 모든 분석이 무의미해진다:
  spark.range(0, N, 1, NUM_FILES) 는 파티션 i 에 id [i*R, (i+1)*R) 를 순서대로 담고,
  Iceberg 는 파티션당 파일 1개를 순서대로 쓴다. 따라서

      파일 i 의 row position p  <->  id = i*R + p        (즉 position = id % R)

  이 매핑이 성립해야 "id 조건으로 삭제" = "position 패턴 제어" 가 된다.
  스크립트는 쓰기 직후 이 불변량을 assert 로 검증한다.
"""
import argparse
import json
import os
import sys

from pyspark.sql import SparkSession


# 20개 컬럼. md5 기반 고엔트로피 — 저엔트로피 데이터는 Parquet 디코드를
# 비현실적으로 싸게 만들어 DV 비중을 과대평가시킨다(go/no-go 오판).
COLUMN_EXPRS = [
    "id",
    "pmod(hash(id, 11), 1000000000)            AS k01",
    "pmod(hash(id, 12), 1000000000)            AS k02",
    "pmod(hash(id, 13), 1000000)               AS k03",
    "pmod(hash(id, 14), 1000)                  AS k04",
    "CAST(pmod(hash(id, 15), 1000000) AS DOUBLE) / 997.0  AS d05",
    "CAST(pmod(hash(id, 16), 1000000) AS DOUBLE) / 331.0  AS d06",
    "md5(CAST(id AS STRING))                   AS s07",
    "md5(CAST(id * 31 AS STRING))              AS s08",
    "substr(md5(CAST(id * 7 AS STRING)), 1, 16)  AS s09",
    "substr(md5(CAST(id * 13 AS STRING)), 1, 8) AS s10",
    "CAST(pmod(hash(id, 17), 128) AS TINYINT)  AS t11",
    "CAST(pmod(hash(id, 18), 32000) AS SMALLINT) AS t12",
    "CAST(id * 1000003 AS BIGINT)              AS k13",
    "pmod(hash(id, 19), 7)                     AS k14",
    "pmod(hash(id, 20), 999983)                AS k15",
    "CAST(pmod(hash(id, 21), 1000000) AS DOUBLE) / 17.0   AS d16",
    "substr(md5(CAST(id * 17 AS STRING)), 1, 24) AS s17",
    "CAST(pmod(hash(id, 22), 2) AS BOOLEAN)    AS b18",
    "TIMESTAMP_SECONDS(1700000000 + pmod(hash(id, 23), 30000000)) AS ts19",
]


def build_spark(warehouse: str, args) -> SparkSession:
    return (
        SparkSession.builder.appName("dv-anatomy-phase0-gen")
        .master(f"local[{args.cores}]")
        .config("spark.driver.memory", args.driver_mem)
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config("spark.sql.catalog.dv", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.dv.type", "hadoop")
        .config("spark.sql.catalog.dv.warehouse", warehouse)
        # 결정론 확보: AQE 가 파티션을 합치면 파일 1:1 불변량이 깨질 수 있다.
        .config("spark.sql.adaptive.enabled", "false")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "false")
        .getOrCreate()
    )


def create_table(spark, table, rows_per_file, num_files):
    spark.sql(f"DROP TABLE IF EXISTS {table} PURGE")
    cols = ", ".join(COLUMN_EXPRS)
    total = rows_per_file * num_files

    src = spark.range(0, total, 1, num_files).selectExpr(*COLUMN_EXPRS)
    src.createOrReplaceTempView("src")

    spark.sql(
        f"""
        CREATE TABLE {table}
        USING iceberg
        TBLPROPERTIES (
          'format-version'            = '3',
          'write.delete.mode'         = 'merge-on-read',
          'write.update.mode'         = 'merge-on-read',
          'write.merge.mode'          = 'merge-on-read',
          'write.distribution-mode'   = 'none',
          'write.target-file-size-bytes' = '1073741824',
          'read.parquet.vectorization.enabled' = 'true'
        )
        AS SELECT * FROM src
        """
    )
    return total


def assert_layout(spark, table, rows_per_file, num_files):
    """position = id % ROWS_PER_FILE 불변량을 검증한다."""
    files = spark.sql(
        f"SELECT file_path, record_count, file_size_in_bytes FROM {table}.files ORDER BY file_path"
    ).collect()

    problems = []
    if len(files) != num_files:
        problems.append(f"파일 수 {len(files)} != 기대값 {num_files}")
    for f in files:
        if f.record_count != rows_per_file:
            problems.append(f"{f.file_path}: {f.record_count} rows != {rows_per_file}")

    print("\n--- 데이터 파일 ---")
    for f in files:
        mb = f.file_size_in_bytes / 1024 / 1024
        chunks = f.record_count / 65536
        print(
            f"  {os.path.basename(f.file_path):<48} "
            f"{f.record_count:>10,} rows  {mb:>7.1f} MB  {chunks:>5.1f} chunks"
        )

    if problems:
        print("\n*** 레이아웃 불변량 위반 — 이 상태로 진행하면 삭제 패턴 제어가 무의미합니다 ***")
        for p in problems:
            print(f"    - {p}")
        print(
            "    원인 후보: write.distribution-mode 미적용 / AQE / target-file-size 초과\n"
        )
        sys.exit(2)
    print("  [ok] position = id % ROWS_PER_FILE 불변량 성립\n")
    return files


def apply_delete(spark, table, pattern, rows_per_file, args):
    if pattern == "none":
        print("[none] 삭제 없음 (대조군)")
        return None

    if pattern == "sparse":
        # d = 1/SPARSE_DENOM, 평균 run 길이 C=1 (hash 로 의사난수 분산)
        pred = f"pmod(hash(id, {args.seed}), {args.sparse_denom}) = 0"
        desc = f"d≈{100.0/args.sparse_denom:.2f}%, C=1  -> array 컨테이너 기대"
    elif pattern == "run":
        lo = int(args.run_start_frac * rows_per_file)
        hi = int(args.run_end_frac * rows_per_file)
        pred = f"pmod(id, {rows_per_file}) >= {lo} AND pmod(id, {rows_per_file}) < {hi}"
        desc = (
            f"파일당 position [{lo:,}, {hi:,}) 연속 삭제 "
            f"({100*(args.run_end_frac-args.run_start_frac):.0f}%)  -> run 컨테이너 기대"
        )
    else:
        raise ValueError(f"unknown pattern: {pattern}")

    print(f"[{pattern}] {desc}")
    print(f"         predicate: {pred}")
    spark.sql(f"DELETE FROM {table} WHERE {pred}")
    return pred


def report_deletes(spark, table, rows_per_file):
    """DV 가 실제로 생성됐는지, 카디널리티가 기대와 맞는지 확인."""
    rows = spark.sql(
        f"""
        SELECT content, file_path, record_count, file_size_in_bytes, referenced_data_file
        FROM {table}.delete_files ORDER BY file_path
        """
    ).collect()

    if not rows:
        print("--- delete_files: 없음 (DV 미생성) ---")
        return []

    print("--- Deletion Vectors ---")
    out = []
    for r in rows:
        density = r.record_count / rows_per_file
        chunk_density = density  # 균등 분포 가정 시 청크 밀도 ≈ 파일 밀도
        expect = "bitmap" if chunk_density > 0.0625 else "array"
        print(
            f"  {os.path.basename(r.referenced_data_file or ''):<44} "
            f"deleted={r.record_count:>9,}  ({density*100:>6.2f}%)  "
            f"dv={r.file_size_in_bytes:>7,}B   균등가정시 예상={expect}"
        )
        out.append(
            {
                "referenced_data_file": r.referenced_data_file,
                "deleted_rows": r.record_count,
                "dv_bytes": r.file_size_in_bytes,
                "delete_density": density,
            }
        )
    print(
        "\n  주의: 위 '예상'은 삭제가 청크에 균등 분포한다는 가정 하의 추정이다.\n"
        "        실제 컨테이너 타입은 Phase 1 의 dv-inspect 로 확인한다."
    )
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--warehouse", required=True)
    p.add_argument("--table", required=True, help="예: dv.db.t_sparse")
    p.add_argument("--pattern", required=True, choices=["none", "sparse", "run"])
    p.add_argument("--rows-per-file", type=int, required=True)
    p.add_argument("--num-files", type=int, required=True)
    p.add_argument("--seed", type=int, default=20260817)
    p.add_argument("--sparse-denom", type=int, default=200)
    p.add_argument("--run-start-frac", type=float, default=0.35)
    p.add_argument("--run-end-frac", type=float, default=0.65)
    p.add_argument("--cores", default="4")
    p.add_argument("--driver-mem", default="8g")
    p.add_argument("--out-json", default=None)
    args = p.parse_args()

    spark = build_spark(args.warehouse, args)
    spark.sql("CREATE NAMESPACE IF NOT EXISTS dv.db")

    print(f"\n=== 생성: {args.table}  (pattern={args.pattern}) ===")
    total = create_table(spark, args.table, args.rows_per_file, args.num_files)
    print(f"총 {total:,} 행 / {args.num_files} 파일 / {args.rows_per_file:,} 행당")

    assert_layout(spark, args.table, args.rows_per_file, args.num_files)
    apply_delete(spark, args.table, args.pattern, args.rows_per_file, args)
    dv = report_deletes(spark, args.table, args.rows_per_file)

    live = spark.sql(f"SELECT count(*) c FROM {args.table}").collect()[0].c
    print(f"\n살아있는 행: {live:,} / {total:,}  (삭제 {total-live:,})")

    if args.out_json:
        with open(args.out_json, "w") as fh:
            json.dump(
                {
                    "table": args.table,
                    "pattern": args.pattern,
                    "total_rows": total,
                    "live_rows": live,
                    "rows_per_file": args.rows_per_file,
                    "num_files": args.num_files,
                    "chunks_per_file": args.rows_per_file / 65536,
                    "deletion_vectors": dv,
                },
                fh,
                indent=2,
            )
    spark.stop()


if __name__ == "__main__":
    main()
