#!/usr/bin/env python3
"""
§7.1 패치에 맞춰 Iceberg 테스트를 갱신하고 새 테스트를 추가한다.

왜 기존 테스트를 고쳐야 하는가:
  TestColumnarBatchUtil 은 PositionDeleteIndex 를 Mockito 로 목킹하고 `isDeleted` 만
  스텁한다. 패치 후 position-only 경로는 `forEachInRange` 를 호출하는데, 목 객체의
  default 메서드는 아무 일도 하지 않으므로 "삭제 없음"으로 보인다.
  목을 실제 인덱스(Deletes.toPositionIndex)로 바꾼다 — 실제 비트맵을 태우므로 더 나은 테스트다.

  또한 equality delete 가 섞인 테스트는 `hasEqDeletes()` 를 스텁해야 한다.
  스텁하지 않으면 false 가 반환되어 빠른 경로를 타고 equality delete 가 무시된다.
  (기존 테스트가 `hasPosDeletes()` 를 스텁하지만 ColumnarBatchUtil 은 그걸 안 본다.)

멱등하다.
"""
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/src/iceberg")
HERE = os.path.dirname(os.path.abspath(__file__))

TEST = os.path.join(
    ROOT, "spark/v4.0/spark/src/test/java/org/apache/iceberg/spark/data/vectorized/TestColumnarBatchUtil.java")
CORE_TEST_DIR = os.path.join(ROOT, "core/src/test/java/org/apache/iceberg/deletes")

# (anchor, replacement, count)
EDITS = [
    # --- imports ---
    ("""import java.util.Arrays;
import java.util.function.Predicate;
import java.util.stream.Stream;
import org.apache.iceberg.Schema;
import org.apache.iceberg.data.DeleteFilter;
import org.apache.iceberg.deletes.PositionDeleteIndex;
import org.apache.spark.sql.catalyst.InternalRow;""",
     """import java.util.Arrays;
import java.util.List;
import java.util.function.Predicate;
import java.util.stream.Stream;
import org.apache.iceberg.Schema;
import org.apache.iceberg.data.DeleteFilter;
import org.apache.iceberg.deletes.Deletes;
import org.apache.iceberg.deletes.PositionDeleteIndex;
import org.apache.iceberg.io.CloseableIterable;
import org.apache.iceberg.relocated.com.google.common.collect.Lists;
import org.apache.spark.sql.catalyst.InternalRow;""", 1),

    ("""import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;""",
     """import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;""", 1),

    # --- testBuildRowIdMappingNoDeletes ---
    ("""    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    PositionDeleteIndex deletedRowPos = mock(PositionDeleteIndex.class);

    for (long i = 0; i <= 10; i++) {
      when(deletedRowPos.isDeleted(i)).thenReturn(false);
    }

    when(deleteFilter.deletedRowPositions()).thenReturn(deletedRowPos);""",
     """    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions()).thenReturn(positionIndex());""", 1),

    # --- testBuildRowIdMappingPositionDeletesOnly ---
    ("""    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    PositionDeleteIndex deletedRowPos = mock(PositionDeleteIndex.class);

    // 5 position deletes
    for (long i = 98; i < 103; i++) {
      when(deletedRowPos.isDeleted(i)).thenReturn(true);
    }

    when(deleteFilter.deletedRowPositions()).thenReturn(deletedRowPos);""",
     """    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    // 5 position deletes
    when(deleteFilter.deletedRowPositions()).thenReturn(positionIndex(98, 99, 100, 101, 102));""", 1),

    # --- position + equality (appears twice, identical) ---
    ("""    PositionDeleteIndex deletedRowPos = mock(PositionDeleteIndex.class);
    when(deletedRowPos.isDeleted(1)).thenReturn(true); // 41
    when(deletedRowPos.isDeleted(4)).thenReturn(true); // 44
    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions()).thenReturn(deletedRowPos);""",
     """    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.hasEqDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions()).thenReturn(positionIndex(1, 4)); // 41 and 44""", 2),

    # --- testBuildRowIdMappingEmptyColumVectors ---
    ("""    PositionDeleteIndex deletedRowPos = mock(PositionDeleteIndex.class);
    when(deletedRowPos.isDeleted(1)).thenReturn(true);
    when(deletedRowPos.isDeleted(4)).thenReturn(true);
    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions()).thenReturn(deletedRowPos);""",
     """    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions()).thenReturn(positionIndex(1, 4));""", 1),

    # --- testBuildRowIdMapAllRowsDeleted ---
    ("""    PositionDeleteIndex deletedRowPos = mock(PositionDeleteIndex.class);
    when(deletedRowPos.isDeleted(0)).thenReturn(true); // 40
    when(deletedRowPos.isDeleted(1)).thenReturn(true); // 41
    when(deletedRowPos.isDeleted(4)).thenReturn(true); // 44
    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions()).thenReturn(deletedRowPos);""",
     """    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.hasEqDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions()).thenReturn(positionIndex(0, 1, 4)); // 40, 41, 44""", 1),

    # --- testBuildIsDeletedPositionDeletes ---
    ("""    PositionDeleteIndex deletedRowPos = mock(PositionDeleteIndex.class);
    when(deleteFilter.deletedRowPositions()).thenReturn(deletedRowPos);

    for (long i = 98; i < 100; i++) {
      when(deletedRowPos.isDeleted(i)).thenReturn(true);
    }""",
     """    when(deleteFilter.deletedRowPositions()).thenReturn(positionIndex(98, 99));""", 1),
]

