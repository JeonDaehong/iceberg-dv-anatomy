# PR #18027 — 1차 리뷰 대응 기록 (2026-09-14)

리뷰어 **Péter Váry** (`pvary`, Iceberg PMC). 인라인 11건 + JMH 요청 1건, 총 12건.
`CHANGES_REQUESTED` 가 아니라 `COMMENTED` 였다 — 방향은 받아들여졌고 다듬는 단계다.

| | |
|---|---|
| 커밋 | `6d57d5b15` Core, Spark 4.2: Address review comments on forEachInRange |
| 변경 | 10개 파일, +451 / −283 |
| 테스트 | `core/deletes` 55 → **63**, `TestColumnarBatchUtil` 17, 실패 0 |
| CI | **42/42 통과** |
| 답글 | 인라인 11/11, JMH 1/1 |

---

## 1. 시그니처를 `(posStart, posEnd)` 로

> Other methods have `long posStart, long posEnd`. Shall we use that?

같은 인터페이스의 `delete(long posStart, long posEnd)` 가 이미 **시작 포함 / 끝 제외**를
쓴다. 맞는 지적이라 그대로 받았다.

**그런데 이름만 바꾸는 게 아니었다.** 옛 구현이 `length` 가 `int` 라는 데 기대고 있었고
주석에 그걸 명시해뒀다.

```java
// the range spans at most two keys because the length is bound by Integer.MAX_VALUE,
// which is smaller than the number of positions a single key covers
```

`posEnd` 가 `long` 이면 구간이 32비트 키 **세 개 이상**에 걸칠 수 있다. 그러면 중간 키는
32비트 공간 전체를 훑어야 하는데:

```
MAX_POS_32_BITS = 0xFFFFFFFF   (2^32 - 1)
lowEnd - lowStart = 2^32
(int) 2^32 = 0                  ← 아무것도 순회하지 않는다
```

**조용히 삭제를 놓친다.** 옛 코드에도 같은 식이 있었지만 "최대 두 키" 라 그 경로에
닿지 않았을 뿐이다. 시그니처를 바꾸는 순간 닿는다.

내부 헬퍼를 `int length` → `long lowStart, long lowEnd` 로 바꾸고 청크를 돌게 했다.
`RoaringBitmap.forEachInRange` 의 `length` 가 `int` 인 건 라이브러리 제약이라
우회할 수 없다.

실무에서 닿을 일은 없다 — 파일 하나가 42억 행을 넘어야 한다. 그래도 공개 API 가
`long` 을 받게 됐으니 맞아야 한다.

### 스타일은 추측하지 않고 베꼈다

같은 파일에 `setRange(long posStartInclusive, long posEndExclusive)` 가 이미 있었다.
파라미터 이름, 예외 문구, 빈 구간 처리를 **그대로** 가져왔다. 덕분에 아래 2번 지적이
같이 해결됐다.

---

## 2. 잘못된 구간에 예외

> Shall we throw instead?

`if (length <= 0) return;` 이 이상한 입력을 조용히 삼키고 있었다.
`setRange` 와 같은 문구를 쓴다.

```java
Preconditions.checkArgument(
    posStartInclusive <= posEndExclusive,
    "Start position must not exceed end position: [%s, %s)",
    posStartInclusive,
    posEndExclusive);
```

`start == end` 는 정상(no-op), `start > end` 만 예외. 이것도 `setRange` 와 같다.

---

## 3. javadoc 에서 구현 설명 제거

> No impl details in the javadoc

*"비트맵이 컨테이너를 한 번 찾아서 훑는다"* 같은 내부 설명을 인터페이스 주석에서 뺐다.
구현이 바뀌면 주석이 거짓말이 된다.

---

## 4~7. `ColumnarBatchUtil` 네 건

| 지적 | 대응 |
|---|---|
| 삭제가 없으면 건너뛸 수 있나 | `deletedPositions.isEmpty()` 면 순회 전에 `null` 반환 |
| `IsDeletedBuilder.accept` 안에서 세면 되지 않나 | `DeleteFilter` 를 넘겨 `accept` 안에서 증가. **카운터 필드와 getter 가 통째로 사라졌다** |
| 카운트 루프가 읽기 어렵다 (`suggestion` 제공) | **버튼을 안 눌렀다.** 위 방식을 `RowIdMappingBuilder` 에도 적용하니 루프 자체가 없어졌다 |
| `build()` 는 build 가 아니다 | `appendRemainingLiveRows()` + `liveRowCount()` 로 분리 |

