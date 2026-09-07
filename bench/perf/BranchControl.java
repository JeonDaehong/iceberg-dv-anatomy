// vPMU 통제 테스트 — "WSL2 의 branch-misses 는 진짜 세고 있는가?"
//
// 왜 필요한가:
//   본 측정에서 array 컨테이너의 분기 예측 실패가 행당 0.008 회로 나왔다.
//   이게 "정말 안 틀린다" 인지 "vPMU 가 못 센다" 인지 구분하지 못하면
//   F-001 기전 반증은 성립하지 않는다. 그래서 답을 아는 워크로드를 먼저 잰다.
//
// 통제군 설계 (고전적인 sorted-vs-unsorted 분기 예측 실험):
//   같은 데이터, 같은 명령어 수, 같은 접근 패턴. 차이는 분기 결과의 예측 가능성뿐.
//     SORTED   : 값이 오름차순 -> `v >= 128` 이 한 번만 뒤집힌다 -> 거의 100% 예측 적중
//     SHUFFLED : 값이 무작위   -> 매번 동전 던지기 -> 이론상 50% 실패
//   N=1<<20 이면 SHUFFLED 는 약 50 만 회 실패해야 한다.
//
// 판정:
//   SHUFFLED 의 branch-misses 가 SORTED 보다 수십~수백 배 크게 나오면 vPMU 는 신뢰할 수 있다.
//   둘이 비슷하면 카운터가 죽은 것이고, 본 측정의 0.008 은 아무 것도 뜻하지 않는다.
public class BranchControl {
  static final int N = 1 << 20;

  public static void main(String[] a) {
    boolean sorted = a.length > 0 && a[0].equals("sorted");
    int[] v = new int[N];
    java.util.Random r = new java.util.Random(42);
    for (int i = 0; i < N; i++) v[i] = r.nextInt(256);
    if (sorted) java.util.Arrays.sort(v);

    long sum = 0;
    // JIT 워밍업 후 본 구간. perf 는 프로세스 전체를 세므로 반복 횟수를 맞춰둔다.
    for (int rep = 0; rep < 200; rep++) {
      for (int i = 0; i < N; i++) {
        if (v[i] >= 128) sum += v[i];   // <- 측정 대상 분기
      }
    }
    System.out.println((sorted ? "sorted  " : "shuffled") + " sum=" + sum);
  }
}
