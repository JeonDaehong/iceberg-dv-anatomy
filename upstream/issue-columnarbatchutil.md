Spark: vectorized reads probe the position delete index once per row, ignoring that batch positions are contiguous

---

### Summary

`ColumnarBatchUtil.buildRowIdMapping` and `buildIsDeleted` call `PositionDeleteIndex.isDeleted(pos)` once for every row in a batch. Positions within a batch are a contiguous ascending range and the DV-backed index is a Roaring bitmap, so the same information can be obtained with a single range traversal instead of `batchSize` independent probes.

On a V3 table read with a narrow projection, this loop accounts for **43–53% of scan CPU**, depending on delete density. I implemented the change against `apache-iceberg-1.11.0` and measured it end to end: the delete-check CPU drops by **3.1x–9.3x** for `buildRowIdMapping` and **14.9x–18.9x** for `buildIsDeleted`, and the scan subtree as a whole drops by **12–45%**. Tables with equality deletes keep the existing loop and show no regression.

`RoaringBitmap` already provides the range APIs needed (`forEachInRange`, `forAllInRange`, `rangeCardinality`), and they are present in the version Iceberg pins (`1.6.14`), including the shaded copy in `iceberg-spark-runtime`.

I have a working branch with the change, updated tests, and the measurements below. Happy to open a PR if the direction sounds reasonable.

### Where

`spark/v4.0/spark/src/main/java/org/apache/iceberg/spark/data/vectorized/ColumnarBatchUtil.java` (same shape in v3.4/v3.5/v4.1):

```java
// :57 buildRowIdMapping
PositionDeleteIndex deletedPositions = deletes.deletedRowPositions();   // :66
for (int rowId = 0; rowId < batchSize; rowId++) {                       // :72
  long pos = rowStartPosInBatch + rowId;
  ...
  if (isDeleted(pos, row, deletedPositions, eqDeleteFilter)) { ... }
}

// :110 buildIsDeleted — same loop shape at :125

// :137
private static boolean isDeleted(...) {
  if (deletedPositions != null && deletedPositions.isDeleted(pos)) {     // :143
```

### Why it costs what it does

`isDeleted(pos)` resolves to `BitmapPositionDeleteIndex.isDeleted` → `RoaringPositionBitmap.contains` → `RoaringBitmap.contains`, and each call independently:

1. extracts the high/low key and bounds-checks the `RoaringBitmap[]` (`RoaringPositionBitmap.contains`),
2. binary searches the top-level container array (`RoaringArray.getIndex` → `binarySearch`),
3. searches within the container.

None of that is amortized across the batch even though every call lands in the same one or two containers.

The cost also depends heavily on which container the deletes materialized into, which is a function of delete density — `ArrayContainer` below 4096 entries per 65536-position chunk, `BitmapContainer` above it:

| chunk density | cardinality | container | per-row `contains` |
|---|---|---|---|
| 0.5% | 327 | array | 13.0 ns |
| 5% | 3,276 | array | 16.0 ns |
| 12% | 7,864 | bitmap | 4.0 ns |
| contiguous 30% | 19,661 | run | 4.6 ns |

So the sparse-delete case that CDC workloads spend most of their time in is also the most expensive per row, because it lands on array containers and pays a binary search per probe.

### Evidence — cost as a function of delete density

Spark 4.0.4, Iceberg 1.11.0, JDK 17, `format-version=3`, MoR. 8M rows across 4 files (~30 Roaring chunks per file). async-profiler, `ctimer`. Scan materialized through the `noop` sink; the vectorized reader was confirmed active in every run. Each configuration profiled **3 times in separate JVMs**; the run-to-run spread of this measurement is 9.7% median (see *Caveats*), so ranges are reported and differences inside that band are not claimed.

`buildRowIdMapping` probes `batchSize` times per batch regardless of density, and the total scanned row count is fixed, so the sample count is directly proportional to per-probe cost.

| delete density | dominant container | delete-check CPU samples | share of scan subtree |
|---|---|---|---|
| 0% (control) | — | **0** | 0.00% |
| 0.5% | array | 599 (576–633) | 44.2% |
| 5% | array | 746 (700–798) | 47.5% |
| **6.1%** | array | **872 (838–909)** | **53.2%** |
| 6.25% | mixed | 654 (617–702) | 46.2% |
| 7.0% | bitmap | 394 (381–406) | 33.6% |
| 12% | bitmap | 422 (410–447) | 36.3% |
| 50% | bitmap | 496 (454–529) | 39.6% |

