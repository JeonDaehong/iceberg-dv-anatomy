/*
 * F-037 후속 — `mapping[live++]` 의 저장 주소 의존을 2×2 요인 설계로 가른다.
 *
 * 무엇이 남았나:
 *   F-034 가 반등의 미상 55% 에 대해 가설 하나를 남겼다 —
 *   "저장 주소가 분기 결과에 의존해 직렬화를 만든다."
 *   F-037 이 변형 6종으로 반등의 원인을 분기로 확정했지만, 이 가설만은
 *   **살지도 죽지도 못했다.** 이유가 명확하다:
 *
 *     v3_fixedAddrStore 는 주소를 live -> rowId 로 바꿨는데,
 *     그 변형에서 분기 실패도 1,084 -> 751 로 같이 줄었다.
 *     변형 하나가 두 가지를 바꿨으니 어느 쪽 몫인지 갈리지 않는다.
 *
 *   그리고 그 751 이라는 값 자체가 못 믿을 것이었다 — fork 3개에서 오차가 ±6,755 였다.
 *
 * 이번에 바꾸는 것 둘:
 *   1. **2×2 요인 설계.** 분기(O/X) × 주소(live/rowId) 네 칸을 다 만든다.
 *      그러면 주효과와 상호작용을 나눠 읽을 수 있다.
 *      F-037 은 대각선 두 칸(v1, v4)과 반쪽 한 칸(v3)만 있었다.
 *   2. **fork 를 3 -> 10 으로.** perfnorm 카운터의 오차가 판정을 못 하게 만들었으므로
 *      카운터부터 믿을 수 있게 만든다. ns/op 은 어차피 fork 수만큼 정확해진다.
 *
 *   설계:
 *                     주소 = live (분기 결과에 의존)   주소 = rowId (독립)
 *     분기 O            a_brLive                      b_brRow
 *     분기 X            c_noBrLive                    d_noBrRow
 *
 *   a 는 현행 Iceberg 다. c 는 F-037 의 v4_branchless 와 같다.
 *
 * 통제: F-037 과 동일하다 — 청크 122개, 두 밀도 모두 bitmap
 *       (d=8% -> 5,242 / d=50% -> 32,768, 경계 4,096), 같은 배치·버퍼.
 *
 * -- 반증 가능한 예측 (측정 전에 적는다) ------------------------------
 *
 *   S1. [설계 검증] d=50% 에서 네 변형의 분기 실패가 **두 그룹으로 깨끗이 갈린다** —
 *       분기 O(a, b) 는 500 이상, 분기 X(c, d) 는 50 미만.
 *       F-037 에서 v2·v3 이 JIT 때문에 중간값을 낸 것이 이 축을 망쳤으므로,
 *       이게 안 갈리면 아래를 채점하지 않는다. 여기서 멈춘다.
 *
 *   S2. [판정용 · 주소의 주효과] **분기가 없으면 주소는 공짜다** —
 *       c 와 d 의 d8->d50 증가폭 차이가 1.0 us/op 안이다.
 *       근거: 두 변형 모두 5,000번 무조건 저장한다. `live` 든 `rowId` 든
 *       주소는 순차이고, 분기가 없으면 둘 다 예측 가능하다.
 *       깨지면 주소 자체가 밀도를 타는 것이고 가설의 전제가 바뀐다.
 *
 *   S3. [판정용 · 상호작용] **분기가 있을 때만 주소가 값을 낸다** —
 *       (a − b) 의 증가폭 차이가 (c − d) 의 차이보다 **2배 이상** 크다.
 *       그러면 F-034 의 가설은 "주소 의존이 단독으로 비싼 게 아니라
 *       **분기와 결합할 때만** 비싸다" 로 살아난다.
 *       깨져서 (a − b) ≈ (c − d) 면 주소는 아무 몫도 없고 **가설은 죽는다.**
 *       F-037 이 낸 "반등은 전부 분기다" 가 문자 그대로 맞는 것이 된다.
 *
 *   S4. 네 변형 중 d=50% 에서 가장 느린 것은 a(현행)다.
 *       분기와 주소 의존이 둘 다 있는 유일한 칸이기 때문.
 *
 *   경고 — b, c, d 는 **대안 구현이 아니다.** 결과 mapping 이 현행과 다르다.
 *          기전 분해용 변형이다.
 * ---------------------------------------------------------------------
 */
