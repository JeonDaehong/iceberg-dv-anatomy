/*
 * Phase 3-B 프로토타입 벤치마크 — README §7.1
 *
 * 측정 대상: Iceberg 벡터화 리더가 배치마다 rowIdMapping 을 만드는 루프.
 *
 *   spark/v4.0/.../vectorized/ColumnarBatchUtil.java:57  buildRowIdMapping
 *     for (int rowId = 0; rowId < batchSize; rowId++)      // :72
 *       if (deletedPositions.isDeleted(pos)) ...           // :75 -> :143
 *
 * isDeleted -> BitmapPositionDeleteIndex.isDeleted (:87)
 *           -> RoaringPositionBitmap.contains (:142)
 *           -> RoaringBitmap.contains
 *           -> RoaringArray.getIndex -> binarySearch (:739, :221)
 *
 * 즉 배치(기본 5000행)마다 5000번의 최상위 이진 탐색이 반복된다.
 * 배치 안의 position 은 연속 단조 증가인데도 그 사실이 활용되지 않는다.
 *
 * 이 벤치는 현행 구현과, RoaringBitmap 1.6.20 이 이미 제공하는 벌크 API 기반
 * 대안들을 같은 조건에서 비교한다.
 *
 * 패키지가 org.apache.iceberg.deletes 인 이유: RoaringPositionBitmap 이
 * package-private 이라 리플렉션 없이 접근하려면 같은 패키지여야 한다.
 */
package org.apache.iceberg.deletes;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;
import java.util.Random;
import java.util.concurrent.TimeUnit;
import org.openjdk.jmh.annotations.*;
import org.openjdk.jmh.infra.Blackhole;
import org.roaringbitmap.RelativeRangeConsumer;
import org.roaringbitmap.RoaringBatchIterator;
import org.roaringbitmap.RoaringBitmap;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MICROSECONDS)
@State(Scope.Thread)
@Fork(value = 3, jvmArgsAppend = {"-Xms2g", "-Xmx2g"})
@Warmup(iterations = 5, time = 1)
@Measurement(iterations = 8, time = 1)
public class DvBatchBench {

  /** Roaring 청크 크기. 컨테이너 타입은 이 단위의 밀도로 결정된다. */
  private static final int CHUNK = 65536;

  /** Spark 벡터화 리더의 기본 배치 크기. */
  @Param({"5000"})
  public int batchSize;

  /**
   * 삭제 패턴. 컨테이너 타입은 '청크' 밀도로 정해지므로, 배치만이 아니라
   * 청크 전체(65536 position)를 해당 밀도로 채운 뒤 그 안의 한 배치를 측정한다.
   *
   *   SPARSE_0_5  d=0.5%   -> array   (실무 CDC 에 가장 가까움)
   *   MEDIUM_5    d=5%     -> array   (4096/65536=6.25% 경계 바로 아래)
   *   DENSE_12    d=12%    -> bitmap  (경계 위)
   *   RUN_CONTIG  연속 블록 -> run  ⚠️ 배치가 통째로 삭제되는 '퇴화' 케이스.
   *               벌크 콜백(acceptAllPresent)이 한 번에 처리해버려 배속이 과장된다.
   *               상한선으로만 읽을 것.
   *   RUN_PARTIAL 길이 64 블록을 5% 밀도로 흩뿌림 -> run, nRuns 약 51.
   *               배치와 '부분' 겹침이 생기는 현실적인 run 케이스.
   *               F-009 의 L=64 지점에 대응한다.
   *   EMPTY_BATCH DV 는 있으나 이 배치 구간엔 삭제 0건
   *               (정렬 레이아웃에서 흔해지는 케이스 — README §3-A 와 연결)
   */
  @Param({"SPARSE_0_5", "MEDIUM_5", "DENSE_12", "RUN_CONTIG", "RUN_PARTIAL", "EMPTY_BATCH"})
  public String pattern;

  private RoaringPositionBitmap rpb;   // 현행 Iceberg 가 실제로 쓰는 타입
  private RoaringBitmap rb;            // 동일 내용의 raw 비트맵 (대안 API 용)
  private int batchStart;              // 배치 시작 position
  private int[] mapping;               // 재사용 버퍼 (모든 변형에 공평)
  private int[] delBuf;                // BatchIterator 용 버퍼
  private int expectedLive;            // 정합성 검증 기준값

