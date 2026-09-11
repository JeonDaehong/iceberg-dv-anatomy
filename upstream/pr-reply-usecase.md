# PR #18027 코멘트 답변 초안

> 피드백이 온 곳은 **메일이 아니라 PR** 이다. 그러니 답도 PR 코멘트로 단다.
> dev@ 쓰레드는 아직 조용하다. 메일 쪽도 짧게 다시 내보내는데, 그건 B 에 있다.
>
> 상대가 말한 것 세 가지:
> 1. dev@ 메일이 자기 **스팸함**으로 갔다
> 2. **더 짧게 쓰거나 맨 앞에 TL;DR** 을 달아라. 바쁜 메인테이너는 긴 글을 건너뛴다
>    (원문은 "way shorter **letters and descriptions**" — `letters` 는 메일,
>    `descriptions` 는 PR 설명이다. 둘 다 가리킨다)
> 3. LLM 요약("좁은 투영이면 13~19% 빨라진다")이 **맞냐**, 그리고
>    **어떤 use-case 에 도움이 되는지 강조하라**
>
> 3번이 실제 질문이고, 답은 **"맞다, 오히려 보수적이다. 다만 조건 셋이 빠졌고
> 그중 하나는 방향이 반대다."**

---

## A. PR 코멘트 (그대로 붙여넣기)

```markdown
**TL;DR** — yes, 13-19% is accurate and if anything conservative. But the
summary dropped three conditions, and one of them is backwards: more deletes
is *not* more benefit. I've rewritten the PR description to lead with when
this helps. Thanks for both points.

### Is 13-19% accurate?

It's measured rather than extrapolated, on projections of 5 columns or fewer,
all integer. A scan-only query with a single projected column came out at 29%
on a dedicated instance. At 10 columns or more it falls inside my noise floor
(9.7% median run-to-run spread) and I don't claim anything there.

### When it helps

| | |
|---|---|
| **Helps** | Narrow projection **and** the columns are integers |
| | Scan-dominated query, little aggregation |
| | Delete rate near the array/bitmap boundary (~6.25%) |
| | Fewer, larger files |
| **Doesn't help** | One md5 string column costs 8-10 integer columns of decode, which sinks it on its own |
| | Heavy aggregation — the CPU saved is constant, so its share falls to ~3% as the aggregation grows |
| | Many small files — per-file DV load cost dilutes it (60.7% of scan CPU at 4 files, 49.7% at 488) |

### The one that's backwards

"DV-heavy" reads as "more deletes, more benefit", and it's the other way
round. The worst case — and so the largest gain — is just *below* 6.25%,
where the Roaring container is still a sorted array at its deepest. Past that
it flips to a bitmap and gets cheaper on its own. Deleting more rows can make
the scan faster, which is the least intuitive thing I found here.

### One rule instead of six

The patch is worth it when the delete check is more than **~21% of scan CPU**.
That single threshold predicted the crossover better than column count or
column type did.

---

Point taken on length — I've added a "when this helps" paragraph at the top of
the description here, and I'll lead with a TL;DR on the list. Sorry it landed
in your spam folder; there's a thread on the dev list about devlist mail going
to gmail spam, so it may not be specific to my message.
```

---

## 왜 이렇게 썼나

- **TL;DR 을 진짜 맨 위에 뒀다.** 그렇게 하라고 한 사람에게 답하면서 안 하면 안 된다.
  그리고 TL;DR 안에 이미 답(맞다 / 조건 셋 / 하나는 반대)이 다 들어 있다.
- **표를 썼다.** 상대가 요청한 게 정확히 *"어떤 use-case 에 도움이 되는지"* 다.
  GitHub 은 마크다운 표가 그대로 렌더되니 문장으로 풀 이유가 없다.
- **"backwards" 를 소제목으로 뽑았다.** LLM 요약에서 유일하게 틀린 부분이라
  안 고치면 다른 사람도 같은 오해를 한다. 그리고 이게 가장 반직관적인 발견이라
  오히려 읽는 사람의 흥미를 끈다.
- **21% 한 줄을 마지막에.** 조건 여섯 개를 기억할 사람은 없다.
- **스팸은 맨 뒤 한 줄.** 먼저 말하면 변명처럼 읽힌다. 그리고 리스트에 실제로
  그 쓰레드가 있으니 사실로 말할 수 있다.
- **"I'll lead with a TL;DR on the list"** — 조언을 앞으로도 쓰겠다는 뜻이다.
  조언한 사람에게 이 한마디가 생각보다 크다.