NEW_TESTS = """  @Test
  void testBuildRowIdMappingNonZeroBatchStart() {
    // batches after the first one start at a non-zero position in the file
    long rowStartPosInBatch = 1_000_000L;
    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions())
        .thenReturn(positionIndex(1_000_000L, 1_000_003L, 1_000_009L));

    var rowIdMapping =
        ColumnarBatchUtil.buildRowIdMapping(columnVectors, deleteFilter, rowStartPosInBatch, 10);

    assertThat(rowIdMapping).isNotNull();
    int[] rowIds = (int[]) rowIdMapping.first();
    int liveRows = (Integer) rowIdMapping.second();

    assertThat(liveRows).isEqualTo(7);
    assertThat(Arrays.copyOf(rowIds, liveRows)).containsExactly(1, 2, 4, 5, 6, 7, 8);
    verify(deleteFilter, times(3)).incrementDeleteCount();
  }

  @Test
  void testBuildRowIdMappingDeletesOutsideBatch() {
    // the index covers the whole file, so most batches see no deletes at all
    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions()).thenReturn(positionIndex(5L, 6L, 500L, 501L));

    var rowIdMapping = ColumnarBatchUtil.buildRowIdMapping(columnVectors, deleteFilter, 100L, 100);

    assertThat(rowIdMapping).isNull();
    verify(deleteFilter, times(0)).incrementDeleteCount();
  }

  @Test
  void testBuildRowIdMappingAllRowsDeletedByPosition() {
    when(deleteFilter.hasPosDeletes()).thenReturn(true);
    when(deleteFilter.deletedRowPositions()).thenReturn(positionIndex(0L, 1L, 2L, 3L, 4L));

    var rowIdMapping = ColumnarBatchUtil.buildRowIdMapping(columnVectors, deleteFilter, 0, 5);

    assertThat(rowIdMapping).isNotNull();
    assertThat((Integer) rowIdMapping.second()).isEqualTo(0);
    verify(deleteFilter, times(5)).incrementDeleteCount();
  }

  @Test
  void testBuildIsDeletedNonZeroBatchStart() {
    when(deleteFilter.deletedRowPositions()).thenReturn(positionIndex(1_000_002L, 1_000_004L));

    var isDeleted = ColumnarBatchUtil.buildIsDeleted(columnVectors, deleteFilter, 1_000_000L, 5);

    assertThat(isDeleted).containsExactly(false, false, true, false, true);
    verify(deleteFilter, times(2)).incrementDeleteCount();
  }

  @Test
  void testPositionOnlyPathMatchesPerRowPath() {
    // the range traversal must agree with a straightforward per-row probe on every batch
    java.util.Random random = new java.util.Random(20260818L);
    long fileSize = 300_000L;
    List<Long> deletedPositions = Lists.newArrayList();
    for (long pos = 0; pos < fileSize; pos++) {
      if (random.nextInt(1000) < 7) {
        deletedPositions.add(pos);
      }
    }

    PositionDeleteIndex index = positionIndex(deletedPositions);
    int batchSize = 5000;

    for (long batchStart = 0; batchStart < fileSize; batchStart += batchSize) {
      DeleteFilter<InternalRow> filter = mock(DeleteFilter.class);
      when(filter.deletedRowPositions()).thenReturn(index);

      var rowIdMapping =
          ColumnarBatchUtil.buildRowIdMapping(columnVectors, filter, batchStart, batchSize);

      int[] expected = new int[batchSize];
      int expectedLiveRows = 0;
      for (int rowId = 0; rowId < batchSize; rowId++) {
        if (!index.isDeleted(batchStart + rowId)) {
          expected[expectedLiveRows] = rowId;
          expectedLiveRows++;
        }
      }

      if (expectedLiveRows == batchSize) {
        assertThat(rowIdMapping).as("batch at %s", batchStart).isNull();
      } else {
        assertThat(rowIdMapping).as("batch at %s", batchStart).isNotNull();
        assertThat((Integer) rowIdMapping.second()).isEqualTo(expectedLiveRows);
        assertThat(Arrays.copyOf((int[]) rowIdMapping.first(), expectedLiveRows))
            .as("batch at %s", batchStart)
            .containsExactly(Arrays.copyOf(expected, expectedLiveRows));
        verify(filter, times(batchSize - expectedLiveRows)).incrementDeleteCount();
      }
    }
  }

  private static PositionDeleteIndex positionIndex(long... positions) {
    List<Long> list = Lists.newArrayList();
    for (long position : positions) {
      list.add(position);
    }
    return positionIndex(list);
  }

  private static PositionDeleteIndex positionIndex(List<Long> positions) {
    return Deletes.toPositionIndex(CloseableIterable.withNoopClose(positions));
  }

  private ColumnVector[] mockColumnVector() {"""


