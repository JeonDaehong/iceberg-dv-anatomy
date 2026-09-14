# PR #18027 리뷰 대응 — pvary 코멘트 12건

> 2026-09-14 07:38~07:56 UTC, 인라인 11건 + JMH 요청 1건.
> `COMMENTED` 리뷰이지 `CHANGES_REQUESTED` 가 아니다 — 방향은 받아들여졌고
> 다듬자는 단계다. 리뷰어가 이 정도로 꼼꼼히 보는 건 머지할 생각이 있다는 뜻이다.

---

## A. 무거운 것 둘

### A-1. 시그니처를 `(posStart, posEnd)` 로 바꾼다

> `PositionDeleteIndex.java:95` — Other methods have `long posStart, long posEnd`. Shall we use that?

**받아들여야 한다.** 같은 인터페이스에 이미 규약이 있다.

```java
/**
 * @param posStart inclusive beginning of position range
 * @param posEnd exclusive ending of position range
 */
void delete(long posStart, long posEnd);
```

`delete` 가 **시작 포함 / 끝 제외**를 쓰므로 `forEachInRange` 도 같아야 한다.

**그런데 이건 이름만 바꾸는 게 아니다.** 지금 구현은 `length` 가 `int` 라는 것에
기대고 있다. `RoaringPositionBitmap.java:220` 의 주석이 그걸 명시한다.

```java
// the range spans at most two keys because the length is bound by Integer.MAX_VALUE,
// which is smaller than the number of positions a single key covers
```

`posEnd` 가 `long` 이 되면 **이 전제가 깨진다.** 구간이 세 개 이상의 32비트 키에
걸칠 수 있으므로 키 순회 루프가 실제로 일반화돼야 한다(지금 루프는 이미 `for` 지만
주석과 경계 계산이 "최대 2개" 를 전제한다).

같이 따라오는 것:

- [ ] `PositionDeleteIndex.forEachInRange` 기본 구현 — `for (long pos = posStart; pos < posEnd; pos++)`
- [ ] `RoaringPositionBitmap.forEachInRange` — `length <= 0` → `posStart >= posEnd`
- [ ] "최대 두 키" 주석 삭제 또는 일반화
- [ ] `BitmapPositionDeleteIndex` 위임부
- [ ] `ColumnarBatchUtil` 호출부 두 곳 — `batchSize` → `rowStartPosInBatch + batchSize`
- [ ] 테스트의 경계 조건 — `length 0/음수` 가 `posStart == posEnd` / `posStart > posEnd` 로 바뀐다
- [ ] **이슈 #18026 본문의 `Proposal` 절** — 시그니처와 "at most two keys" 설명이 거기 그대로 있다

### A-2. JMH 벤치를 저장소 안에 넣는다

> Could you please add JMH test to show the gains?

**자리가 이미 있다.**

```
core/src/jmh/java/org/apache/iceberg/deletes/RoaringPositionBitmapBenchmark.java
```

같은 디렉터리에 형식을 맞춰 넣으면 된다. **우리 `bench/DvBatchBench.java` 를 그대로
옮기면 안 된다** — 관례가 다르다.

| | Iceberg 기존 벤치 | 우리 벤치 |
|---|---|---|
| Fork | `@Fork(1)` | `@Fork(3)` |
| 모드 | `Mode.SingleShotTime` | `Mode.AverageTime` |
| 타임아웃 | `@Timeout(5, MINUTES)` | 없음 |
| 실행법 주석 | javadoc 에 `./gradlew :iceberg-core:jmh -PjmhIncludeRegex=…` | 없음 |

우리 쪽 결과(64.99 → 0.45 등)는 **PR 코멘트에 숫자로 남기고**, 저장소에는
재현 가능한 최소 벤치만 넣는 게 맞다. 리뷰어가 원하는 건 "직접 돌려볼 수 있는 것" 이다.

---