---

## 보내기 전에

답변에 "PR 설명을 고쳤다"고 **과거형**으로 썼다. 코멘트를 달기 **전에** 실제로
고쳐두어야 한다. 그 사람이 확인하러 올라갔을 때 없으면 곤란하다.

- [x] `upstream/pr-body-paste.md` 맨 앞에 `**When this helps.**` 문단 추가
- [ ] GitHub PR #18027 **본문(Description)** 갱신 ← 먼저
- [ ] 그다음 이 코멘트 달기

---

---

## B. dev@ 메일 — 같은 쓰레드에 짧은 요약을 자기 답글로

`letters` 도 짧게 하라고 했으니 메일 쪽도 손댄다. 다만 **새 쓰레드를 열지 않는다.**

이유는 세 가지다.

- 이틀 만에 같은 제안을 새 글로 올리면 재게시로 읽힌다. 리스트에서 가장 눈총받는 행동이다
- 스팸은 새 메일로 안 고쳐진다. 같은 발신자·같은 리스트라 조건이 똑같다
- 자기 글이 길었다는 걸 알았을 때 **같은 쓰레드에 TL;DR 을 덧붙이는 것**이
  ASF 리스트의 표준 동작이다. 받는 쪽도 "고쳐서 다시 말하는구나" 로 읽는다

받은편지함에 원글이 들어간 사람에게는 이 답글로 쓰레드가 다시 올라온다.
스팸으로 간 사람에게는 어차피 무엇을 보내도 같은 곳으로 간다 — 그건 지금
리스트에서 따로 논의 중인 문제다.

### 보내는 법

아카이브에서 네 글을 열고 `Reply` 를 누른다. 원글이 받은편지함에 없기 때문이다
(Gmail 이 자기가 보낸 메일을 중복으로 보고 보낸편지함에만 둔다).

```
https://lists.apache.org/thread/5v8j1zj2l8ntygph47r0k61xyskxc5o1
```

제목은 `Re: [DISCUSS] …` 그대로 두고, 일반 텍스트 모드로 보낸다.

### 본문

```
Short version, since my original was too long - thanks to the reviewer on
the PR for saying so.

  Problem   Spark's vectorized reader asks the position delete index
            "is row N deleted?" once per row - 5,000 times per batch -
            while those positions are a contiguous ascending range. It
            re-resolves the same Roaring container every time.

  Change    Pass the range once. PositionDeleteIndex gets a
            forEachInRange default method; the default implementation is
            the current per-row loop, so nothing external has to change.
            ColumnarBatchUtil takes it only when there are no equality
            deletes.

  Effect    2.6-9.3x less delete-check CPU. End to end, 13-19% on narrow
            all-integer projections, up to 29% for a single column on a
            dedicated instance, and inside my noise floor at 10+ columns.
            One rule: it pays off when the delete check is more than ~21%
            of scan CPU.

  Links     PR    https://github.com/apache/iceberg/pull/18027
            Data  https://github.com/apache/iceberg/issues/18026

The part I would most like opinions on is the interface addition itself -
whether growing PositionDeleteIndex with another default method is the
right call, or whether the traversal should stay internal to core.

One correction to how this is easy to read: more deletes is not more
benefit. The largest gain is just below 6.25%, where the Roaring container
is still a sorted array at its deepest; past that it flips to a bitmap and
gets cheaper on its own.
```

### 왜 이렇게 썼나

- **`Short version` 로 시작하고 이유를 밝힌다.** 이게 없으면 같은 글을 또 올린
  것처럼 보인다. "PR 에서 지적받아서" 한 줄이 재게시와 정정을 가른다.
- **Problem / Change / Effect / Links 네 칸.** 원글 60줄이 여기 20줄로 들어갔다.
- **인터페이스 질문을 다시 앞으로.** 원글에서도 이게 핵심이었는데 측정값에
  묻혀 있었다. 짧게 쓰니 오히려 선명해진다.
- **6.25% 정정을 마지막에.** PR 에서 나온 오해라 리스트에도 같이 남겨둔다.

### 순서

PR 을 먼저 정리하고 메일은 그다음이다. 코멘트에 "PR 설명을 고쳤다" 고 썼으니
메일에서 PR 링크를 걸 때도 이미 고쳐져 있어야 앞뒤가 맞는다.

1. GitHub PR #18027 **본문** 갱신
2. PR 코멘트 (A)
3. dev@ 자기 답글 (B)
