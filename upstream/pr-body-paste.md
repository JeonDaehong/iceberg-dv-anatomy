`ColumnarBatchUtil.buildRowIdMapping` and `buildIsDeleted` call `PositionDeleteIndex.isDeleted(pos)` once for every row in a batch. Positions within a batch are a contiguous ascending range, so every call repeats the same work: extracting the high and low keys, bounds-checking the bitmap array, and binary searching the container array. None of it is amortized across the batch even though every call lands in the same one or two containers.

This adds `PositionDeleteIndex.forEachInRange(posStart, length, consumer)`, which traverses the deleted positions in a range in ascending order. The default implementation falls back to the per-position loop, and `BitmapPositionDeleteIndex` overrides it to resolve the range to at most two underlying 32-bit bitmaps and walk each once. `ColumnarBatchUtil` takes that path when the scan has no equality deletes; tables with equality deletes keep the existing loop unchanged.

Measured on 8M rows across 4 files, one column projected, 3 repetitions per configuration in separate JVMs (delete-check CPU samples, async-profiler `ctimer`):

| delete density | container | current | patched | reduction |
|---|---|---|---|---|
| 0.5% | array | 674 | 79 | 8.6x |
| 6.1% | array | 873 | 94 | 9.3x |
| 7.0% | bitmap | 420 | 113 | 3.7x |
| 8.0% | bitmap | 499 | 142 | 3.5x |
| 50% | bitmap | 584 | 225 | 2.6x |

The gain is largest just below the array/bitmap boundary, which is where the removed work — a binary search per row — was most expensive. `buildIsDeleted` improves 14.9x–18.9x since it does not need to fill the gaps between deleted positions.

Correctness was checked before performance: `count(*)`, `sum(id)` and `min/max(id)` over 7 tables are identical for both jars, and tables with equality deletes show no regression.

This touches `spark/v4.2` only, to keep the diff reviewable. `ColumnarBatchUtil` is byte-identical in v3.5, v4.0 and v4.1, so I will backfill those in a follow-up if this direction looks right.

Closes #18026, which has the rest of the measurements — portability across three CPU microarchitectures, local disk and S3, warm and dropped page cache, task parallelism 1 through 16, a distributed cluster, and hardware counters — along with the conditions under which the numbers above do not hold.
