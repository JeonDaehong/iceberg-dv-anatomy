# dev@ 답장 초안 — "13~19% 맞냐" 질문에 대해

> 같은 쓰레드에 **답글**로 보낸다. 제목은 `Re: [DISCUSS] …` 그대로 두고
> 받는 사람에 `dev@iceberg.apache.org` 가 들어 있는지 확인한다.
>
> 상대가 지적한 게 "길다" 이므로 **이 답장은 짧아야 한다.** TL;DR 을 맨 위에 둔다.

---

## 본문

```
TL;DR - yes, 13-19% is about right, and it is on the conservative side.
But it needs three conditions the summary dropped: the projection has to be
narrow AND all-integer, the benefit peaks just below the container boundary
rather than growing with delete rate, and it halves when the table is split
into many small files. I will put a "when does this help" section at the top
of the PR description.

Sorry about the length, and thanks for saying so - that is fair.

On the number itself. 13-19% is measured, not extrapolated: projections of
5 columns or fewer, all integer, on my laptop. On a dedicated instance a
scan-only query with a single projected column came out at 29%. At 10
columns or more it falls inside my noise floor and I do not claim anything.

The conditions that matter:

  helps            narrow projection, and the columns are integers
                   scan-dominated query, little aggregation
                   delete rate near the array/bitmap boundary (~6.25%)
                   fewer, larger files

  does not help    one md5 string column is worth 8-10 integer columns of
                   decode cost, so a single string column sinks it
                   heavy aggregation - the saved CPU is constant, so its
                   share falls to ~3% as the aggregation grows
                   many small files - the per-file DV load cost dilutes it
                   (60.7% of scan CPU at 4 files, 49.7% at 488)

The one correction I would make to the summary: "DV-heavy" is not monotonic.
More deletes is not more benefit. The worst case - and so the largest gain -
is just *below* 6.25%, where the Roaring container is still a sorted array at
its deepest. Past that it flips to a bitmap and gets cheaper on its own.

If it is easier to state as one rule: the patch is worth it when the delete
check is more than ~21% of scan CPU. That single threshold predicted the
crossover better than column count or column type did.

Also, it went to your spam folder - there is a thread on this list about
devlist mail landing in gmail spam, so that may not be specific to my message.

Thanks for taking the time to read it.
```

---

## 왜 이렇게 썼나

- **TL;DR 이 다섯 줄이다.** 지적의 핵심이 "길다" 였으니 답장이 길면 조언을 안 들은 게 된다.
- **"Sorry about the length" 를 한 줄로 끝냈다.** 사과가 길면 그것도 길어진다.
- **helps / does not help 를 표로 뽑았다.** 상대가 요청한 게 정확히
  *"어떤 use-case 에 도움이 되는지 강조하라"* 였다. 문장으로 풀면 또 길어진다.
- **"DV-heavy 는 단조가 아니다" 를 따로 뺐다.** 이게 LLM 요약에서 **유일하게 틀린 부분**이고,
  고쳐주지 않으면 다른 사람도 같은 오해를 한다.
- **21% 한 줄을 마지막에 뒀다.** 조건 여섯 개를 기억할 사람은 없다. 하나만 남기면 이거다.
- **스팸 이야기를 맨 뒤에 한 줄로.** 먼저 말하면 변명처럼 읽힌다.

## 같이 할 것

답장만 하고 끝내면 안 된다. **PR 본문 맨 위에 "when this helps" 절을 실제로 추가한다.**
하겠다고 답했으니 하지 않으면 그게 더 나쁘다.

- [ ] `upstream/pr-body-paste.md` 맨 앞에 3~5줄짜리 조건 요약 추가
- [ ] GitHub PR 본문 갱신