## B. `ColumnarBatchUtil` — 네 건

| 줄 | 지적 | 대응 |
|---|---|---|
| 100 | Could we shortcut if there are no deletes? | `deletedPositions.isEmpty()` 면 순회 전에 `null` 반환 |
| 110 | 삭제 카운트 루프가 읽기 어렵다 (`suggestion` 블록 제공) | **그대로 적용.** GitHub 에서 `Commit suggestion` 한 번이면 된다 |
| 156 | `IsDeletedBuilder.accept` 안에서 호출하면 되지 않나 | `incrementDeleteCount()` 를 콜백 안으로 — 루프 하나가 사라진다 |
| 249 | this is not a `build` method | 이름 바꾼다. `build()` → `liveRowCount()` 같은 것 |

110번 제안 블록:

```java
int deletedRowCount = batchSize - liveRowId;
for (int i = 0; i < deletedRowCount; i++) {
  deletes.incrementDeleteCount();
}
```

156번을 받으면 110번 루프 자체가 없어질 수 있으니 **156 먼저 보고 110 을 정한다.**

---

## C. Javadoc·예외 — 두 건

| 위치 | 지적 | 대응 |
|---|---|---|
| `PositionDeleteIndex.java:89` | No impl details in the javadoc | 구현 설명(키 추출·컨테이너 조회 등)을 빼고 계약만 남긴다 |
| `RoaringPositionBitmap.java:210` | Shall we throw instead? | `if (length <= 0) return;` → 잘못된 구간은 예외 |

`RoaringPositionBitmap` 은 내부 클래스이므로 예외를 던져도 공개 계약에 영향이 없다.
다만 **인터페이스 기본 구현과 동작이 갈리면 안 된다** — 둘 다 같은 규칙이어야 한다.
`posStart == posEnd`(빈 구간)는 정상으로 두고, `posStart > posEnd` 만 던지는 게 자연스럽다.

---

## D. 테스트 — 네 건

| 위치 | 지적 |
|---|---|
| `TestPositionDeleteIndexForEachInRange.java:36` | **왜 새 테스트 클래스인가. `TestRoaringPositionBitmap` 이나 `TestBitmapPositionDeleteIndex` 에 넣으면 안 되나** |
| `:36` | 클래스를 package private 으로 |
| `:42` | 메서드도 package private, 그리고 **새 테스트에는 `test` 접두어를 쓰지 않는다** |
| `TestColumnarBatchUtil.java:327` | nit: 완전한정명 쓰지 말 것 |

첫 번째가 실질적이다. **227줄짜리 새 파일을 없애고 기존 두 클래스에 나눠 넣는 쪽**이
리뷰어 의도로 보인다 — 비트맵 수준 테스트는 `TestRoaringPositionBitmap`,
인덱스 수준은 `TestBitmapPositionDeleteIndex`.

나머지 셋은 Iceberg 테스트 관례다. 기존 파일들을 열어 확인하고 맞추면 된다.

---

## 순서

1. **A-1 시그니처** — 나머지가 전부 여기 딸려 움직인다. 먼저 한다
2. **B 156 → 110 → 100 → 249**
3. **C**
4. **D** — 테스트를 옮기면서 A-1 의 경계 조건도 같이 고친다
5. **A-2 JMH**
6. 빌드·스포트리스·테스트
7. **이슈 #18026 의 `Proposal` 절 갱신** — 시그니처가 바뀌었으므로

## 답변할 때

12건 중 **받아들이지 않을 것이 있으면 그것만 이유를 적는다.** 나머지는 고치고
"done" 이면 충분하다. 지금은 전부 받아들일 만해 보인다.

A-1 은 받아들이되 **"최대 두 키" 전제가 깨진다는 걸 먼저 말해두는 게 좋다.**
리뷰어가 모르고 제안했을 수 있고, 알고도 제안했다면 내가 이해했다는 신호가 된다.
