#!/usr/bin/env python3
"""
패치본이 베이스라인과 '같은 행'을 읽는지 확인한다.

성능 숫자보다 이게 먼저다. 빨라졌는데 답이 다르면 아무 의미가 없다.

count(*) 하나로는 부족하다 — Iceberg 가 메타데이터만 보고 답할 수 있고,
행 개수가 같아도 '다른 행'을 살릴 수 있기 때문이다. 그래서:
  count(*)        살아남은 행 수
  sum(id)         어떤 행이 살아남았는지에 민감 (id 는 파일 내 위치와 1:1)
  min/max(id)     경계 행이 잘못 잘렸는지
세 값이 모두 같아야 통과다.
"""
import argparse
import json

from pyspark.sql import SparkSession


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--tables", required=True, help="쉼표로 구분")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--is-deleted", action="store_true",
                    help="_deleted 메타컬럼을 투영해 buildIsDeleted 경로를 검증한다.")
    ap.add_argument("--cores", default="4")
    ap.add_argument("--driver-mem", default="8g")
    args = ap.parse_args()

    spark = (
        SparkSession.builder.appName(f"dv-verify-{args.label}")
        .config("spark.sql.catalog.dv", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.dv.type", "hadoop")
        .config("spark.sql.catalog.dv.warehouse", args.warehouse)
        .config("spark.sql.iceberg.vectorization.enabled", "true")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    out = {"label": args.label, "tables": {}}
    for table in args.tables.split(","):
        table = table.strip()
        if not table:
            continue
        if args.is_deleted:
            # _deleted 를 투영하면 삭제 행이 '제거' 대신 '표시' 된다.
            #   n  = 삭제 포함 전체 행 (원본 행 수와 같아야 한다)
            #   s  = 살아있는 행의 id 합 (평소 모드의 sum(id) 와 같아야 한다)
            #   nd = 삭제로 표시된 행 수
            row = spark.sql(
                f"""SELECT count(*) AS n,
                           sum(CASE WHEN _deleted THEN 0 ELSE id END) AS s,
                           sum(CASE WHEN _deleted THEN 1 ELSE 0 END) AS nd,
                           min(id) AS lo, max(id) AS hi
                    FROM {table}"""
            ).collect()[0]
        else:
            row = spark.sql(
                f"SELECT count(*) AS n, sum(id) AS s, min(id) AS lo, max(id) AS hi FROM {table}"
            ).collect()[0]
        # 벡터화 경로를 정말 탔는지 확인 (행 경로로 폴백했다면 비교 자체가 무의미)
        plan = spark.sql(f"SELECT id FROM {table}")._jdf.queryExecution().executedPlan().toString()
        out["tables"][table] = {
            "count": int(row["n"]),
            "sum_id": int(row["s"]) if row["s"] is not None else None,
            "min_id": int(row["lo"]) if row["lo"] is not None else None,
            "max_id": int(row["hi"]) if row["hi"] is not None else None,
            "columnar": "ColumnarToRow" in plan,
        }
        if args.is_deleted:
            out["tables"][table]["n_deleted"] = int(row["nd"])
        print(f"  {table}: count={row['n']:,} sum={row['s']}"
              + (f" deleted={row['nd']:,}" if args.is_deleted else ""))

    with open(args.out_json, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"-> {args.out_json}")
    spark.stop()


if __name__ == "__main__":
    main()
