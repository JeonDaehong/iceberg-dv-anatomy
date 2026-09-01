#!/usr/bin/env python3
"""
Phase 0 스캔 워크로드.

측정 대상은 "DV 체크가 전체 스캔 CPU 에서 차지하는 비중" 이므로,
스캔이 JVM 수명의 대부분을 차지하도록 다회 반복한다.
(async-profiler 는 JVM 시작 시점부터 붙으므로, 기동 오버헤드는 분모에 섞인다.
 이는 DV 비중을 과소평가하는 방향 = go/no-go 게이트에서 안전한 방향이다.)

노트:
  - count(*) 는 메타데이터로 최적화될 수 있어 쓰지 않는다.
  - noop 싱크는 컬럼을 실제로 물질화시키면서 I/O 는 발생시키지 않는다.
  - 좁은 스캔(1컬럼)과 넓은 스캔(20컬럼)을 모두 재서 DV 비중의 '범위'를 얻는다.
    DV 체크 비용은 컬럼 수와 무관하게 행당 1회이므로, 비중은 컬럼 수에 반비례한다.
"""
import argparse
import json
import subprocess
import time

from pyspark.sql import SparkSession

ALL_COLS = [
    "id", "k01", "k02", "k03", "k04", "d05", "d06", "s07", "s08", "s09",
    "s10", "t11", "t12", "k13", "k14", "k15", "d16", "s17", "b18", "ts19",
]


def build_spark(warehouse, args):
    return (
        SparkSession.builder.appName(f"dv-anatomy-phase0-scan-{args.label}")
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
        # 벡터화 리더 강제 — 이게 꺼지면 ColumnarBatchUtil 경로를 안 타고
        # 행 기반 DeleteFilter 경로로 빠져서 측정 대상이 달라진다.
        .config("spark.sql.iceberg.vectorization.enabled", "true")
        .config("spark.sql.iceberg.parquet.reader-type", "ICEBERG")
        .getOrCreate()
    )


def check_vectorized(df, label):
    plan = df._jdf.queryExecution().executedPlan().toString()
    columnar = "ColumnarToRow" in plan or "columnar" in plan.lower()
    print(f"[{label}] 벡터화 리더: {'ON (추정)' if columnar else '*** OFF 의심 ***'}")
    if not columnar:
        print(
            "  경고: 실행 계획에 columnar 흔적이 없습니다. 행 기반 경로일 수 있고,\n"
            "        그러면 ColumnarBatchUtil 대신 DeleteFilter 가 잡힙니다.\n"
            "        attribute.py 결과의 frame 이름으로 최종 확인하세요.\n"
        )
    return columnar


def drop_caches():
    """반복 사이에 페이지 캐시를 비운다.

    왜 필요한가: 테이블 825 MB / RAM 15 GiB 이므로 평소 측정은 캐시가 완전히 따뜻하고,
    측정 구간의 디스크 I/O 가 사실상 0 이다. 즉 우리가 보고하는 'DV 가 스캔 CPU 의 N%'
    는 순수 CPU-bound 조건의 값이다. 캐시를 비우면 I/O 가 분모에 들어와
    같은 절대 비용이 훨씬 작은 비중으로 보인다 — 그 차이가 곧
    "클러스터/스토리지를 바꾸면 이 결과가 얼마나 흔들리나" 의 답이다.

    한계: 로컬 디스크 콜드 리드는 S3 의 대리 지표일 뿐이다. S3 는 지연이 훨씬 크고
    병렬 프리페치가 있어 양상이 다르다. 여기서는 'I/O 가 분모에 들어온 경우' 만 본다.
    """
    subprocess.run(["sync"], check=False)
    with open("/proc/sys/vm/drop_caches", "w") as fh:
        fh.write("3")


