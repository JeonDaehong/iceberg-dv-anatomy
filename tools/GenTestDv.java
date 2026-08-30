/*
 * dv_inspect.py 검증용 픽스처 생성기.
 *
 * Iceberg 의 실제 클래스(BitmapPositionDeleteIndex / RoaringPositionBitmap)로
 * 컨테이너 타입이 '알려진' DV blob 을 만들어 파일로 떨군다.
 * 그리고 RoaringBitmap 라이브러리 API 가 보고하는 실제 컨테이너 타입을
 * 정답지(expected.csv)로 함께 출력한다.
 *
 * dv_inspect.py 가 이 정답지와 일치하면, 파서가 리플렉션 없이
 * portable 포맷만으로 컨테이너를 정확히 식별한다는 뜻이다.
 *
 * package 가 org.apache.iceberg.deletes 인 이유:
 *   BitmapPositionDeleteIndex 와 RoaringPositionBitmap 이 package-private.
 */
package org.apache.iceberg.deletes;

import java.io.FileOutputStream;
import java.io.IOException;
import java.io.PrintWriter;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import org.roaringbitmap.RoaringBitmap;
import org.roaringbitmap.ContainerPointer;

public final class GenTestDv {

  private static final int CHUNK = 65536;

  private record Case(String name, String desc) {}

  public static void main(String[] args) throws IOException {
    Path out = Paths.get(args.length > 0 ? args[0] : "fixtures");
    Files.createDirectories(out);

    List<String> expected = new ArrayList<>();
    expected.add("case,chunk,container,cardinality,runs");

    // 청크 밀도를 array/bitmap 경계(4096/65536 = 6.25%) 양쪽으로 스윕.
    gen(out, expected, "sparse_0_5",  b -> fillRandom(b, 0, 0.005));
    gen(out, expected, "medium_5",    b -> fillRandom(b, 0, 0.05));
    gen(out, expected, "boundary_6",  b -> fillExact(b, 0, 4096));    // 정확히 경계
    gen(out, expected, "boundary_6b", b -> fillExact(b, 0, 4097));    // 경계 +1
    gen(out, expected, "dense_12",    b -> fillRandom(b, 0, 0.12));
    gen(out, expected, "run_contig",  b -> b.add(
        (long) (CHUNK * 0.35), (long) (CHUNK * 0.65)));
    gen(out, expected, "run_frag",    b -> { // 파편화된 run 여러 개
          for (int i = 0; i < 200; i++) {
            b.add((long) (i * 300), (long) (i * 300 + 120));
          }
        });
    gen(out, expected, "multi_chunk", b -> {  // 청크마다 다른 밀도
          fillRandom(b, 0 * CHUNK, 0.002);
          fillRandom(b, 1 * CHUNK, 0.10);
          b.add((long) (2L * CHUNK), (long) (2L * CHUNK + 30000));
        });
    gen(out, expected, "empty_ish",   b -> b.add(7));   // 단일 원소

    Files.write(out.resolve("expected.csv"), expected);
    System.out.println("\n정답지 -> " + out.resolve("expected.csv"));
    System.out.println("픽스처 -> " + out.toAbsolutePath());
  }

  private interface Filler { void fill(RoaringBitmap b); }

  private static void gen(Path dir, List<String> expected, String name, Filler f)
      throws IOException {
    RoaringBitmap src = new RoaringBitmap();
    f.fill(src);

    // Iceberg 쓰기 경로 재현: RoaringPositionBitmap 에 담고 -> serialize()
    // BitmapPositionDeleteIndex.serialize():126 이 runLengthEncode() 를 호출한다.
    BitmapPositionDeleteIndex idx = new BitmapPositionDeleteIndex();
    src.forEach((org.roaringbitmap.IntConsumer) p -> idx.delete(Integer.toUnsignedLong(p)));

    ByteBuffer blob = idx.serialize();
    byte[] bytes = new byte[blob.remaining()];
    blob.get(bytes);

    Path file = dir.resolve(name + ".dvblob");
    try (FileOutputStream fos = new FileOutputStream(file.toFile())) {
      fos.write(bytes);
    }

    // 정답지: 라이브러리 API 가 보고하는 실제 컨테이너 타입.
    // serialize() 가 runOptimize 를 걸었으므로 동일 변환을 적용해야 일치한다.
    RoaringBitmap after = src.clone();
    after.runOptimize();

    int n = 0;
    StringBuilder types = new StringBuilder();
    for (ContainerPointer cp = after.getContainerPointer(); cp.getContainer() != null; cp.advance()) {
      String type = containerType(cp);
      int card = cp.getCardinality();
      Integer runs = "run".equals(type) ? runCount(cp) : null;
      expected.add(String.format("%s,%d,%s,%d,%s",
          name, cp.key() & 0xFFFF, type, card, runs == null ? "" : runs.toString()));
      types.append(type.charAt(0));
      n++;
    }
    System.out.printf("  %-13s %,10d bytes  컨테이너 %d개 [%s]  카디널리티 %,d%n",
        name, bytes.length, n, types, after.getLongCardinality());
  }

  /** ContainerPointer 로부터 컨테이너 타입 판정 (리플렉션 없이 공개 API 로). */
  private static String containerType(ContainerPointer cp) {
    String cls = cp.getContainer().getClass().getSimpleName();
    if (cls.startsWith("Run")) return "run";
    if (cls.startsWith("Bitmap")) return "bitmap";
    if (cls.startsWith("Array")) return "array";
    return cls;
  }

  private static int runCount(ContainerPointer cp) {
    // RunContainer.numberOfRuns() 는 공개 API 가 아니므로
    // 직렬화 크기로 역산: 2 + 4*runs bytes
    return (cp.getContainer().getArraySizeInBytes() - 2) / 4;
  }

  private static void fillRandom(RoaringBitmap b, long base, double density) {
    Random rnd = new Random(20260817 + (int) base);
    int target = (int) (CHUNK * density);
    int added = 0;
    while (added < target) {
      int p = rnd.nextInt(CHUNK);
      if (!b.contains((int) (base + p))) {
        b.add((int) (base + p));
        added++;
      }
    }
  }

  private static void fillExact(RoaringBitmap b, long base, int count) {
    // 균등 간격이면 runOptimize 가 개입할 수 있으므로 의사난수로 흩뿌린다.
    Random rnd = new Random(42 + count);
    int added = 0;
    while (added < count) {
      int p = rnd.nextInt(CHUNK);
      if (!b.contains((int) (base + p))) {
        b.add((int) (base + p));
        added++;
      }
    }
  }
}
