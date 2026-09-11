# dev@ 답장 초안 — 짧은 재요약 + 질문 답변

> **새 쓰레드를 열지 않는다.** 같은 쓰레드에 답글로 보낸다.
> 제목은 `Re: [DISCUSS] …` 그대로, 받는 사람에 `dev@iceberg.apache.org` 확인.
>
> 이유 — ① 스팸은 새 메일로 안 고쳐진다(같은 발신자·같은 리스트). 오히려 이미
> 대화에 들어온 상대에게는 답글이 더 잘 도착한다. ② 하루 만에 같은 주제로 새 글을
> 올리면 재게시로 읽힌다. ③ 답글이면 쓰레드가 다시 위로 올라와서 원문을 건너뛴
> 사람도 이걸 먼저 본다.
>
> 그래서 이 한 통이 **짧은 재요약 + 질문 답변** 두 일을 같이 한다.
> 원문을 안 읽은 사람도 이것만 보고 파악할 수 있게 썼다.

---

## 본문

```
Short version first, since my original was too long - thanks for saying so.

  What: Spark's vectorized reader asks the position delete index
        "is row N deleted?" once per row, 5,000 times per batch. The
        positions are a contiguous ascending range, so it re-resolves
        the same Roaring container every time.
  Fix:  pass the range once instead. PositionDeleteIndex gets a
        forEachInRange default method; the default is the current loop,
        so nothing external has to change.
  Gain: 2.6-9.3x less delete-check CPU. 13-19% faster end-to-end, but
        only under the conditions below.
  PR:   https://github.com/apache/iceberg/pull/18027
  Data: https://github.com/apache/iceberg/issues/18026

On your question - yes, 13-19% is accurate, and if anything conservative.
It is measured, not extrapolated: projections of 5 columns or fewer, all
integer. A scan-only query with one projected column came out at 29% on a
dedicated instance. At 10 columns or more it falls inside my noise floor
and I claim nothing.

Your summary dropped three conditions, and one of them is backwards:

  helps          narrow projection AND the columns are integers
                 scan-dominated query, little aggregation
                 delete rate near the array/bitmap boundary (~6.25%)
                 fewer, larger files

  does not help  a single md5 string column costs 8-10 integer columns of
                 decode, which sinks it on its own
                 heavy aggregation - the CPU saved is constant, so its
                 share falls to ~3% as the aggregation grows
                 many small files - per-file DV load cost dilutes it
                 (60.7% of scan CPU at 4 files, 49.7% at 488)

The backwards one is "DV-heavy". More deletes is not more benefit. The
worst case, and so the largest gain, is just *below* 6.25%, where the
Roaring container is still a sorted array at its deepest. Past that it
flips to a bitmap and gets cheaper on its own - which is why deleting
more rows can make the scan faster.

If it helps to carry one rule instead of six: the patch is worth it when
the delete check is more than ~21% of scan CPU. That single threshold
predicted the crossover better than column count or column type did.

I have put this as a "when this helps" section at the top of the PR
description.

(On the spam folder - there is a thread on this list about devlist mail
landing in gmail spam, so it may not be specific to my message.)
```

---

## 왜 이렇게 썼나

- **`Short version first` 로 시작한다.** 지적이 "길다" 였는데 긴 답을 보내면 조언을
  안 들은 게 된다. 사과는 반 줄로 끝낸다.
- **What / Fix / Gain / PR / Data 다섯 줄.** 원문을 안 읽은 사람이 여기서 다 얻는다.
  실질적으로 "다시 쓴 짧은 메일" 이 이 블록이다.
- **helps / does not help 를 표로.** 상대가 요청한 게 정확히 *"어떤 use-case 에
  도움이 되는지 강조하라"* 였다. 문장으로 풀면 또 길어진다.
- **"backwards" 한 개를 따로 뺐다.** LLM 요약에서 유일하게 틀린 부분이고,
  안 고치면 다른 사람도 같은 오해를 한다. 그리고 이게 이 프로젝트에서 가장
  반직관적인 발견이라 오히려 관심을 끈다.
- **21% 한 줄을 마지막에.** 조건 여섯 개를 기억할 사람은 없다. 하나만 남기면 이거다.
- **스팸은 괄호로 맨 뒤에.** 먼저 말하면 변명처럼 읽힌다.

## 답장 전에 할 것

PR 본문을 **먼저** 고친다. 답장에 "PR 설명 맨 위에 넣었다"(과거형)고 썼으므로,
보내는 시점에 실제로 들어가 있어야 한다.

- [x] `upstream/pr-body-paste.md` 맨 앞에 `**When this helps.**` 문단 추가
- [ ] GitHub PR #18027 본문 갱신
- [ ] 그다음 메일 답장

## 그래도 새 쓰레드를 열고 싶다면

지금은 아니다. 답글로 일주일을 기다려 보고, 그때까지 아무 반응이 없으면
**주제를 바꿔서** — 예를 들어 측정 방법론 쪽으로 — 새로 쓰는 게 맞다.
같은 제안을 같은 리스트에 두 번 올리는 모양은 피한다.
