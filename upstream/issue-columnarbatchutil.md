Spark: vectorized reads probe the position delete index once per row, ignoring that batch positions are contiguous

---

### Summary

`ColumnarBatchUtil.buildRowIdMapping` and `buildIsDeleted` call `PositionDeleteIndex.isDeleted(pos)` once for every row in a batch. Positions within a batch are a contiguous ascending range and the DV-backed index is a Roaring bitmap, so the same information can be obtained with a single range traversal instead of `batchSize` independent probes.

On a V3 table read with a narrow projection, this loop accounts for **43–58% of scan CPU**, depending on delete density — a figure I have since reproduced on three CPU microarchitectures (Zen 3, Sapphire Rapids, Graviton3), on local disk and on S3, with a warm and a dropped page cache, and at task parallelism 1 through 16. It moves by at most a few points across all of those; with a heavy aggregation in the same job it settles toward the low end of that range. On a small distributed cluster it comes out at **42%** — the top of the range is a single-JVM figure, and executor startup and serialization enlarge the denominator once the work is spread across machines. I implemented the change against `apache-iceberg-1.11.0` and measured it end to end: the delete-check CPU drops by **3.1x–9.3x** for `buildRowIdMapping` and **14.9x–18.9x** for `buildIsDeleted`, and the scan subtree as a whole drops by **12–45%**. Tables with equality deletes keep the existing loop and show no regression.

There is also something users can do today, without waiting for this: **sorting the table by the column the deletes target makes the delete check 2.8x cheaper on its own**, because the Roaring bitmap switches from array to run containers. That only works when the deletes concentrate on relatively few distinct key values — I measured it fading to 1.1x and then to nothing as the sort key's cardinality rises. The two remedies overlap but do not replace each other: sorting alone 2.8x, this patch alone 7.8x, both together 12.4x.

How much of that reaches end-to-end query time depends on how much work the rest of the scan does. The threshold is not a column count — it is the share the delete check holds in scan CPU. Above roughly 20% the change is worth **13–19% of end-to-end query time**; below it the difference falls inside my measurement noise, even though the delete-check CPU is still reduced 3.0x–6.7x there. The change removes the same absolute CPU either way.

As a unit that transfers to other schemas: **one delete check costs about as much as decoding 1.6–3.2 fixed-width integer columns**, while a single 32-char string column costs 8–10 integer columns. On my table a projection of ten integer columns still shows the effect and a projection of three md5 strings does not — so a column count is the wrong thing to quote. One caveat on reading any CPU figure below as latency: across 24 configurations only about half of a profiler-measured CPU saving arrives as wall clock (regression slope 0.53).

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
- With a wide projection (20 columns) the same table shows 3.75%, because the check is per row regardless of how many columns are read. **The 40–53% figures are specific to narrow projections.** See *Evidence — projection width* below for the patched numbers at 20 columns.

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

### Evidence — projection width

The numbers above are all `select` of a single column. Because the delete check runs once per row regardless of projection width, widening the projection leaves the delete-check cost alone and inflates everything around it. I re-ran the same three tables projecting 20 columns, 6 repetitions per arm, with the arm order flipped halfway (rounds 1–3 baseline first, rounds 4–6 patched first):

| configuration | delete-check share, 1 col | delete-check share, 20 cols | reduction, 1 col | reduction, 20 cols |
|---|---|---|---|---|
| 0.5% deletes | 43.2% | **3.2%** | 8.6x | **6.8x** |
| **6.1% deletes** | 49.4% | **4.8%** | 9.3x | **6.3x** |
| 7.0% deletes | 33.4% | 2.7% | 3.7x | 3.5x |

The arm ranges are disjoint in all three configurations, so the reduction itself still holds at 20 columns. The mechanism shows up directly in the absolute sample counts: widening the projection 20x leaves the baseline delete-check samples essentially unchanged (674→625, 873→902, 420→516) while the scan subtree grows 1,558→19,332 — the share falls because the denominator grew, not because the check got cheaper.