순 **11줄 줄었다.** 두 개의 카운트 루프가 `accept()` 안 한 줄로 들어갔다.

`suggestion` 을 안 누른 건 **답글에 이유를 반드시 적어야 했다.** 안 그러면 무시한 걸로
보인다.

---

## 8~11. 테스트 네 건

> Why new test class? Shall we put these into `TestRoaringPositionBitmap`, or
> `TestBitmapPositionDeleteIndex`?

227줄짜리 `TestPositionDeleteIndexForEachInRange.java` 를 지우고 **대상별로 나눴다.**

| 어디로 | 무엇 | 왜 |
|---|---|---|
| `TestRoaringPositionBitmap` (+9) | 컨테이너·키 경계, 세 키 걸침, 미할당 키, run 인코딩, 잘못된 구간 예외 | **비트맵이 하는 일** |
| `TestBitmapPositionDeleteIndex` (+8) | 빈 인덱스, 경계, 오름차순, `isDeleted` 와 무작위 대조 | **인덱스 표면의 계약** |

합치면서 **없던 테스트 둘을 추가**했다:

- `testForEachInRangeInvalidRange` — 2번으로 생긴 새 동작
- `testForEachInRangeSpanningThreeKeys` — **1번의 `int` 넘침 경로.** 이게 없으면 그 버그를 못 잡는다

두 번째는 옆에 `testAddRangeSpanningThreeKeys` 가 이미 있어서 이름과 모양을 맞췄다.

### `package private` 과 `test` 접두어 지적은 저절로 해소됐다

새 파일이 없어졌으니 해당 사항이 없다. 기존 파일에 들어가면 **그 파일 스타일**
(`public void testX`)을 따르는 게 맞다.

조사해보니 저장소 관례가 **섞여 있다** — 최근 추가된 테스트에도 `public class` 와
`class` 가 둘 다 있다. 그래서 답글에 *"원하면 두 파일 다 새 관례로 바꾸겠다"* 를 덧붙였다.

나머지 하나(`TestColumnarBatchUtil` 완전한정명)는 `java.util.Random` 을 import 로.

---

## 12. JMH 벤치

> Could you please add JMH test to show the gains?

**저장소에 코드를 넣어달라는 것**이었다. 확인해보니 명확하다.

```
JMH 벤치 파일        199개    ← 코드는 커밋한다
커밋된 결과 파일        0개
.gitignore:42   */benchmark/*   ← 결과는 무시된다
```

`core/src/jmh/java/org/apache/iceberg/deletes/PositionDeleteIndexBenchmark.java` 를
추가했다. 옆 파일 `RoaringPositionBitmapBenchmark` 의 관례를 맞췄다 — `@Fork(1)`,
`SingleShotTime`, `@Timeout(5분)`, javadoc 에 `./gradlew …` 실행법.

우리 `bench/DvBatchBench.java` 는 `@Fork(3)` / `AverageTime` 이라 **그대로 옮기면 안 됐다.**

### 결과 (JMH 1.37, JDK 21.0.10)

```
Benchmark                        (density)  Mode  Cnt    Score    Error  Units
probePerPosition                       0.5    ss    5   82.893 ± 13.257  ms/op
probePerPosition                       6.1    ss    5  111.101 ± 43.291  ms/op
probePerPosition                      12.0    ss    5   68.924 ± 18.444  ms/op
traverseRange                          0.5    ss    5    0.369 ±  0.378  ms/op
traverseRange                          6.1    ss    5    1.304 ±  0.891  ms/op
traverseRange                         12.0    ss    5    2.456 ±  1.660  ms/op
```

| 밀도 | 컨테이너 | 행마다 | 구간 순회 |
|---|---|---|---|
| 0.5% | array | 82.9 ± 13.3 | 0.37 ± 0.38 |
| **6.1%** | array | **111.1 ± 43.3** | 1.30 ± 0.89 |
| 12.0% | bitmap | 68.9 ± 18.4 | 2.46 ± 1.66 |

