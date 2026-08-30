#!/usr/bin/env python3
"""
Iceberg 1.11.0 소스에 §7.1 패치를 적용한다.

배치 안의 position 은 연속 오름차순 구간이므로, position delete index 를
행마다 한 번씩 프로브하는 대신 구간을 한 번만 순회하면 된다.

  core  : PositionDeleteIndex.forEachInRange(long, int, LongConsumer)  (default = 현행 동작)
          BitmapPositionDeleteIndex / EmptyPositionDeleteIndex 오버라이드
          RoaringPositionBitmap.forEachInRange — RoaringBitmap.forEachInRange 위임
  spark : ColumnarBatchUtil 이 equality delete 가 없을 때만 구간 경로를 탄다

중요 — 초안(upstream/issue-columnarbatchutil.md)의 전제 하나가 틀렸다.
`DeleteFilter.eqDeletedRowFilter()` 는 null 을 반환하지 않는다. equality delete 가
없으면 `t -> true` 를 반환한다(DeleteFilter.java:245). 따라서 `eqDeleteFilter == null`
로 게이트를 걸면 빠른 경로가 **영원히 안 탄다**. `hasEqDeletes()` 로 게이트한다.

멱등하다. 이미 적용돼 있으면 건너뛴다.
"""
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/src/iceberg")

CORE = os.path.join(ROOT, "core/src/main/java/org/apache/iceberg/deletes")
SPARK = os.path.join(ROOT, "spark/v4.0/spark/src/main/java/org/apache/iceberg/spark/data/vectorized")


def edit(path, anchor, addition, marker):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    if marker in src:
        print(f"  = 이미 적용됨: {os.path.basename(path)}")
        return False
    if anchor not in src:
        raise SystemExit(f"  ! 앵커를 못 찾음: {path}\n    {anchor[:80]!r}")
    if src.count(anchor) != 1:
        raise SystemExit(f"  ! 앵커가 {src.count(anchor)}번 나타남: {path}")
    src = src.replace(anchor, addition)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(src)
    print(f"  + {os.path.basename(path)}")
    return True


# ---------------------------------------------------------------- 1. 인터페이스
PDI_ANCHOR = """  /**
   * Returns delete files that this index was created from or an empty collection if unknown.
   *
   * @return delete files that this index was created from
   */
  default Collection<DeleteFile> deleteFiles() {"""

PDI_NEW = """  /**
   * Traverses the deleted positions within the given range in ascending order, applying the
   * provided consumer.
   *
   * <p>Callers that test a contiguous range of positions should prefer this method over calling
   * {@link #isDeleted(long)} once per position. Implementations backed by a bitmap can locate the
   * containers covering the range once and walk them, instead of resolving the container for every
   * position.
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

  /**
   * Returns delete files that this index was created from or an empty collection if unknown.
   *
   * @return delete files that this index was created from
   */
  default Collection<DeleteFile> deleteFiles() {"""

# ---------------------------------------------------------------- 2. 비트맵 구현
BPDI_ANCHOR = """  @Override
  public Collection<DeleteFile> deleteFiles() {
    return deleteFiles;
  }"""

BPDI_NEW = """  @Override
  public void forEachInRange(long posStart, int length, LongConsumer consumer) {
    bitmap.forEachInRange(posStart, length, consumer);
  }

  @Override
  public Collection<DeleteFile> deleteFiles() {
    return deleteFiles;
  }"""

# ---------------------------------------------------------------- 3. 빈 인덱스
EPDI_ANCHOR = """  @Override
  public String toString() {
    return "PositionDeleteIndex{}";
  }"""

EPDI_NEW = """  @Override
  public void forEachInRange(long posStart, int length, LongConsumer consumer) {}

  @Override
  public String toString() {
    return "PositionDeleteIndex{}";
  }"""

EPDI_IMPORT_ANCHOR = """package org.apache.iceberg.deletes;

class EmptyPositionDeleteIndex implements PositionDeleteIndex {"""

EPDI_IMPORT_NEW = """package org.apache.iceberg.deletes;

import java.util.function.LongConsumer;

class EmptyPositionDeleteIndex implements PositionDeleteIndex {"""

# ---------------------------------------------------------------- 4. 로어링 래퍼
RPB_ANCHOR = """  @VisibleForTesting
  int allocatedBitmapCount() {"""