I then filled in the widths between, 6 repetitions per arm at each (108 profiles). End-to-end wall clock, paired within each round (negative = patched faster; **bold** = outside the 9.7% noise band with all 6 pairs agreeing in sign, sign test p = 0.03):

| projected columns | 0.5% deletes | 6.1% deletes | 7.0% deletes |
|---|---|---|---|
| 1 | **−19.5%** | **−18.8%** | **−12.8%** |
| 3 | **−18.5%** | **−13.5%** | −15.6% (5/6, p=0.22) |
| 5 | **−13.2%** | **−15.2%** | −6.7% |
| 10 | −2.3% | −6.7% | +4.7% |
| 20 | −8.4% | +3.5% | +4.9% |

So the crossover is between 5 and 10 columns on this table. But the useful statement is not a column count — it is what those columns cost to decode. Scan samples added per extra column, at 6.1% deletes:

| range | columns added | scan samples per column |
|---|---|---|
| 1 → 3 | 2 ints | 280 |
| 3 → 5 | 2 ints | 100 |
| **5 → 10** | 2 doubles + **3 md5 strings** | **1,958** |
| 10 → 20 | mixed | 660 |

The first five columns of this table are integers; the first string column is the eighth — so column count and decoding cost are completely confounded in the table above.

To separate them I ran a third experiment that puts the two in opposition: ten integer columns (many columns, cheap) against one and three md5 string columns (few columns, expensive), 6 repetitions per arm across the same three densities (108 profiles). Delete-check share of scan CPU:

| projection | columns | 0.5% deletes | 6.1% deletes | 7.0% deletes |
|---|---|---|---|---|
| 1 int (`id`) | 1 | 43.2% | 49.4% | 33.4% |
| 1 md5 string | 1 | 17.4% | 22.2% | 12.1% |
| 3 ints | 3 | 35.5% | 42.8% | 27.6% |
| 3 md5 strings | 3 | 7.5% | 10.6% | 5.5% |
| **10 ints** | 10 | **15.9%** | **21.6%** | **12.8%** |
| 10 mixed (3 strings) | 10 | 5.9% | 7.7% | 4.3% |

Ten integer columns hold a **larger** share than three string columns in all three densities — the opposite of what a column-count model predicts, and a single integer column holds twice the share of a single string column. Solving for per-column decode cost across these configurations gives 288–299 samples for an integer column against 2,395–2,966 for a 32-char md5 column (8–10x); the integer cost and the fixed overhead come out stable across delete densities (288–299 and 537–598), which is what they should do, since decoding does not depend on how much is deleted.

So the portable form of the threshold is **one delete check ≈ decoding 1.6–3.2 integer columns ≈ 0.16–0.39 of one md5 string column**, and "five columns" is an artifact of this table's column order.

One caution on reading the sample counts as latency. Across all 24 configurations measured here, regressing the measured wall-clock reduction on the sample-based scan reduction through the origin gives a slope of 0.53 (R² = 0.72): **only about half of a profiler-measured CPU saving arrives as wall clock.** (Taking the ratio only where wall clock clears the noise band gives 1.73x, but that selects for large effects; the regression is the honest summary. I have not established the mechanism — tail-task effects under `local[4]` and fixed cost outside the scan subtree are both candidates.)

Combining the two — the change removes ~87% of the delete check, and about half of that reaches latency — predicts that the effect clears my 9.7% noise band once the delete check is above **~20% of scan CPU**. The ten-integer projection sits at 21.5% share and measured −9.1%, right on the boundary. Every scan-subtree percentage quoted in this issue is subject to this correction.

I also swept task parallelism to check how much that 0.53 depends on it, since the threshold is derived from it. Fitting the correction factor against `local[N]` for N = 1, 2, 4, 16 (96 profiles, one 16-vCPU instance, split size pinned so task count scales) gives **1.30 × N^0.14** (R² = 0.95): 1.29x at N=1, 1.63x at N=4, 1.90x at N=16. So the factor does grow with parallelism — the tail-task explanation is directionally supported — but only by 1.5x across a 16x range, which moves the threshold from about 18% to about 21%. The break-even is **around a fifth of scan CPU** and does not change order of magnitude with scale. Note that `local[N]` is a thread pool in one JVM; this says nothing about shuffle or executor scheduling in a real cluster.

