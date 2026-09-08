/*
 * F-032 / F-034 후속 — bitmap 구간 '반등' 의 나머지 55% 를 변형 벤치로 가른다.
 *
 * 무엇이 남아 있나:
 *   F-008 이 d=7% 이후 비용이 다시 오르는 것을 봤고(반등), F-032 가 그 순증의
 *   28.5% 를 분기 오예측으로, F-034 가 17% 를 DeleteCounter.increment 로 설명했다.
 *   **약 55% 는 이름이 없다.** F-034 가 남긴 가설은 하나다 —
 *   `mapping[live++] = rowId` 의 저장이 분기 결과에 의존해 직렬화를 만든다.
 *   F-034 는 이미 찍은 프로파일을 다시 읽은 **탐색적 분해**였으므로 판정이 아니다.
 *   여기서 예측을 걸고 변형을 만들어 다시 친다.
 *
 * 왜 마이크로벤치인가:
 *   Spark 경로에서는 루프 몸통을 뜯어낼 수 없다. 저장을 없앤 Iceberg,
 *   분기를 없앤 Iceberg 를 각각 빌드할 수는 있지만 그건 여섯 번의 빌드다.
 *   그리고 async-profiler 의 프레임 귀속은 JIT 인라이닝에 흔들린다
 *   (F-034 의 -4,160 / +10,176 이 서로 상쇄하는 모양이 정확히 그 냄새다).
 *   ns/op 는 귀속에 면역이다.
 *
 * 통제:
 *   - 두 밀도 모두 bitmap 컨테이너다 (8% -> 5,243, 50% -> 32,768, 경계 4,096).
 *     따라서 컨테이너 타입 축(F-001)이 아니라 루프 몸통 축만 남는다.
 *   - 청크를 122개 채운다. Spark 의 DV 하나는 데이터 파일 하나(8M행=122청크)를
 *     덮으므로 최상위 RoaringArray 이진 탐색 깊이가 실제와 같아야 한다.
 *     DvBatchBench 는 청크 1개라 이 깊이가 없다 — 그래서 별도 클래스다.
 *   - 모든 변형이 같은 배치, 같은 비트맵, 같은 버퍼를 쓴다.
 *
 * -- 반증 가능한 예측 (측정 전에 적는다) ------------------------------
 *
 *   R1. [설계 검증] 반등이 여기서 재현된다 — v1_full 의 ns/op 가
 *       d=50% 에서 d=8% 보다 크다.
 *       깨지면: 반등은 Spark 경로 고유(GC·배치 재구성·메모리 대역)이고
 *       루프 몸통 가설 전체가 틀린 자리를 보고 있었다는 뜻이다. 여기서 멈춘다.
 *
 *   R2. v5_lookupOnly 는 d=50% 가 d=8% 보다 느리지 않다 (±10% 안 또는 더 빠름).
 *       F-034 가 "조회 기계장치는 오히려 싸진다"(-6,000 샘플) 를 봤고,
 *       bitmap 의 비트 테스트는 밀도와 무관한 O(1) 이기 때문이다.
 *       깨지면 F-034 의 분해가 틀렸고 조회가 주범이다.
 *
 *   R3. [판정용 · 분기] v4_branchless 의 d8->d50 증가폭이 v1_full 증가폭의
 *       절반 미만이다. 즉 분기를 지우면 반등의 절반 이상이 사라진다.
 *       F-032 는 분기의 설명력을 28.5% 로 쟀는데, 그건 '분기 오예측 사이클' 만
 *       센 하한이다. 분기가 만드는 파이프라인 직렬화까지 포함하면 더 클 것이다.
 *       깨지면(증가폭이 절반 이상 남으면) 분기는 하한 그대로이고 다른 게 있다.
 *
 *   R4. [판정용 · 저장] v2_noStore 의 d8->d50 증가폭이 v1_full 증가폭의
 *       80% 이상 남는다. 즉 저장을 지워도 반등은 거의 그대로다.
 *       => F-034 가 남긴 `mapping[live++]` 주소 의존 가설은 죽는다.
 *       근거: d=50% 는 d=8% 보다 저장을 더 적게 한다(2,500회 vs 4,600회).
 *       일을 덜 하는 쪽이 비싸질 수는 없다. 저장이 주범이라면 부호가 반대여야 한다.
 *       깨지면(증가폭이 크게 줄면) 저장의 주소 의존이 진짜였다는 뜻이고
 *       F-034 의 가설이 산다.
 *
 *   R5. v6_withCounter 와 v1_full 의 차이가 d=50% 에서 더 크다.
 *       DeleteCounter 는 삭제 행마다 1회이므로 호출이 d=8% 400회 vs
 *       d=50% 2,500회다. F-034 의 17% 를 독립적으로 재현한다.
 *
 *   경고 — 이 벤치가 재지 못하는 것: 실제 배치 재구성, Parquet 디코딩과의 간섭,
 *          멀티코어 메모리 대역. 방향과 몫만 본다.
 * ---------------------------------------------------------------------
 *
 * 패키지가 org.apache.iceberg.deletes 인 이유는 DvBatchBench 와 같다 —
 * RoaringPositionBitmap 과 DeleteCounter 가 package-private 이기 때문이다.
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
@Fork(value = 3, jvmArgsAppend = {"-Xms2g", "-Xmx2g"})
@Warmup(iterations = 5, time = 1)
@Measurement(iterations = 8, time = 1)
public class DvReboundBench {

  private static final int CHUNK = 65536;

  /** Spark 의 DV 하나가 덮는 데이터 파일 = 8,000,000행 / 65,536 = 122 청크. */
  private static final int NUM_CHUNKS = 122;

  @Param({"5000"})
  public int batchSize;

  /** 둘 다 bitmap 컨테이너다. 바뀌는 것은 루프 몸통이 만나는 삭제 비율뿐이다. */
  @Param({"DENSE_8", "DENSE_50"})
  public String pattern;

  private RoaringPositionBitmap rpb;
  private int batchStart;
  private int[] mapping;
  private long deletedInBatch;

  @Setup(Level.Trial)
  public void setup() {
    double density;
    switch (pattern) {
      case "DENSE_8":  density = 0.08; break;
      case "DENSE_50": density = 0.50; break;
      default: throw new IllegalArgumentException(pattern);
    }

    Random rnd = new Random(20260907);
    RoaringBitmap src = new RoaringBitmap();
    int target = (int) (CHUNK * density);
    for (int c = 0; c < NUM_CHUNKS; c++) {
      long base = (long) c * CHUNK;
      // 청크마다 목표 카디널리티를 정확히 채운다 (컨테이너 타입을 결정하는 단위).
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

    // 배치는 가운데 청크 중앙에 둔다 — 청크 경계 효과를 피하고,
    // 최상위 이진 탐색이 매번 같은 깊이를 타게 한다.
    batchStart = (NUM_CHUNKS / 2) * CHUNK + CHUNK / 2 - batchSize / 2;

    // 쓰기 경로 재현: runLengthEncode -> serialize -> deserialize.
    // 이걸 건너뛰면 실제와 다른 컨테이너를 재게 된다 (DvBatchBench 와 같은 이유).
    RoaringPositionBitmap tmp = new RoaringPositionBitmap();
    src.forEach((org.roaringbitmap.IntConsumer) p -> tmp.set(Integer.toUnsignedLong(p)));
    tmp.runLengthEncode();
    ByteBuffer buf = ByteBuffer.allocate((int) tmp.serializedSizeInBytes())
        .order(ByteOrder.LITTLE_ENDIAN);
    tmp.serialize(buf);
    buf.flip();
    rpb = RoaringPositionBitmap.deserialize(buf);

    mapping = new int[batchSize];

    deletedInBatch = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (rpb.contains(batchStart + rowId)) {
        deletedInBatch++;
      }
    }

    long chunkCard = 0;
    long chunkBase = (long) (NUM_CHUNKS / 2) * CHUNK;
    for (int p = 0; p < CHUNK; p++) {
      if (rpb.contains(chunkBase + p)) {
        chunkCard++;
      }
    }
    System.out.printf(
        "%n  [setup] pattern=%-9s chunks=%d  chunkCardinality=%,6d (%s)  "
            + "batch[%d,%d)  deletedInBatch=%,d/%d (%.1f%%)%n",
        pattern, NUM_CHUNKS, chunkCard, chunkCard > 4096 ? "bitmap" : "array",
        batchStart, batchStart + batchSize, deletedInBatch, batchSize,
        100.0 * deletedInBatch / batchSize);
  }

  // ==================================================================
  //  v1 — 현행 Iceberg (ColumnarBatchUtil.buildRowIdMapping 몸통 그대로)
  //       분기 O, 저장 O(live 주소), 카운터 X
  // ==================================================================
  @Benchmark
  public int v1_full() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (!rpb.contains(batchStart + rowId)) {
        mapping[live++] = rowId;
      }
    }
    return live;
  }

  // ==================================================================
  //  v2 — 저장 제거. 분기와 조회는 그대로.
  //       v1 대비 사라진 것: 배열 저장 + live 주소 의존.
  //       R4 는 이 변형의 증가폭으로 판정한다.
  // ==================================================================
  @Benchmark
  public int v2_noStore() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (!rpb.contains(batchStart + rowId)) {
        live++;
      }
    }
    return live;
  }

  // ==================================================================
  //  v3 — 저장은 하되 주소를 분기와 무관하게(rowId). live 카운터는 유지.
  //       v1 과의 차이는 오직 저장 주소가 live 냐 rowId 냐 뿐이다.
  //       F-034 가설이 옳다면 v3 에서 반등이 눈에 띄게 줄어야 한다.
  // ==================================================================
  @Benchmark
  public int v3_fixedAddrStore() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (!rpb.contains(batchStart + rowId)) {
        mapping[rowId] = rowId;
        live++;
      }
    }
    return live;
  }

  // ==================================================================
  //  v4 — 분기 제거(무조건 저장 + 산술로 live 갱신). 저장 주소는 여전히 live.
  //       예측 실패가 사라지므로 R3 은 이 변형으로 판정한다.
  //       주의: 결과 mapping 은 v1 과 같지 않다(삭제 행 자리에 덮어쓰기가 남는다).
  //             이건 기전 분해용 변형이지 대안 구현이 아니다.
  // ==================================================================
  @Benchmark
  public int v4_branchless() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      int d = rpb.contains(batchStart + rowId) ? 1 : 0;
      mapping[live] = rowId;
      live += 1 - d;
    }
    return live;
  }

  // ==================================================================
  //  v5 — 조회만. 저장도 조건 분기도 없다(누산은 산술).
  //       R2 는 이 변형으로 판정한다: bitmap 비트 테스트가 밀도를 타는가.
  // ==================================================================
  @Benchmark
  public int v5_lookupOnly() {
    int acc = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      acc += rpb.contains(batchStart + rowId) ? 1 : 0;
    }
    return acc;
  }

  // ==================================================================
  //  v6 — v1 + DeleteCounter.increment (삭제 행마다 1회).
  //       실제 Iceberg 는 이걸 같이 부른다. F-034 의 17% 를 독립 재현한다.
  // ==================================================================
  private final DeleteCounter counter = new DeleteCounter();

  @Benchmark
  public int v6_withCounter() {
    int live = 0;
    for (int rowId = 0; rowId < batchSize; rowId++) {
      if (rpb.contains(batchStart + rowId)) {
        counter.increment();
      } else {
        mapping[live++] = rowId;
      }
    }
    return live;
  }
}