RPB_NEW = """  /**
   * Iterates over the positions set within the given range, in ascending order.
   *
   * <p>The range is resolved to at most two underlying 32-bit bitmaps, each of which is traversed
   * once. This avoids the per-position key extraction, bounds check and container lookup that
   * {@link #contains(long)} performs on every call.
   *
   * @param posStart the first position in the range, inclusive
   * @param length the number of positions in the range
   * @param consumer a consumer for the positions that are set within the range
   */
  public void forEachInRange(long posStart, int length, LongConsumer consumer) {
    if (length <= 0) {
      return;
    }

    long posEnd = posStart + length - 1; // inclusive
    validatePosition(posStart);
    validatePosition(posEnd);

    int startKey = key(posStart);
    int endKey = key(posEnd);

    // the range spans at most two keys because the length is bound by Integer.MAX_VALUE,
    // which is smaller than the number of positions a single key covers
    for (int key = startKey; key <= endKey && key < bitmaps.length; key++) {
      long lowStart = key == startKey ? Integer.toUnsignedLong(pos32Bits(posStart)) : 0L;
      long lowEnd = key == endKey ? Integer.toUnsignedLong(pos32Bits(posEnd)) : MAX_POS_32_BITS;
      forEachInRange(key, bitmaps[key], (int) lowStart, (int) (lowEnd - lowStart + 1), consumer);
    }
  }

  @VisibleForTesting
  int allocatedBitmapCount() {"""

RPB_HELPER_ANCHOR = """  // iterates over 64-bit positions, reconstructing them from keys and 32-bit positions
  private static void forEach(int key, RoaringBitmap bitmap, LongConsumer consumer) {
    bitmap.forEach((int pos32Bits) -> consumer.accept(toPosition(key, pos32Bits)));
  }"""

RPB_HELPER_NEW = """  // iterates over 64-bit positions, reconstructing them from keys and 32-bit positions
  private static void forEach(int key, RoaringBitmap bitmap, LongConsumer consumer) {
    bitmap.forEach((int pos32Bits) -> consumer.accept(toPosition(key, pos32Bits)));
  }

  // iterates over a range of 32-bit positions within one bitmap, reconstructing 64-bit positions
  private static void forEachInRange(
      int key, RoaringBitmap bitmap, int start, int length, LongConsumer consumer) {
    bitmap.forEachInRange(start, length, (int pos32Bits) -> consumer.accept(toPosition(key, pos32Bits)));
  }"""

RPB_CONST_ANCHOR = """  private static final long BITMAP_KEY_SIZE_BYTES = 4L;"""
RPB_CONST_NEW = """  private static final long BITMAP_KEY_SIZE_BYTES = 4L;
  private static final long MAX_POS_32_BITS = 0xFFFFFFFFL;"""


def main():
    print("core:")
    edit(os.path.join(CORE, "PositionDeleteIndex.java"), PDI_ANCHOR, PDI_NEW, "default void forEachInRange")
    edit(os.path.join(CORE, "BitmapPositionDeleteIndex.java"), BPDI_ANCHOR, BPDI_NEW, "public void forEachInRange")
    edit(os.path.join(CORE, "EmptyPositionDeleteIndex.java"), EPDI_IMPORT_ANCHOR, EPDI_IMPORT_NEW, "import java.util.function.LongConsumer;")
    edit(os.path.join(CORE, "EmptyPositionDeleteIndex.java"), EPDI_ANCHOR, EPDI_NEW, "public void forEachInRange")
    edit(os.path.join(CORE, "RoaringPositionBitmap.java"), RPB_CONST_ANCHOR, RPB_CONST_NEW, "MAX_POS_32_BITS")
    edit(os.path.join(CORE, "RoaringPositionBitmap.java"), RPB_ANCHOR, RPB_NEW, "public void forEachInRange")
    edit(os.path.join(CORE, "RoaringPositionBitmap.java"), RPB_HELPER_ANCHOR, RPB_HELPER_NEW, "private static void forEachInRange")

    print("spark v4.0:")
    dst = os.path.join(SPARK, "ColumnarBatchUtil.java")
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "ColumnarBatchUtil.patched.java"), encoding="utf-8") as fh:
        new = fh.read()
    with open(dst, encoding="utf-8") as fh:
        old = fh.read()
    if old == new:
        print("  = 이미 적용됨: ColumnarBatchUtil.java")
    else:
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        print("  + ColumnarBatchUtil.java")

    print("\n완료.")


if __name__ == "__main__":
    main()