**Wall clock at 20 columns is not interpretable, in either direction.** The three configurations came out +8.7% / −3.9% / −3.2% (patched vs current), all inside the 9.7% run-to-run band and not even agreeing on sign. Pairing runs within a round, the patched arm was slower in 13 of 18 pairs (sign test p ≈ 0.10, not significant); before flipping the arm order it was 8 of 9, so part of that was an order effect rather than the jar. I make no wall-clock claim at this projection width.

So the honest end-to-end statement is: **the change reduces delete-check CPU by 3.0x–9.3x across every projection I measured, and that turns into a visible query-latency win while the delete check is above roughly a fifth of scan CPU.** On this schema that means projections of up to about ten fixed-width columns, or fewer than one md5 string column. Below that threshold the CPU saving is real but I cannot measure it in query time.

### Evidence — does this hold outside my machine?

Everything above was measured on one developer machine. Since the delete-check share is the number
the rest of the argument rests on, I re-ran the same two jars (byte-identical, verified by md5) on
the same table bytes in three more environments. Delete-check share of scan CPU, narrow projection:

| delete density | Zen 3 / WSL2 | Sapphire Rapids | Graviton3 | S3 instead of local disk | page cache dropped |
|---|---|---|---|---|---|
| 0.5% | 43.2% | 49.3% | 48.4% | — | — |
| **6.1%** | **49.4%** | **56.2%** | **54.3%** | **58.0%** (vs 56.2% on EBS) | **52.0%** (vs 51.0% warm) |
| 7.0% | 33.4% | 39.3% | 35.6% | — | — |

The share moves by at most a few points, and where it moves it moves *up* off my machine, so the
numbers quoted above are the conservative end.

Two of those results were surprises worth stating, because both were predictions I wrote down
first and got wrong:

- **Dropping the page cache does not lower the share, and neither does reading from S3.**
  `ctimer` samples CPU time, so I/O *wait* never enters the denominator; and the S3 client's own
  CPU (HTTP, TLS, checksums) turns out to be only ~1.4 percentage points of total CPU here, far
  too small to move it. Storage changes what fraction of wall clock is CPU, not what fraction of
  CPU is the delete check.
- **The patch helps *more* on newer cores**, not less: 11.3x–17.9x on the cloud instances against
  8.6x–9.3x on mine, because the per-row probe gets relatively more expensive while the bulk range
  traversal gets cheaper. I first attributed that to branch prediction. Hardware counters do not
  support it (see below), so I state it as an observation, not a mechanism.

One methodological note that cuts against my own earlier numbers: the run-to-run spread on the
dedicated instances was **7–13%**, against **27% median** on my WSL2 box. The 9.7% noise floor I
use throughout this issue is a property of my development environment more than of the workload.
Anything I mark "inside the noise" might be resolvable on quieter hardware.

### Evidence — what a user can do before this is fixed

The delete check is cheap or expensive depending on which Roaring container the positions land in,
so the physical layout of the table matters independently of this patch. I tested that directly:
two tables with the **same rows deleted** (identical predicate, verified to the row: 430,575 in
both), differing only in whether the table was sorted by the column the deletes target. The scan
carries no predicate, so no files can be skipped — this isolates the delete-check effect from the
file-pruning effect that sorting also gives you.

| | delete-check CPU | share of scan | vs baseline |
|---|---|---|---|
| unsorted, unpatched | 915 | 51.0% | 1.0x |
| **sorted, unpatched** | 327 | 27.3% | **2.8x** |
| unsorted, patched | 117 | 10.9% | 7.8x |
| **sorted + patched** | 74 | 8.0% | **12.4x** |

The mechanism is visible in the files: unsorted, every chunk is an `array` container at ~7,000 bytes;
sorted, every chunk is a `run` container at 6-10 bytes, and the whole deletion vector shrinks from
864 KB to 2.5 KB. Decoding cost is unchanged between the two (non-delete scan samples 876 vs 883),
which is what makes the comparison valid.