def main():
    with open(TEST, encoding="utf-8") as fh:
        src = fh.read()

    if "positionIndex(" in src:
        print("  = 이미 적용됨: TestColumnarBatchUtil.java")
    else:
        for anchor, repl, count in EDITS:
            found = src.count(anchor)
            if found != count:
                raise SystemExit(f"앵커가 {found}번 나타남 (기대 {count}):\n{anchor[:120]}")
            src = src.replace(anchor, repl)

        marker = "  private ColumnVector[] mockColumnVector() {"
        if src.count(marker) != 1:
            raise SystemExit("mockColumnVector 앵커를 못 찾음")
        src = src.replace(marker, NEW_TESTS)

        with open(TEST, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src)
        print("  + TestColumnarBatchUtil.java")

    dst = os.path.join(CORE_TEST_DIR, "TestPositionDeleteIndexForEachInRange.java")
    with open(os.path.join(HERE, "TestPositionDeleteIndexForEachInRange.java"), encoding="utf-8") as fh:
        new = fh.read()
    old = open(dst, encoding="utf-8").read() if os.path.exists(dst) else None
    if old == new:
        print("  = 이미 적용됨: TestPositionDeleteIndexForEachInRange.java")
    else:
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        print("  + TestPositionDeleteIndexForEachInRange.java")

    print("\n완료.")


if __name__ == "__main__":
    main()