The control table with no deletes shows exactly zero samples attributed to the delete path, so the attribution has no false positives.

Two things worth noting:

- The worst case is **just below the array→bitmap boundary** (~6.1% deletes per chunk), not at high delete rates. Deleting *more* rows can make the per-row check cheaper.
- With a wide projection (20 columns) the same table shows 3.75%, because the check is per row regardless of how many columns are read. **The 40–53% figures are specific to narrow projections.**

Wall clock, 1 column, median of 12 iterations: a table with 0.5% deletes reads **13.0% slower** than the same table with no deletes at all — despite having 0.5% fewer rows to emit.

### Evidence — with the change applied

I cloned `apache-iceberg-1.11.0` (commit `6976e02`), implemented the change, and re-measured the **same tables** with only the jar swapped. Both jars were built from the same tree — the baseline is the same source with the patch stashed — so the only difference between them is the diff.

Correctness was checked first: `count(*)`, `sum(id)` and `min/max(id)` over 7 tables, identical for both jars. `sum(id)` is sensitive to *which* rows survive, not just how many.

| configuration | container | current | patched | reduction | share of scan subtree | scan subtree |
|---|---|---|---|---|---|---|
| 0.5% deletes | array | 674 (588–748) | **79 (67–97)** | **8.6x** | 43.2% → 7.6% | −33.6% |
| **6.1% deletes** | array | **873 (704–959)** | **94 (68–117)** | **9.3x** | **49.4% → 9.6%** | **−44.8%** |
| 7.0% deletes | bitmap | 420 (378–474) | 113 (109–115) | 3.7x | 33.4% → 11.5% | −22.0% |
| 0.5%, unclustered | array | 621 (568–683) | 73 (64–78) | 8.5x | 44.1% → 7.4% | −29.9% |
| 0.5%, clustered | run | 185 (174–196) | 61 (43–79) | 3.1x | 19.1% → 7.0% | −12.4% |

3 repetitions per arm, arms interleaved (baseline r1, patched r1, baseline r2, …) so machine drift is shared rather than attributed to the jar. The ranges of the two arms do not overlap in any configuration; comparing the best baseline against the worst patched run still leaves 2.2x–7.3x.

The gain tracks the baseline cost: it is largest on array containers, which is exactly where the removed work — a binary search per row — was most expensive.

Hot frames before and after (0.5% deletes, 1 column, sample counts):

```
current                                     patched
  RoaringBitmap.contains              59      RoaringBitmap.forEachInRange           13
  Util.unsignedBinarySearch           49      RoaringBitmap.forAllInRange            13
  Util.hybridUnsignedBinarySearch     45      ArrayContainer.forAllInRange           11
  ArrayContainer.contains             34      IntConsumerRelativeRangeAdapter        11
  RoaringArray.binarySearch           17      Util.hybridUnsignedBinarySearch         1
  RoaringArray.getContainerIndex      17      Util.unsignedBinarySearch               1
```

Binary-search samples: **94 → 2**.

Wall clock, 1 column, median over 30 iterations × 3 runs:

| configuration | current | patched |
|---|---|---|
| 0.5% deletes | 0.207 s | 0.180 s (−12.8%) |
| **6.1% deletes** | 0.221 s | **0.153 s (−30.8%)** |
| 7.0% deletes | 0.171 s | 0.150 s (−12.8%) |

These scans are short (0.15–0.22 s) so wall clock is noisier than the sample counts; I treat it as a direction check rather than the primary number.

### Evidence — microbenchmark

JMH, batchSize 5000, positions built into a full 65536-position chunk at the target density, then round-tripped through `BitmapPositionDeleteIndex.serialize()`/`deserialize()` so the containers match what the read path actually sees. Time per batch, µs (RoaringBitmap 1.6.20 standalone):

| implementation | sparse 0.5% | medium 5% | dense 12% | run (L=64, 5%) | empty batch |
|---|---|---|---|---|---|
| current (`isDeleted` per row) | 70.37 | 81.54 | 19.17 | 44.67 | 23.31 |
| `forEachInRange` + gap fill | **0.47** | **1.86** | **3.38** | **0.80** | **0.26** |
| `forAllInRange` + `RelativeRangeConsumer` | 0.47 | 1.81 | 2.95 | 0.30 | 0.27 |