  @Setup(Level.Trial)
  public void setup() {
    Random rnd = new Random(20260817);
    RoaringBitmap src = new RoaringBitmap();

    // 청크 1개를 통째로 채운다 (컨테이너 타입 결정 단위).
    // 배치는 청크 중앙에 두어 경계 효과를 피한다.
    batchStart = CHUNK / 2 - batchSize / 2;

    switch (pattern) {
      case "SPARSE_0_5": fillRandom(src, rnd, 0.005); break;
      case "MEDIUM_5":   fillRandom(src, rnd, 0.05);  break;
      case "DENSE_12":   fillRandom(src, rnd, 0.12);  break;
      case "RUN_CONTIG":
        // 청크의 35%~65% 를 연속 삭제. 배치가 그 안에 '완전히' 포함된다.
        // 즉 배치의 5000행이 전부 삭제 -> 벌크 콜백 한 번으로 끝난다. 퇴화 케이스.
        src.add((long) (CHUNK * 0.35), (long) (CHUNK * 0.65));
        break;
      case "RUN_PARTIAL":
        // 길이 64 블록을 청크 전체에 균등 분포시켜 밀도 5% 를 맞춘다.
        //   블록 수 = 65536*0.05/64 = 51,  블록 간격 = 65536/51 = 1285
        // -> run 컨테이너지만 nRuns 가 51 이라 이진 탐색이 살아 있고,
        //    배치(5000행 = 청크의 7.6%)는 그중 약 4블록과 '부분' 겹친다.
        fillBlocks(src, 64, 0.05);
        break;
      case "EMPTY_BATCH":
        // 삭제는 존재하되 전부 배치 구간 밖.
        src.add(0L, (long) batchStart);
        break;
      default: throw new IllegalArgumentException(pattern);
    }

    // Iceberg 쓰기 경로를 그대로 재현한다:
    //   BitmapPositionDeleteIndex.serialize():126 이 runLengthEncode() 를 호출하고,
    //   읽기 경로는 그렇게 직렬화된 컨테이너를 그대로 역직렬화해 쓴다.
    // 따라서 벤치도 반드시 직렬화 왕복을 거쳐야 실제와 같은 컨테이너를 얻는다.
    RoaringPositionBitmap tmp = new RoaringPositionBitmap();
    src.forEach((org.roaringbitmap.IntConsumer) p -> tmp.set(Integer.toUnsignedLong(p)));
    tmp.runLengthEncode();

    ByteBuffer buf = ByteBuffer.allocate((int) tmp.serializedSizeInBytes())
        .order(ByteOrder.LITTLE_ENDIAN);
    tmp.serialize(buf);
    buf.flip();
    rpb = RoaringPositionBitmap.deserialize(buf);

    rb = new RoaringBitmap();
    rpb.forEach(p -> rb.add((int) p));
    rb.runOptimize();

    mapping = new int[batchSize];
    delBuf = new int[batchSize];

    // --- 정합성 검증: 모든 변형이 동일한 결과를 내야 비교가 성립한다 ---
    expectedLive = baseline();
    int[] expected = Arrays.copyOf(mapping, expectedLive);

    check("perRowRoaringBitmap", expected, perRowRoaringBitmap());
    check("forAllInRange",       expected, forAllInRange());
    check("forEachInRange",      expected, forEachInRange());
    check("batchIterator",       expected, batchIterator());
    check("rangeCardFastPath",   expected, rangeCardFastPath());

    long card = rpb.cardinality();
    System.out.printf(
        "%n  [setup] pattern=%-11s chunkCardinality=%,7d  batch[%d,%d)  live=%d/%d  container=%s%n",
        pattern, card, batchStart, batchStart + batchSize, expectedLive, batchSize,
        guessContainer(card));
  }

  /** 길이 {@code blockLen} 의 블록을 균등 간격으로 배치해 목표 밀도를 맞춘다. */
  private static void fillBlocks(RoaringBitmap b, int blockLen, double density) {
    int blocks = (int) (CHUNK * density) / blockLen;
    int stride = CHUNK / blocks;
    for (int i = 0; i < blocks; i++) {
      long start = (long) i * stride;
      b.add(start, Math.min(start + blockLen, CHUNK));
    }
  }

  private static void fillRandom(RoaringBitmap b, Random rnd, double density) {
    int target = (int) (CHUNK * density);
    while (b.getCardinality() < target) {
      b.add(rnd.nextInt(CHUNK));
    }
  }

  /** 청크 카디널리티로 컨테이너 타입을 추정 (실제 확인은 Phase 1 dv-inspect 로). */
  private static String guessContainer(long chunkCardinality) {
    if (chunkCardinality > 4096) return "bitmap(추정)";
    return "array(추정)";
  }

  private void check(String name, int[] expected, int live) {
    if (live != expected.length) {
      throw new AssertionError(name + ": live " + live + " != " + expected.length);
    }
    for (int i = 0; i < live; i++) {
      if (mapping[i] != expected[i]) {
        throw new AssertionError(name + ": mapping[" + i + "] " + mapping[i] + " != " + expected[i]);
      }
    }
  }

  // ==================================================================
  //  A. 현행 Iceberg — ColumnarBatchUtil.buildRowIdMapping 재현
  // ==================================================================
  @Benchmark
  public int a_baseline_perRow_RoaringPositionBitmap() {
    return baseline();
  }

