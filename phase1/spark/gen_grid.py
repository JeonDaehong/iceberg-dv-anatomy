#!/usr/bin/env python3
"""
Phase 1 격자 생성기 — 목표 청크 밀도 d 로 DV 를 만든다.

삭제 술어:  pmod(hash(id, SEED), 10000) < density_bp

hash 는 murmur3 라 id 에 대해 균일하게 흩어지므로, 파일 전체 밀도뿐 아니라
'청크(65536 position) 단위 밀도'도 목표값 근처에 모인다. F-006 에서 실측 확인함
(목표 0.5% -> 청크별 0.456~0.581%).

스키마와 파일 레이아웃은 phase0/spark/gen_table.py 와 의도적으로 동일하다.
Phase 0 결과와 직접 비교하기 위해서다. 바꾸려면 양쪽을 함께 바꿔야 한다.
"""
import argparse
import json
import os
import sys

from pyspark.sql import SparkSession

CHUNK = 65536

# phase0/spark/gen_table.py 와 동일해야 한다.
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


def build_spark(warehouse, args):
    return (
        SparkSession.builder.appName(f"dv-p1-gen-{args.density_bp}")
        .master(f"local[{args.cores}]")
        .config("spark.driver.memory", args.driver_mem)
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config("spark.sql.catalog.dv", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.dv.type", "hadoop")
        .config("spark.sql.catalog.dv.warehouse", warehouse)
        .config("spark.sql.adaptive.enabled", "false")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "false")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--warehouse", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--density-bp", type=int, required=True,
                   help="목표 청크 밀도, basis point of 10000 (625 = 6.25%%)")
    p.add_argument("--run-length", type=int, default=1,
                   help="평균 run 길이 L. 1 이면 완전 랜덤(C=1). "
                        "L>1 이면 position 을 L 크기 블록으로 묶어 블록 단위로 삭제한다.")
    p.add_argument("--occupancy-bp", type=int, default=10000,
                   help="청크 점유율 p, basis point of 10000. 10000 이면 모든 청크. "
                        "낮추면 일부 청크에만 삭제가 생겨 컨테이너 개수가 줄어든다. "
                        "청크 '안'의 밀도는 --density-bp 로 유지되므로 컨테이너 타입은 안 바뀐다.")
    p.add_argument("--rows-per-file", type=int, required=True)
    p.add_argument("--num-files", type=int, required=True)
    p.add_argument("--seed", type=int, default=20260817)
    p.add_argument("--cores", default="4")
    p.add_argument("--driver-mem", default="8g")
    p.add_argument("--out-json", default=None)
    args = p.parse_args()

    spark = build_spark(args.warehouse, args)
    spark.sparkContext.setLogLevel("ERROR")
    spark.sql("CREATE NAMESPACE IF NOT EXISTS dv.g")

    total = args.rows_per_file * args.num_files
    density = args.density_bp / 10000.0

    print(f"\n=== {args.table}   d={density*100:.2f}%  ({args.density_bp} bp) ===")

    spark.sql(f"DROP TABLE IF EXISTS {args.table} PURGE")
    # 파티션 i = id [i*R, (i+1)*R) 를 순서대로 -> position = id % R (phase0 와 동일 불변량)
    spark.range(0, total, 1, args.num_files).selectExpr(*COLUMN_EXPRS) \
         .createOrReplaceTempView("src")
    spark.sql(
        f"""
        CREATE TABLE {args.table} USING iceberg
        TBLPROPERTIES (
          'format-version'='3',
          'write.delete.mode'='merge-on-read',
          'write.update.mode'='merge-on-read',
          'write.merge.mode'='merge-on-read',
          'write.distribution-mode'='none',
          'write.target-file-size-bytes'='1073741824',
          'read.parquet.vectorization.enabled'='true'
        ) AS SELECT * FROM src
        """
    )

    files = spark.sql(
        f"SELECT record_count FROM {args.table}.files").collect()
    bad = [f.record_count for f in files if f.record_count != args.rows_per_file]
    if len(files) != args.num_files or bad:
        print(f"*** 레이아웃 불변량 위반: 파일 {len(files)}개, 이상 record_count={bad}")
        sys.exit(2)

    # C 축: position 을 L 크기 블록으로 묶어 '블록 단위'로 삭제하면
    # 평균 run 길이가 L 이 된다. L=1 이면 기존의 완전 랜덤과 동일하다.
    #
    # position = id % ROWS_PER_FILE (레이아웃 불변량, 위에서 검증됨)
    L = args.run_length
    if L <= 1:
        key = f"id"
    else:
        key = f"(pmod(id, {args.rows_per_file}) div {L})"
    pred = f"pmod(hash({key}, {args.seed}), 10000) < {args.density_bp}"

    # p 축: 청크 자체를 켜고 끈다. 청크 '안'의 밀도는 그대로이므로
    # 컨테이너 '타입'은 유지되고 컨테이너 '개수'만 줄어든다.
    # -> RoaringArray 최상위 이진 탐색 비용만 분리해서 볼 수 있다.
    if args.occupancy_bp < 10000:
        chunk_key = f"(pmod(id, {args.rows_per_file}) div {CHUNK})"
        pred = (f"pmod(hash({chunk_key}, {args.seed + 1}), 10000) < {args.occupancy_bp}"
                f" AND {pred}")

    print(f"  predicate: {pred}")
    print(f"  L={L}  p={args.occupancy_bp/100:.1f}%  "
          f"->  기대 nRuns/청크 ≈ {CHUNK * density / L:,.0f}, "
          f"기대 컨테이너 ≈ {args.num_files * (args.rows_per_file/CHUNK) * args.occupancy_bp/10000:,.0f}")
    spark.sql(f"DELETE FROM {args.table} WHERE {pred}")

    dv = spark.sql(
        f"""SELECT record_count, file_size_in_bytes, referenced_data_file
            FROM {args.table}.delete_files ORDER BY referenced_data_file"""
    ).collect()
    deleted = sum(r.record_count for r in dv)
    dv_bytes = sum(r.file_size_in_bytes for r in dv)
    actual = deleted / total if total else 0

    print(f"  삭제 {deleted:,} / {total:,}  (실제 {actual*100:.3f}%, 목표 {density*100:.2f}%)")
    print(f"  DV {len(dv)}개  {dv_bytes:,}B   삭제행당 {dv_bytes*8/max(deleted,1):.4f} bit")

    if args.out_json:
        with open(args.out_json, "w") as fh:
            json.dump({
                "table": args.table,
                "density_bp": args.density_bp,
                "run_length": L,
                "occupancy_bp": args.occupancy_bp,
                "target_density": density,
                "actual_density": actual,
                "total_rows": total,
                "deleted_rows": deleted,
                "dv_count": len(dv),
                "dv_bytes": dv_bytes,
                "rows_per_file": args.rows_per_file,
                "num_files": args.num_files,
                "chunks_per_file": args.rows_per_file / CHUNK,
                "expected_chunk_cardinality": CHUNK * density,
            }, fh, indent=2)

    spark.stop()


if __name__ == "__main__":
    main()