The `run` column uses length-64 deleted blocks spread across the chunk to reach 5% density
(51 runs), so the batch partially overlaps the deletes. A fully-deleted batch would report
~3800x, which is a degenerate best case and not a number worth quoting.

Two things fall out of the `run` column:

- At **identical density (5%)**, the array container costs 81.54 µs and the run container
  44.67 µs — 1.83x, purely from container structure. `log2(3277)/log2(51) = 2.06`, so the
  binary-search model predicts this within 11%. Table layout changes what the reader pays
  before any code change does.
- **`forAllInRange` is 2.7x faster than `forEachInRange` on run containers** (0.30 vs 0.80),
  because it can skip the gaps between runs with `acceptAllAbsent` instead of only being told
  where the deletes are. On array and bitmap containers the two are equivalent. Both are still
  ≥55x over the current code, so I lean towards the smaller API, but this is the concrete cost
  of that choice and I am happy to go the other way.

All variants were asserted to produce byte-identical `rowIdMapping` and live counts before measurement. The end-to-end gain is smaller than the microbenchmark ratio because the delete-check subtree also contains DV deserialization, the mapping array write, and the `RoaringPositionBitmap` wrapper — none of which this change touches.

### Evidence — the other two paths

**`buildIsDeleted`** is reached when a query projects the `_deleted` metadata column. Same tables,
same method, only the projection differs:

| configuration | current | patched | reduction |
|---|---|---|---|
| 0.5% deletes + `_deleted` | 649 (573–717) | **34 (25–42)** | **18.9x** |
| 6.1% deletes + `_deleted` | 854 (793–892) | **57 (56–58)** | **14.9x** |

The gain is larger here than for `buildRowIdMapping` because this path only has to mark the
deleted positions — it never fills the gaps with live row ids, so the range traversal does
~25 writes per batch instead of ~4,975. Row counts and the number of rows flagged as deleted
were identical between the two jars (40,154 and 488,161, matching the DV cardinalities exactly).

**Equality deletes.** I built two tables with equality delete files written through
`Parquet.writeDeletes(...).buildEqualityWriter()` and committed with `RowDelta`, since Spark
itself only writes position deletes:

| configuration | current | patched | ratio | verdict |
|---|---|---|---|---|
| equality deletes only | 3,303 (2,590–4,249) | 2,635 (2,594–2,679) | 1.25x | ranges overlap — no claim |
| DVs **+** equality deletes | 3,362 (3,177–3,559) | 3,524 (3,267–3,955) | 0.95x | ranges overlap — no claim |

No regression. The second table is the case where the gate actively refuses the fast path, and
the only added work there is one `hasEqDeletes()` call per batch.

The first table turns out to be a useful negative control: with no position deletes,
`deletedPositions == null` short-circuits before `hasEqDeletes()` is even called, so **both jars
execute byte-identical code** — and the measurement still differed by 1.25x. That is a direct
read on how much apparent effect noise can produce at these sample counts, and it is an order of
magnitude below the numbers claimed above.

### Proposal

Add a range-scoped traversal to `PositionDeleteIndex` with a default implementation that preserves current behavior, so no existing implementation has to change:

```java
/**
 * Traverses the deleted positions within the given range in ascending order, applying the
 * provided consumer.
 *
 * @param posStart the first position in the range, inclusive
 * @param length the number of positions in the range
 * @param consumer a consumer for the deleted positions in the range
 */
default void forEachInRange(long posStart, int length, LongConsumer consumer) {
  for (int index = 0; index < length; index++) {
    long pos = posStart + index;
    if (isDeleted(pos)) {
      consumer.accept(pos);
    }
  }
}
```

Override it in `BitmapPositionDeleteIndex`, delegating through `RoaringPositionBitmap` to `RoaringBitmap.forEachInRange`. `EmptyPositionDeleteIndex` overrides it to a no-op.

`RoaringPositionBitmap.forEachInRange` has to split the range across the 32-bit keys, but since `length` is an `int` and a single key covers 2³² positions, a range can span at most two keys.