  private int baseline() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      long pos = batchStart + rowId;
      if (!rpb.contains(pos)) {
        mapping[live++] = rowId;
      }
    }
    return live;
  }

  // ==================================================================
  //  B. 래퍼 오버헤드 분리 — raw RoaringBitmap 에 직접 per-row contains
  //     A 와의 차이 = RoaringPositionBitmap 2단 간접 + 경계 체크 비용
  // ==================================================================
  @Benchmark
  public int b_perRow_RoaringBitmap() {
    return perRowRoaringBitmap();
  }

  private int perRowRoaringBitmap() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (!rb.contains(batchStart + rowId)) {
        mapping[live++] = rowId;
      }
    }
    return live;
  }

  // ==================================================================
  //  C. forAllInRange + RelativeRangeConsumer
  //     연속 구간을 한 번에 훑으며 present/absent 를 통보받는다.
  //     acceptAllAbsent/acceptAllPresent 벌크 콜백 덕에 run 컨테이너나
  //     빈 구간은 O(1) 로 처리된다.
  // ==================================================================
  @Benchmark
  public int c_forAllInRange() {
    return forAllInRange();
  }

  private final MappingConsumer consumer = new MappingConsumer();

  private int forAllInRange() {
    consumer.reset(mapping);
    rb.forAllInRange(batchStart, batchSize, consumer);
    return consumer.live;
  }

  private static final class MappingConsumer implements RelativeRangeConsumer {
    private int[] out;
    private int live;

    void reset(int[] target) {
      this.out = target;
      this.live = 0;
    }

    @Override public void acceptPresent(int relPos) { /* 삭제된 행 — 건너뜀 */ }

    @Override public void acceptAbsent(int relPos) { out[live++] = relPos; }

    @Override public void acceptAllPresent(int relFrom, int relTo) { /* 전부 삭제 */ }

    @Override public void acceptAllAbsent(int relFrom, int relTo) {
      for (int i = relFrom; i < relTo; i++) {
        out[live++] = i;
      }
    }
  }

  // ==================================================================
  //  D. forEachInRange — 삭제된 position 만 순회하고, 사이 구간은 순차 채움
  //     분기 많은 per-row contains 를 순차 복사 루프로 바꾸는 효과
  // ==================================================================
  @Benchmark
  public int d_forEachInRange() {
    return forEachInRange();
  }

  private int delCount;

  private int forEachInRange() {
    delCount = 0;
    rb.forEachInRange(batchStart, batchSize, (org.roaringbitmap.IntConsumer)
        p -> delBuf[delCount++] = p - batchStart);
    return fillGaps(delCount);
  }

  /** 정렬된 삭제 상대위치 목록으로부터 rowIdMapping 을 채운다. */
  private int fillGaps(int nDeleted) {
    int live = 0;
    int prev = 0;
    for (int i = 0; i < nDeleted; i++) {
      int d = delBuf[i];
      for (int r = prev; r < d; r++) {
        mapping[live++] = r;
      }
      prev = d + 1;
    }
    for (int r = prev; r < batchSize; r++) {
      mapping[live++] = r;
    }
    return live;
  }

  // ==================================================================
  //  E. BatchIterator — 삭제 position 을 버퍼 단위로 벌크 인출
  // ==================================================================
  @Benchmark
  public int e_batchIterator() {
    return batchIterator();
  }

  private int batchIterator() {
    RoaringBatchIterator it = rb.getBatchIterator();
    it.advanceIfNeeded(batchStart);
    int n = 0;
    int end = batchStart + batchSize;
    while (it.hasNext() && n < delBuf.length) {
      int got = it.nextBatch(delBuf.length - n == delBuf.length ? delBuf : delBuf);
      if (got == 0) break;
      boolean stop = false;
      for (int i = 0; i < got; i++) {
        int p = delBuf[i];
        if (p >= end) { stop = true; break; }
        delBuf[n++] = p - batchStart;
      }
      if (stop) break;
    }
    return fillGaps(n);
  }

  // ==================================================================
  //  F. rangeCardinality fast-path
  //     이 배치에 삭제가 0건이면 5000번의 contains 를 통째로 건너뛴다.
  //     정렬 레이아웃(README §3-A)에서 삭제가 소수 배치에 뭉치면
  //     대부분의 배치가 이 경로를 탄다.
  // ==================================================================
  @Benchmark
  public int f_rangeCardFastPath() {
    return rangeCardFastPath();
  }

  private int rangeCardFastPath() {
    long n = rb.rangeCardinality(batchStart, (long) batchStart + batchSize);
    if (n == 0) {
      for (int r = 0; r < batchSize; r++) {
        mapping[r] = r;
      }
      return batchSize;
    }
    return forAllInRange();
  }

  // ==================================================================
  //  참고: buildIsDeleted (boolean[]) 경로 — ColumnarBatchUtil:110
  // ==================================================================
  @Benchmark
  public void g_isDeleted_baseline(Blackhole bh) {
    boolean[] isDeleted = new boolean[batchSize];
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (rpb.contains(batchStart + rowId)) {
        isDeleted[rowId] = true;
      }
    }
    bh.consume(isDeleted);
  }

  @Benchmark
  public void h_isDeleted_forEachInRange(Blackhole bh) {
    boolean[] isDeleted = new boolean[batchSize];
    int start = batchStart;
    rb.forEachInRange(start, batchSize, (org.roaringbitmap.IntConsumer)
        p -> isDeleted[p - start] = true);
    bh.consume(isDeleted);
  }
}