> **배수를 적지 않는다.** 구간 순회 쪽은 오차가 값에 맞먹거나 더 크다
> (0.5% 는 `0.369 ± 0.378`). 분모가 그 폭으로 흔들리면 "225배" 같은 특정 값을
> 주장할 근거가 없다. **말할 수 있는 건 두 팔이 두 자릿수 떨어져 있다는 것까지**이고,
> PR 코멘트에도 그렇게만 적었다. 배수를 정확히 내려면 반복을 늘려 다시 재야 한다.

**8월에 Spark 프로파일로 본 비단조 곡선이 순수 JMH 에서도 그대로 나왔다.**
6.1% 가 정점이고 12% 는 더 싸다.

---

## 숫자가 세 개 돌아다닌다 — 섞으면 안 된다

| 출처 | 무엇을 재나 | 배수 |
|---|---|---|
| **새 JMH** | 파일 전체(청크 약 77개) | 두 자릿수 (배수는 노이즈로 확정 불가) |
| 옛 마이크로벤치 (F-002) | 청크 **1개**로 컨테이너 타입만 분리 | 5~144배 |
| Spark 실측 (F-011) | 실제 스캔의 delete-check CPU | **2.6~9.3배** |

### 옛 벤치와 새 벤치가 다른 이유

`isDeleted` 한 번은 두 단계다 — **어느 32비트 비트맵인지 찾고**, 그 안에서 찾는다.

옛 벤치는 청크를 하나만 만들어 1단계를 사실상 0으로 만들었다. 컨테이너 타입만
분리하려는 의도였다. 새 벤치는 500만 위치라 비트맵이 여러 개고, **1단계가 실제 비용이
된다.** 그래서 `19.76 → 68.92 µs/batch` 로 비싸졌다.

그리고 **구간 순회는 1단계를 배치당 한 번으로 상각**하므로 격차가 벌어진다.
다만 그 격차가 정확히 몇 배인지는 위 오차 때문에 말할 수 없다.

**패치가 좋아진 게 아니라 측정 범위가 넓어진 것이다.** 옛 숫자가 과소평가였다.
그리고 사용자가 체감하는 값은 여전히 Spark 쪽 2.6~9.3배다.

이 설명을 JMH 답변에 넣지 않으면 *"225배라며? 본문엔 9.3배던데?"* 를 듣는다.

---

## 오늘 배운 것

- **시그니처를 바꾸면 그 타입에 기대던 불변식이 같이 깨진다.** 주석에 적혀 있던
  "최대 두 키" 가 그 신호였다. 주석을 지우기 전에 왜 있었는지 읽어야 한다.
- **스타일은 추측하지 말고 옆 코드에서 베낀다.** `setRange` 를 본떴더니 지적 두 개가
  한 번에 해결됐고, 리뷰어가 보기에 "원래 있던 코드" 처럼 읽힌다.
- **관례는 확인하고 나서 따른다.** `package private` 지적이 저장소 전체의 규칙인 줄
  알았는데 최근 파일에도 두 스타일이 섞여 있었다. 확인 안 했으면 기존 파일을 잘못
  고쳤을 것이다.
- **리뷰어 제안을 안 따를 거면 이유를 반드시 적는다.** `suggestion` 버튼을 안 누른 건
  더 나은 방법이 있어서였지만, 말하지 않으면 무시로 읽힌다.
- **벤치는 무엇을 포함하고 무엇을 뺐는지가 숫자보다 중요하다.** 같은 패치가
  청크 하나에서는 5배, 파일 전체에서는 두 자릿수로 나온다.
- **새 벤치의 배수를 내가 표로 적었다가 지웠다.** 구간 순회 쪽 오차가 값보다 커서
  이 프로젝트 규칙(노이즈 안의 차이는 주장하지 않는다)에 걸린다. 다행히 PR 코멘트에는
  처음부터 "두 자릿수 차이" 로만 적었다.

---

## 남은 것

- [ ] pvary 의 재검토
- [ ] 승인되면 **v3.5 / v4.0 / v4.1 백필** 후속 PR (`ColumnarBatchUtil` 이 바이트 단위로 동일)
- [ ] 머지되면 블로그 §2.7 의 *"리뷰를 기다리는 중"* 갱신, LinkedIn
- [ ] 리뷰 끝날 때까지 프로파일(19 GB)과 `~/opt/jars-{baseline,patched}/` 유지

**Resolve 는 누르지 않는다** — 리뷰어가 확인하고 닫는다.