Then in `ColumnarBatchUtil`, take the range path when there are no equality deletes and fall back to the existing per-row loop otherwise:

```java
PositionDeleteIndex deletedPositions = deletes.deletedRowPositions();

if (deletedPositions != null && !deletes.hasEqDeletes()) {
  return buildRowIdMapping(deletedPositions, deletes, rowStartPosInBatch, batchSize);
}

// existing per-row loop
```

**Note the condition is `!deletes.hasEqDeletes()`, not `eqDeleteFilter == null`.** `DeleteFilter.eqDeletedRowFilter()` returns `t -> true` rather than `null` when there are no equality deletes (`DeleteFilter.java:245`), so a null check would never take the fast path.

**The equality-delete case must keep the per-row loop**, because `eqDeleteFilter.test(row)` needs each row and `ColumnarBatchRow.rowId` is advanced inside that loop. The range path only applies to position deletes / deletion vectors — the V3 DV case.

`deletes.incrementDeleteCount()` is still called once per deleted row.

I kept the proposed API deliberately small. `RelativeRangeConsumer` (which additionally reports absent positions and has bulk `acceptAllPresent`/`acceptAllAbsent` callbacks) is faster in the dense and fully-deleted cases, but it is a much larger API surface to expose through `PositionDeleteIndex`. It also turns out that `RoaringBitmap.forEachInRange` internally delegates to `forAllInRange` with an `IntConsumerRelativeRangeAdapter`, so the small API already gets most of the benefit. Happy to go the other way if maintainers prefer it.

### Tests

`TestColumnarBatchUtil` currently mocks `PositionDeleteIndex` and stubs only `isDeleted`. A Mockito mock does nothing for default methods, so the position-only tests would see "nothing deleted" once the range path is taken. I changed those tests to use a real index built with `Deletes.toPositionIndex(...)`, which also makes them exercise the actual bitmap. Tests that combine position and equality deletes additionally need `hasEqDeletes()` stubbed — they currently stub `hasPosDeletes()`, which `ColumnarBatchUtil` does not read.

New coverage on the branch:

- `TestPositionDeleteIndexForEachInRange` (core, 12 tests): empty index, zero/negative length, inclusive-exclusive boundaries, ascending order, ranges spanning a 65536-position Roaring chunk, ranges spanning a 32-bit key boundary, ranges past the allocated bitmaps, run containers after `runLengthEncode`, and randomized comparison of 500 ranges against a per-position `isDeleted` scan — for both the bitmap implementation and the interface default.
- `TestColumnarBatchUtil` (spark, 5 new tests): non-zero batch start positions, deletes entirely outside the batch range, all rows deleted by position, and a randomized comparison of 60 batches against the per-row path including the delete-counter call count.

```
:iceberg-core:test --tests "org.apache.iceberg.deletes.*"      55 tests, 0 failures
:iceberg-spark:iceberg-spark-4.0_2.13:test --tests "...TestColumnarBatchUtil"
                                                               17 tests, 0 failures
```

### Compatibility

- `PositionDeleteIndex` already extends via `default` methods (`merge`, `forEach`, `cardinality`, `serialize`), so this follows an established pattern and is source- and binary-compatible for external implementations.
- The default implementation is exactly the current behavior, so correctness does not depend on any implementation adopting the override.
- No format or spec change.

### Caveats on the numbers

- Measured on a developer machine (WSL2, no hardware PMU), Spark `local[4]` mode. These are relative comparisons; cycle- and branch-level attribution would need a bare-metal run.
- The run-to-run spread of the same configuration is **9.7% median, 25.9% max** across 15 configurations × 3 runs. I report ranges and do not claim differences inside that band. The patched arm has larger *relative* spread (up to 59%) simply because its absolute sample counts are small (43–117).
- Equality deletes were written on a single column (`id`). Multi-column equality deletes were not measured.
- The `_deleted` projection emits every row rather than filtering, so its wall clock is not comparable to the plain scans above.
- The microbenchmark reuses the output buffer to isolate the algorithm. The real code allocates `new int[batchSize]` per batch.

### Prior work

I looked for existing discussion before filing: #12053 and #12054 touch `ColumnarBatchUtil` but are Javadoc and unit tests. I did not find an existing issue on the per-row probing cost, nor any reference to the Roaring range APIs in this repository.