**This has a condition, and it matters.** Sorting only helps when the deletes concentrate on
relatively few distinct values of the sort key. Sweeping the key's cardinality:

| sort key cardinality | rows per value | delete-check gain |
|---|---|---|
| ~1,000 | ~8,000 | **2.78x** |
| ~1,000,000 | ~8 | 1.10x |
| ~10^9 (effectively unique) | 1 | **0.89x** |

So "sort by the delete key" is good advice for deletes driven by date, region or tenant, and no
advice at all for deletes driven by individual row ids — there the sorted table performs like the
unsorted one and you have paid the sort for nothing. The two remedies also overlap: the sorting
gain drops from 2.78x to 1.58x once this patch is applied, since the patch has already removed most
of what sorting was saving.

(Wall-clock differences on this axis did not clear my noise band, so the numbers above are CPU
samples, where the repeat ranges are disjoint. I did not measure the cost of the sort itself.)

### Evidence — a distributed cluster

Everything above runs in one JVM, so I put the same two jars on a 3-node Spark 4.0.4 standalone
cluster (1 master + 2 workers, 8 cores total, table on S3, profiler attached to the *executors* via
`spark.executor.extraJavaOptions` and the per-executor profiles summed per run).

| query | delete-check share of scan CPU | patch speedup | wall clock |
|---|---|---|---|
| scan, one JVM | 53.95% | 6.8x | — |
| **scan, cluster** | **41.58%** | **5.30x** | **−9.5%** (0/6 pairs, sign test p = 0.03) |
| aggregation, one JVM | 43.19% | 6.9x | — |
| **aggregation, cluster** | **41.57%** | **5.07x** | +0.7% (inside noise) |

Two things worth stating, both of which contradict what I predicted:

- **The share drops to about 42% and the speedup to about 5x.** Executor startup, task
  serialization and S3 reads all land inside the scan subtree, enlarging the denominator. The
  delete check is still the single largest identified item, but the 58% top of my range is a
  single-JVM number and I have corrected the summary accordingly.
- **In the cluster the shuffle no longer moves the share** — 41.58% with no shuffle against 41.57%
  with a large one, where in a single JVM the same contrast was 53.95% against 43.19%. The fixed
  distributed overhead appears to already occupy the space the shuffle would otherwise take. I have
  not confirmed that mechanism.

The wall-clock result is the useful one: on the plain scan the patched build is **9.5% faster end
to end on the cluster**, with all six paired rounds agreeing in sign. That is the first time I have
been able to claim a latency difference outside a single JVM.

Caveat on size: 8M rows over 8 cores is small enough that the cluster is 2.3x slower than one JVM
on the same query (0.50s vs 0.215s), which means fixed overhead is inflating the denominator. On a
job large enough to be worth a cluster I would expect the share to move back toward the single-JVM
figure, so 42% reads as a floor.

### Evidence — hardware counters, and a mechanism I had wrong

I had assumed the per-row probe was expensive because the `array` container does a binary search
whose branches mispredict. **That is not what the counters say.** Running the microbenchmark under
`-prof perfnorm` (AMD Zen 3, 5000-row batch, 2 forks x 3 iterations):

| pattern | container | us/op | cycles/row | **insn/row** | branches/row | **br-misses/row** | IPC |
|---|---|---|---|---|---|---|---|
| 0.5% density | array (card. 327) | 66.05 | 58.4 | 282.9 | 77.2 | 0.0126 | 4.84 |
| 5% density | array (card. 3,276) | 85.21 | 78.8 | 370.0 | 96.6 | 0.0763 | 4.70 |
| 12% density | **bitmap** | **21.13** | **18.6** | **86.5** | 17.8 | 0.0292 | 4.66 |
| scattered runs | run | 45.27 | 40.4 | 191.4 | 45.0 | 0.0066 | 4.74 |

Three things fall out:

- **Branch misprediction explains about 1.4% of the gap, not the gap.** To account for the
  301,045-cycle difference between the 5% and 12% patterns at an 18-cycle penalty you would need
  roughly 16,700 extra mispredicts per batch. There are 235. For the 0.5% pattern the array
  container actually mispredicts *less* than the bitmap one does.
- **Cache locality does not explain it either.** The fastest pattern (bitmap) takes **8x more**
  L1-dcache misses than the slow ones — 1,173.9 per op against 141.2.
- **IPC is flat at 4.66–4.84 across all four patterns**, and cycles track instructions to within
  0.8–3.8%. Nothing is stalling. The containers differ in **how many instructions they execute
  per row**, and that is the whole story.

This cuts for the proposal rather than against it. Even the cheapest container costs **86.5
instructions per row**, because `RoaringPositionBitmap.contains(long)` has to split the position,
binary-search the key array and follow two levels of indirection before it can test a bit. That
fixed per-call cost is paid 5,000 times per batch whatever the container is. The batch-oriented API
pays it a handful of times instead, which is why the speedups are as large as they are.

Control: before trusting numbers this low I checked that the counters work under this hypervisor,
with a sorted-vs-shuffled branch experiment — 9.2M vs 108.6M mispredicts (11.8x), matching the
theoretical 50% miss rate to within 3%.

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

- The primary measurements are on a developer machine (WSL2, no hardware PMU), Spark `local[4]` mode; the portability checks above are on dedicated EC2 instances. No hardware PMU anywhere, so cycle- and branch-level attribution would still need a bare-metal run.
- Most of the numbers are single-JVM `local[N]`. Adding a `GROUP BY` moves the delete check from **14.0% of total query CPU to 6.8% and then 2.4%** as the aggregation grows, while its share *of the scan* stays in the 43-54% band and the absolute CPU saved is unchanged. I also ran it on a 3-node Spark standalone cluster (see below); the cluster there is small (8 cores, 8M rows), so fixed overhead dominates and the cluster is actually 2.3x *slower* than one JVM on the same query. A production-sized job would sit closer to the single-JVM numbers, so treat 42% as a lower bound rather than a typical cluster value.
- One table shape throughout: 8M rows, 4 files, 20 columns. File-skipping predicates, nested types and many-file tables are unmeasured.
- The run-to-run spread of the same configuration is **9.7% median, 25.9% max** across 15 configurations × 3 runs. I report ranges and do not claim differences inside that band. The patched arm has larger *relative* spread (up to 59%) simply because its absolute sample counts are small (43–117).
- Equality deletes were written on a single column (`id`). Multi-column equality deletes were not measured.
- I ran the length-controlled axis afterwards (four md5-derived string columns of 8, 16, 24 and 32 characters, same type, one column projected at a time). Fitting scan samples against length gives **1,099 + 63.8 x length** (R² = 0.94), so it is neither purely "because it is a string" nor purely "because it is wide" — both terms are real. At 8 characters the fixed per-column term and the length term are about equal; at 32 characters length dominates 2:1. Against an integer column the same measurement gives 2.1x for an 8-char string and 4.2x for a 32-char one, so the "8-10 integer columns" figure above applies to full-length md5, not to short strings. One point (24 chars) sits well off the line and I have not explained it — Parquet page encoding is the obvious suspect and I did not inspect the page headers.
- The 0.53 sample-to-wall-clock slope is a fit to data I had already collected, not a prediction I tested. It is measured on one machine at `local[4]` with a warm page cache; I would expect a different slope at other parallelism or on other storage.
- The 1-column wall-clock rows come from 3 repetitions; every other row from 6.
- The `_deleted` projection emits every row rather than filtering, so its wall clock is not comparable to the plain scans above.
- The microbenchmark reuses the output buffer to isolate the algorithm. The real code allocates `new int[batchSize]` per batch.

### Prior work

I looked for existing discussion before filing: #12053 and #12054 touch `ColumnarBatchUtil` but are Javadoc and unit tests. I did not find an existing issue on the per-row probing cost, nor any reference to the Roaring range APIs in this repository.