package org.apache.iceberg.deletes;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.BitSet;
import java.util.Random;
import java.util.concurrent.TimeUnit;
import org.openjdk.jmh.annotations.*;
import org.roaringbitmap.RoaringBitmap;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MICROSECONDS)
@State(Scope.Thread)
@Fork(value = 10, jvmArgsAppend = {"-Xms2g", "-Xmx2g"})
@Warmup(iterations = 5, time = 1)
@Measurement(iterations = 8, time = 1)
public class DvAddrBench {

  private static final int CHUNK = 65536;
  private static final int NUM_CHUNKS = 122;

  @Param({"5000"})
  public int batchSize;

  @Param({"DENSE_8", "DENSE_50"})
  public String pattern;

  private RoaringPositionBitmap rpb;
  private int batchStart;
  private int[] mapping;

  @Setup(Level.Trial)
  public void setup() {
    double density;
    switch (pattern) {
      case "DENSE_8":  density = 0.08; break;
      case "DENSE_50": density = 0.50; break;
      default: throw new IllegalArgumentException(pattern);
    }

    Random rnd = new Random(20260907);   // F-037 과 같은 시드 — 같은 비트맵을 재려는 것이다.
    RoaringBitmap src = new RoaringBitmap();
    int target = (int) (CHUNK * density);
    for (int c = 0; c < NUM_CHUNKS; c++) {
      long base = (long) c * CHUNK;
      BitSet seen = new BitSet(CHUNK);
      int n = 0;
      while (n < target) {
        int p = rnd.nextInt(CHUNK);
        if (!seen.get(p)) {
          seen.set(p);
          n++;
          src.add((int) (base + p));
        }
      }
    }

    batchStart = (NUM_CHUNKS / 2) * CHUNK + CHUNK / 2 - batchSize / 2;

    RoaringPositionBitmap tmp = new RoaringPositionBitmap();
    src.forEach((org.roaringbitmap.IntConsumer) p -> tmp.set(Integer.toUnsignedLong(p)));
    tmp.runLengthEncode();
    ByteBuffer buf = ByteBuffer.allocate((int) tmp.serializedSizeInBytes())
        .order(ByteOrder.LITTLE_ENDIAN);
    tmp.serialize(buf);
    buf.flip();
    rpb = RoaringPositionBitmap.deserialize(buf);

    mapping = new int[batchSize];

    long del = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (rpb.contains(batchStart + rowId)) {
        del++;
      }
    }
    System.out.printf("%n  [setup] %-9s deletedInBatch=%,d/%d (%.1f%%)%n",
        pattern, del, batchSize, 100.0 * del / batchSize);
  }

  // ── 분기 O · 주소 = live  (현행 Iceberg) ────────────────────────────
  @Benchmark
  public int a_brLive() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (!rpb.contains(batchStart + rowId)) {
        mapping[live++] = rowId;
      }
    }
    return live;
  }

  // ── 분기 O · 주소 = rowId  (분기는 그대로, 주소 의존만 끊는다) ──────
  @Benchmark
  public int b_brRow() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (!rpb.contains(batchStart + rowId)) {
        mapping[rowId] = rowId;
        live++;
      }
    }
    return live;
  }

  // ── 분기 X · 주소 = live  (F-037 의 v4_branchless 와 동일) ──────────
  @Benchmark
  public int c_noBrLive() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      int d = rpb.contains(batchStart + rowId) ? 1 : 0;
      mapping[live] = rowId;
      live += 1 - d;
    }
    return live;
  }

  // ── 분기 X · 주소 = rowId  (둘 다 없는 기준칸) ──────────────────────
  @Benchmark
  public int d_noBrRow() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      int d = rpb.contains(batchStart + rowId) ? 1 : 0;
      mapping[rowId] = rowId;
      live += 1 - d;
    }
    return live;
  }
}