def run(spark, table, ncols, warmup, iters, label, is_deleted=False, batch_size=None,
        cold=False, col_list=None):
    # 기본은 앞에서부터 ncols 개. 그런데 ALL_COLS 는 타입이 섞여 있고 순서가 고정이라
    # "컬럼 수" 를 늘리면 "디코딩 비용" 도 같이 늘어난다 — 앞 5개는 전부 정수이고
    # 첫 문자열은 8번째다 (F-016). 두 축을 분리하려면 컬럼을 이름으로 골라야 한다.
    cols = list(col_list) if col_list else ALL_COLS[:ncols]
    # _deleted 메타데이터 컬럼을 투영하면 리더가 다른 분기를 탄다:
    #   BaseBatchReader.filterBatch -> hasIsDeletedColumn -> buildIsDeleted
    # (평소에는 buildRowIdMapping). 삭제 행을 지우는 대신 표시만 하므로
    # 내보내는 행 수도 달라진다 — 그래서 별도 비교군으로만 쓴다.
    if is_deleted:
        cols = cols + ["_deleted"]
    # 배치 크기는 세션 conf 가 없다. SparkReadConf.parquetBatchSize() 는
    #   .option("batch-size") -> 테이블 속성 read.parquet.vectorization.batch-size -> 기본 5000
    # 순으로만 본다. 그래서 DataFrameReader 옵션으로 넣는다.
    # (테이블 속성을 쓰면 다른 실험에 영향이 남는다.)
    if batch_size:
        df = spark.read.option("batch-size", str(batch_size)).table(table).select(*cols)
    else:
        df = spark.table(table).select(*cols)
    check_vectorized(df, label)

    def once():
        if cold:
            drop_caches()          # 측정 시작 '전에' 비운다. 비우는 시간은 안 잰다.
        t0 = time.perf_counter()
        df.write.format("noop").mode("overwrite").save()
        return time.perf_counter() - t0

    for i in range(warmup):
        d = once()
        print(f"  warmup {i+1}/{warmup}: {d:.3f}s")

    times = []
    for i in range(iters):
        d = once()
        times.append(d)
        print(f"  iter   {i+1}/{iters}: {d:.3f}s")

    times.sort()
    n = len(times)
    stats = {
        "label": label,
        "table": table,
        "n_cols": len(cols),
        "cols": cols,
        "batch_size": batch_size,
        "cold": cold,
        "iters": iters,
        "min_s": times[0],
        "median_s": times[n // 2],
        "max_s": times[-1],
        "mean_s": sum(times) / n,
    }
    print(
        f"[{label}] median={stats['median_s']:.3f}s  "
        f"min={stats['min_s']:.3f}s  max={stats['max_s']:.3f}s"
    )
    return stats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--warehouse", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--label", required=True)
    p.add_argument(
        "--cols",
        type=int,
        required=True,
        help="선택할 컬럼 수. 프로파일 귀속을 깨끗하게 하려고 폭마다 별도 JVM 으로 실행한다.",
    )
    p.add_argument(
        "--col-list",
        default=None,
        help="투영할 컬럼을 이름으로 직접 지정 (쉼표 구분). 주면 --cols 를 무시한다. "
             "컬럼 수와 컬럼 타입을 분리해 재기 위한 것이다 (F-016).",
    )
    p.add_argument(
        "--is-deleted",
        action="store_true",
        help="_deleted 메타데이터 컬럼을 함께 투영해 buildIsDeleted 경로를 태운다.",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="벡터화 배치 크기. 생략하면 Iceberg 기본값 5000 을 그대로 쓴다.",
    )
    p.add_argument(
        "--cold",
        action="store_true",
        help="매 반복 전에 페이지 캐시를 비운다 (root 필요). I/O 가 분모에 들어온 경우를 본다.",
    )
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--iters", type=int, default=12)
    p.add_argument("--cores", default="4")
    p.add_argument("--driver-mem", default="8g")
    p.add_argument("--out-json", default=None)
    args = p.parse_args()

    col_list = None
    if args.col_list:
        col_list = [c.strip() for c in args.col_list.split(",") if c.strip()]
        unknown = [c for c in col_list if c not in ALL_COLS]
        if unknown:
            raise SystemExit("알 수 없는 컬럼: %s\n사용 가능: %s"
                             % (", ".join(unknown), ", ".join(ALL_COLS)))

    spark = build_spark(args.warehouse, args)
    spark.sparkContext.setLogLevel("WARN")

    mode = " + _deleted" if args.is_deleted else ""
    bs = f", 배치 {args.batch_size}" if args.batch_size else ", 배치 기본(5000)"
    bs += " · 콜드 캐시" if args.cold else " · 웜 캐시"
    shown = ",".join(col_list) if col_list else f"{args.cols} 컬럼"
    print(f"\n=== 스캔: {args.table}  ({shown}{mode}{bs}) ===")
    stats = run(spark, args.table, args.cols, args.warmup, args.iters, args.label,
                is_deleted=args.is_deleted, batch_size=args.batch_size,
                cold=args.cold, col_list=col_list)
    stats["is_deleted"] = args.is_deleted

    if args.out_json:
        with open(args.out_json, "w") as fh:
            json.dump(stats, fh, indent=2)

    spark.stop()


if __name__ == "__main__":
    main()
