# dev@ 답장 — `RoaringBatchIterator` 제안에 대해

> 제안은 **메일링 리스트**로 왔다. Richard Startin 의 batch iteration 글을 참조하며
> `RoaringBatchIterator` 를 뒤에서 쓰는 `PositionBatchIterator` 가 낫지 않겠냐는 것.
>
> **이미 쟀다.** 2026-08-17 프로토타입 단계에서 여섯 구현을 같은 조건으로 비교했고
> `BatchIterator` 가 그중 E 였다. 채택한 `forEachInRange` 보다 **1.4~1.5배 느렸다.**
>
> 정직하게 인정할 구멍이 하나 있다 — 우리 벤치는 **배치마다 이터레이터를 새로
> 만든다.** 제안자가 말한 게 "파일 전체에 걸쳐 사는 이터레이터" 라면 그건 안 잰
> 것이다. 답변에서 내가 먼저 밝힌다.
>
> **쓰레드가 살아났다.** 답이 왔다는 건 사람들이 읽고 있다는 뜻이니, 따로 축약본을
> 올리자던 계획(`pr-reply-usecase.md` B절)은 접어도 된다. 이 답장이 쓰레드를 올린다.

---

## 보내는 법

그 사람의 메일에 **전체답장(Reply all)**. `dev@iceberg.apache.org` 가 받는 사람에
들어 있는지 확인한다 — Apache 리스트는 `Reply-To` 를 리스트로 바꾸지 않아서
그냥 `답장` 을 누르면 개인에게만 간다.

제목은 `Re: [DISCUSS] …` 그대로. 일반 텍스트 모드. 인용은 제안 부분만 남기고 자른다.

---

## 본문

```
Good pointer - I did benchmark BatchIterator as one of six candidate
implementations before settling on this one, and it came out slower.

Same JMH harness for all six: 5,000-row batch, a full 65,536-position chunk
filled to the target density, round-tripped through
BitmapPositionDeleteIndex.serialize()/deserialize() so the containers match
what the read path actually sees. Time per batch in microseconds, lower is
better:

  implementation                        sparse 0.5%   medium 5%   dense 12%
  current (isDeleted per row)                 64.99       80.22       19.76
  forAllInRange + RelativeRangeConsumer        0.49        1.91        3.07
  forEachInRange + gap fill (this PR)          0.45        1.87        4.16
  BatchIterator                                0.63        2.79        5.92

Two reasons it loses, though this part is my reading rather than something
I measured directly:

- It needs two passes. nextBatch(int[]) hands back the deleted positions in
  a buffer, and the row-id mapping still has to be built from them
  afterwards. forEachInRange writes into the mapping as it goes, so the
  deleted positions are never materialised at all.

- There is no bulk "everything in this stretch is absent" callback. That is
  what makes forAllInRange the fastest column above - acceptAllAbsent(from,
  to) skips a clean stretch in O(1), and on run containers it is 2.7x faster
  than forEachInRange for exactly that reason. A batch iterator has to
  enumerate.

One thing my benchmark does not answer, and it may be the version you have
in mind: it constructs the iterator per batch. A PositionBatchIterator held
open across the batches of a file would amortise the container lookup over
the whole file rather than per batch, and would not allocate per batch. I
would expect that to recover part of the gap but not to pass forAllInRange,
since the two-pass and no-bulk-skip issues remain - and the per-batch lookup
is already amortised over 5,000 rows, so there is not much left to win
there. Happy to add that variant and measure it if it is worth pinning down.

There is also an API-shape question, which is really the same question this
thread is already on. A stateful iterator is a larger addition to
PositionDeleteIndex than a default method whose default implementation is
the current loop, and it brings lifecycle with it - create, reset, reuse
across batches. I leaned towards the smallest thing that produced the
measured win. If the consensus is that a batch iterator is the better
abstraction to expose, I am happy to go that way.
```

---

## 왜 이렇게 썼나

- **숫자를 먼저 준다.** "이미 검토했다" 만 말하면 방어적으로 읽힌다.
  표가 먼저 나오면 검토했다는 게 저절로 증명된다.
- **"my reading rather than something I measured"** — 두 이유는 해석이지 측정이
  아니다. 측정한 것과 추론한 것을 섞어 말하지 않는다. 이 프로젝트에서 제일
  중요한 습관이고, 리스트에서 그 구분을 지키는 사람은 신뢰를 얻는다.
- **안 잰 것을 내가 먼저 밝힌다.** 배치마다 이터레이터를 만든다는 건 실제 한계이고,
  제안자가 말한 게 그쪽일 가능성이 높다. 내가 안 말하면 상대가 찾아낸다.
- **재보겠다고 제안한다.** 논쟁을 끝내는 방식이 그거다. 하루면 된다.
- **API 이야기를 마지막에.** 성능으로 먼저 답하고 설계를 꺼내야
  "숫자로 졌으니 설계로 반박한다" 로 안 읽힌다. 그리고 이 쓰레드가 원래
  물어보려던 게 바로 그 질문이라 자연스럽게 이어진다.
- **`RUN` 열을 뺐다.** 배치 전체가 삭제되는 퇴화 케이스라 3,992배 같은 값이 나온다.
  인용하지 않기로 한 숫자다.
- **마크다운 표가 아니라 공백 정렬.** 메일에서 파이프 표는 깨진다.

---

## 사실 확인

- `BatchIterator` 는 RoaringBitmap 1.6.20 에 있다 — `nextBatch(int[])`,
  `advanceIfNeeded(int)`, `hasNext()`, `clone()`. 지금 `main` 이 핀한 버전이다.
- 벤치 코드는 `bench/src/org/apache/iceberg/deletes/DvBatchBench.java` 의
  `e_batchIterator()`. 이터레이터를 호출마다 생성하고, `delBuf` 를 채운 뒤
  `fillGaps(n)` 로 두 번째 패스를 돈다.
- 표 수치 출처는 `docs/findings.md` F-002.
