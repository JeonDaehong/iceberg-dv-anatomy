#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""정렬 전후 테이블의 **컬럼별 저장 바이트**를 Iceberg .files 메타데이터에서 뽑는다.

왜 필요한가:
  정렬 테이블의 데이터 파일이 2.64% 더 컸다 (W3 예측과 반대). 같은 데이터인데 커졌으니
  통제 실패이거나, 설명 가능한 기전이 있거나 둘 중 하나다. 컬럼별로 쪼개면 갈린다.

가설: repartitionByRange(k04) 는 행을 k04 기준으로 재분배한다. 그런데 원래 테이블에서
      id 는 파티션 안에서 **완벽한 등차수열**이라 델타/RLE 인코딩이 극단적으로 잘 먹었다.
      k04 로 재분배하면 id 가 흩어지므로 그 이득이 날아간다.
      => id 와 id 파생 컬럼(k13)만 크게 늘고, 정렬 키 k04 는 오히려 줄어야 한다.
"""
import sys
from pyspark.sql import SparkSession

def main():
    wh, tables = sys.argv[1], sys.argv[2:]
    spark = (SparkSession.builder.appName("colsize")
             .config("spark.sql.catalog.dv", "org.apache.iceberg.spark.SparkCatalog")
             .config("spark.sql.catalog.dv.type", "hadoop")
             .config("spark.sql.catalog.dv.warehouse", wh)
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")

    out = {}
    fields = None
    for t in tables:
        df = spark.sql("SELECT readable_metrics FROM %s.files" % t)
        rows = df.collect()
        agg = {}
        for r in rows:
            m = r["readable_metrics"].asDict()
            for col, v in m.items():
                d = v.asDict() if hasattr(v, "asDict") else v
                agg[col] = agg.get(col, 0) + (d.get("column_size") or 0)
        out[t] = agg
        fields = fields or sorted(agg)

    a, b = tables[0], tables[1]
    print("\n%-8s %14s %14s %10s %12s" % ("컬럼", a.split(".")[-1], b.split(".")[-1], "배율", "차이 B"))
    print("-" * 64)
    rowsout = []
    for c in fields:
        x, y = out[a].get(c, 0), out[b].get(c, 0)
        if x == 0 and y == 0: continue
        rowsout.append((y - x, c, x, y))
    for delta, c, x, y in sorted(rowsout, reverse=True):
        print("%-8s %14s %14s %10s %+12s"
              % (c, f"{x:,}", f"{y:,}", ("%.3f" % (y/x)) if x else "-", f"{delta:,}"))
    tx, ty = sum(out[a].values()), sum(out[b].values())
    print("-" * 64)
    print("%-8s %14s %14s %10.4f %+12s" % ("합계", f"{tx:,}", f"{ty:,}", ty/tx, f"{ty-tx:,}"))
    spark.stop()

main()
