# PR #18027 — `RoaringBatchIterator` 제안에 대한 답변

> 제안 내용: Richard Startin 의 batch iteration 글을 참조하며,
> `RoaringBatchIterator` 를 뒤에서 쓰는 `PositionBatchIterator` 같은 것이
> 더 나을 수 있지 않느냐.
>
> **운 좋게도 이미 쟀다.** 2026-08-17 프로토타입 단계에서 여섯 구현을 같은 조건으로
> 비교했고 `BatchIterator` 가 그중 E 였다. 채택한 `forEachInRange` 보다
> **1.4~1.5배 느렸다.**
>
> 다만 정직하게 인정할 구멍이 하나 있다 — 우리 벤치는 **배치마다 이터레이터를
> 새로 만든다.** 제안자가 말한 게 "파일 전체에 걸쳐 살아 있는 이터레이터" 라면
> 그건 안 잰 것이다. 답변에 그 구분을 넣는다.

---

## 답변 (그대로 붙여넣기)

```markdown
Good pointer — I did benchmark `BatchIterator` as one of six candidate
implementations before picking this one, and it came out slower. Same JMH
harness, 5,000-row batch, chunk filled to the target density and round-tripped
through `BitmapPositionDeleteIndex.serialize()`/`deserialize()` so the containers
match what the read path sees. Time per batch in µs, lower is better:

| implementation | sparse 0.5% | medium 5% | dense 12% |
|---|---|---|---|
| current (`isDeleted` per row) | 64.99 | 80.22 | 19.76 |
| `forAllInRange` + `RelativeRangeConsumer` | **0.49** | **1.91** | **3.07** |
| `forEachInRange` + gap fill (this PR) | 0.45 | 1.87 | 4.16 |
| **`BatchIterator`** | **0.63** | **2.79** | **5.92** |

Two reasons it loses, I think:

- **It needs two passes.** `nextBatch(int[])` hands back the deleted positions in
  a buffer, and the mapping still has to be built from them afterwards.
  `forEachInRange` writes into the mapping as it goes, so the deleted positions
  are never materialised.
- **There is no bulk "everything here is absent" callback.** That is what makes
  `forAllInRange` the fastest column above — `acceptAllAbsent(from, to)` skips a
  clean stretch in O(1), and on run containers it is 2.7x faster than
  `forEachInRange` for exactly that reason. A batch iterator has to enumerate.

One thing my benchmark does **not** answer, and it may be the version you have in
mind: it constructs the iterator per batch. A `PositionBatchIterator` held open
across the batches of a file would amortise the container lookup over the whole
file instead of per batch, and would not allocate per batch. I would expect that
to recover part of the gap but not to pass `forAllInRange`, since the two-pass
and no-bulk-skip issues remain — the per-batch lookup is already amortised over
5,000 rows, so there is not much left there. Happy to add that variant and
measure it if you think it is worth pinning down.

There is also an API-shape question. A stateful iterator is a larger addition to
`PositionDeleteIndex` than a `default` method whose default implementation is the
current loop, and it brings lifecycle with it (create, reset, reuse across
batches). Since whether to grow this interface at all is the open question on the
dev list, I leaned towards the smallest thing that got the measured win. If the
consensus is that a batch iterator is the better abstraction to expose, I am
happy to go that way.
```

---

## 왜 이렇게 썼나

- **먼저 숫자를 준다.** 제안을 받았을 때 "이미 검토했다" 만 말하면 방어적으로 읽힌다.
  표를 먼저 놓으면 검토했다는 게 저절로 증명된다.
- **"I think" 를 붙였다.** 두 이유는 측정이 아니라 해석이다. 측정한 것과
  추론한 것을 섞어 말하면 안 된다.
- **안 잰 것을 먼저 밝힌다.** 배치마다 이터레이터를 만든다는 건 실제 한계이고,
  제안자가 말한 게 그쪽일 가능성이 높다. 내가 먼저 말하지 않으면 상대가 찾아낸다.
- **재보겠다고 제안한다.** 이 프로젝트에서 논쟁을 끝내는 방식이 그거고,
  하루면 된다.
- **API 이야기를 마지막에.** 성능으로 먼저 답하고 나서 설계 이야기를 꺼내야
  "숫자로 졌으니 설계로 반박한다" 로 안 읽힌다.
- **`RUN` 열을 표에서 뺐다.** 그 열은 배치 전체가 삭제되는 퇴화 케이스라
  3,992배 같은 값이 나온다. 인용하지 않기로 한 숫자다.

---

## 사실 확인

- `BatchIterator` 는 `RoaringBitmap` 1.6.20 에 있다 (`nextBatch(int[])`,
  `advanceIfNeeded(int)`, `hasNext()`, `clone()`). 지금 `main` 이 핀한 버전이다.
- 벤치 코드는 `bench/src/org/apache/iceberg/deletes/DvBatchBench.java` 의
  `e_batchIterator()` — 이터레이터를 호출마다 생성하고, `delBuf` 를 채운 뒤
  `fillGaps(n)` 로 두 번째 패스를 돈다.
- 표 수치의 출처는 `docs/findings.md` F-002.

## 곁가지 — 이슈 본문의 버전

이슈에 `the version Iceberg pins (1.6.14)` 라고 적혀 있다. `apache-iceberg-1.11.0`
태그 기준으로는 맞지만 **지금 `main` 은 1.6.20 을 핀한다.** 이슈를 손볼 때 같이
고치는 게 좋다 — 이번 답변에서 1.6.20 을 언급하게 되므로 더 눈에 띈다.
