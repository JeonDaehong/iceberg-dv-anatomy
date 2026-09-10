# dev@iceberg.apache.org 메일 초안

> Eduard Tudenhoefner 가 Slack `#dev` 쓰레드에서 *"this would be probably good to
> bring up on the dev mailing list to get additional eyes"* 라고 제안한 데 따른 것이다.
> 거절이 아니라 **범위를 넓히라는 조언**이다. `core` 의 공개 인터페이스에 메서드를
> 하나 추가하는 변경이라, Slack 을 안 보는 사람에게도 보여 두라는 뜻으로 읽는 게 맞다.

---

## 0. 받는 사람을 헷갈리지 말 것

```
To: dev@iceberg.apache.org
```

**`dev-subscribe@` 가 아니다.** 그쪽은 구독만 처리하는 봇 주소라서, 글을 보내면
제목·본문을 통째로 버리고 "already subscribed" 같은 안내 메일만 되돌려준다.
리스트에는 아무것도 안 올라간다 (2026-09-10 에 실제로 이렇게 한 번 보냈다).

| 주소 | 하는 일 |
|---|---|
| `dev@iceberg.apache.org` | **여기로 글을 보낸다** |
| `dev-subscribe@iceberg.apache.org` | 구독 신청. 내용은 읽지 않는다 |
| `dev-unsubscribe@iceberg.apache.org` | 구독 해지 |

---

## 0-1. 구독하지 않으면 글이 안 나간다

> `<내 구독 주소>` 은 **이미 구독되어 있다** (2026-09-10 확인).
> 이 절은 다른 주소로 보낼 때만 필요하다.

메일링 리스트는 **구독한 주소에서 보낸 메일만 바로 통과**한다. 구독 안 한 주소로
보내면 모더레이터 승인 대기열에 걸려서, 운이 나쁘면 며칠 뒤에 뜨거나 그냥 묻힌다.

1. `dev-subscribe@iceberg.apache.org` 로 **빈 메일**을 보낸다
   (제목·본문 아무거나 상관없다. 보낼 주소는 실제로 글을 쓸 주소여야 한다)
2. `...@iceberg.apache.org` 에서 **확인 요청 메일**이 온다 →
   그냥 **답장(Reply)** 버튼 누르고 그대로 전송한다. 내용 채울 필요 없다
3. Welcome 메일이 오면 끝. 이때부터 `dev@iceberg.apache.org` 로 글을 쓸 수 있다

구독하면 하루에 수십 통이 온다. Gmail 필터로 `to:dev@iceberg.apache.org` 를
라벨로 빼두면 편하다. 나중에 끊을 땐 `dev-unsubscribe@iceberg.apache.org`.

---

## 0-2. 반드시 일반 텍스트로

Gmail 작성창 오른쪽 아래 `⋮` → **일반 텍스트 모드**. ASF 아카이브는 HTML 메일을
제대로 못 그려서 표가 깨지고 서명 블록이 지저분하게 남는다. 아래 본문은 72자로
줄바꿈해 뒀으니 일반 텍스트로 보내면 표가 그대로 정렬된다.

제대로 켰는지는 ASF 가 돌려주는 헤더로 확인할 수 있다. HTML 로 나가면
`Content-Type: multipart/alternative` 와 스팸 점수에 `HTML_MESSAGE=0.2` 가 찍힌다.
일반 텍스트면 `text/plain` 이고 그 항목이 없다.

**첨부는 하지 않는다.** ASF 리스트는 첨부를 대부분 떼어낸다. 전부 링크로 건다.

보낸 뒤 아카이브에서 확인: <https://lists.apache.org/list?dev@iceberg.apache.org>

---

## 1. 제목

```
[DISCUSS] Reducing per-row position delete index probes in the vectorized read path
```

`[DISCUSS]` 접두어는 ASF 리스트 관행이다. 투표(`[VOTE]`)나 공지(`[ANNOUNCE]`)가
아니라 의견을 구하는 글이라는 표시다. 붙이면 사람들이 읽을지 말지 빨리 판단한다.

---

## 2. 본문 (그대로 붙여넣기)

```
Hi all,

Eduard suggested on Slack that I bring this to the list for additional
eyes, so here it is.

Spark's vectorized reader calls PositionDeleteIndex.isDeleted(pos) once
per row while building the row-id mapping for a batch, in
ColumnarBatchUtil.buildRowIdMapping and buildIsDeleted. Positions within
a batch are a contiguous ascending range, so every one of those calls
repeats the same work: extracting the high and low keys, bounds-checking
the bitmap array, and binary searching the container array. None of it is
amortized across the batch, even though the whole batch lands in the same
one or two containers.

  PR:    https://github.com/apache/iceberg/pull/18027
  Issue: https://github.com/apache/iceberg/issues/18026

The change adds one method to PositionDeleteIndex:

  default void forEachInRange(long posStart, int length, LongConsumer c)

The default implementation is the current per-position loop, so this is
source- and binary-compatible and any external implementation keeps
working untouched. BitmapPositionDeleteIndex overrides it to resolve the
range to at most two underlying 32-bit bitmaps and walk each once.
ColumnarBatchUtil takes that path only when the scan has no equality
deletes; tables with equality deletes keep the existing loop.

The interface addition is the part I would most like opinions on. The
alternative I considered was keeping the traversal internal to core, but
every shape of that either exposed RoaringPositionBitmap or gave up the
clean fallback for other implementations. PositionDeleteIndex already
grows through default methods (merge, forEach, cardinality, serialize),
so this seemed like the established pattern rather than a new one -- but
I would rather hear that from people who maintain it.

Measured on 8M rows across 4 files, one column projected, 3 repetitions
per configuration in separate JVMs (delete-check CPU samples,
async-profiler ctimer):

  density  container   current   patched   reduction
    0.5%   array           674        79       8.6x
    6.1%   array           873        94       9.3x
    7.0%   bitmap          420       113       3.7x
    8.0%   bitmap          499       142       3.5x
     50%   bitmap          584       225       2.6x

The gain is largest just below the array/bitmap container boundary,
which is where the removed work -- a binary search per row -- was most
expensive. buildIsDeleted improves more (14.9x-18.9x) because it does
not need to fill the gaps between deleted positions.

Correctness was checked before performance: count(*), sum(id) and
min/max(id) over 7 tables are identical for both jars, equality-delete
tables show no regression, and the existing delete tests pass. CI on the
PR is green.

Two things I would rather state up front than have someone find:

- These are delete-check CPU samples, not end-to-end wall clock. The
  delete check is 43-54% of scan CPU in my setup, but its share of total
  query CPU falls to 6.8% and then 2.4% once a GROUP BY is added. The
  absolute CPU saved does not change; the fraction does.
- The run-to-run spread of an identical configuration is 9.7% median on
  my primary machine, so I do not claim any difference inside that band.
  The issue lists the rest of the caveats, including the two numbers I
  withdrew after re-measuring on quieter hardware.

The issue has the fuller picture: three CPU microarchitectures, local
disk and S3, warm and dropped page cache, task parallelism 1 through 16,
a small distributed cluster, and hardware counters used to check the
mechanism rather than infer it from timings -- which is how I found out
that my first explanation for the cost was wrong.

It currently touches spark/v4.2 only, to keep the diff reviewable.
ColumnarBatchUtil is byte-identical in v3.5, v4.0 and v4.1, so I will
backfill those in a follow-up if the direction looks right.

I am happy to change the approach if there is a better one, and I still
have the raw profiles, flame graphs and per-axis charts if any of it
would help the review.

Thanks,
Daehong Jeon
```

---

## 3. Slack 쓰레드 답변 (Eduard 에게)

같은 쓰레드에 답글로 단다. 짧게, 그리고 **하겠다는 것만** 말한다.

### A. 기본안 (추천)

```
Thanks Eduard, that makes sense -- it does add a method to a public
interface, so it deserves wider eyes than a Slack thread. I'll write it up
for the dev list today and link the PR and the issue from there.
```

### B. 조금 더 짧게

```
Thanks Eduard -- good call, especially since it touches a public
interface. I'll post it to the dev list and link back here.
```

### C. 메일을 이미 보낸 뒤에 다는 버전

```
Thanks Eduard -- posted it to the dev list, subject
"[DISCUSS] Reducing per-row position delete index probes in the
vectorized read path". Appreciate the pointer.
```

> 메일을 보내고 나서 C 를 다는 게 가장 깔끔하다. 조언 → 실행 → 보고가 한 번에 끝난다.
> 다만 구독 확인에 시간이 걸릴 수 있으니, 지금 바로 답하고 싶으면 A 를 쓰고
> 메일이 아카이브에 뜬 뒤 링크만 한 줄 더 붙이면 된다.

---

## 왜 이렇게 썼나

- **인터페이스 추가를 앞으로 뺐다.** Eduard 가 리스트로 보내라고 한 이유는 성능 숫자가
  아니라 `core` 공개 인터페이스가 바뀌기 때문일 가능성이 높다. 성능 자랑으로 시작하면
  정작 논의할 지점이 아래로 밀린다.
- **"대안을 검토했고 이래서 접었다"를 넣었다.** 그냥 "추가했습니다" 보다
  반박할 거리를 먼저 주는 쪽이 답이 빨리 온다.
- **한계를 두 줄로 접었다.** 이슈에 15개가 있지만 메일에 다 옮기면 아무도 안 읽는다.
  가장 반박당하기 쉬운 두 개(전체 대비 비중, 노이즈 밴드)만 먼저 말한다.
- **"머지해달라"고 하지 않았다.** ASF 리스트에서는 결례에 가깝다.
  `happy to change the approach` 로 판단을 넘긴다.
- **표를 공백 정렬로 썼다.** 마크다운 파이프 표는 메일에서 그냥 깨진다.

## 하지 말 것

- HTML 메일로 보내기 (표가 깨진다)
- 파일 첨부 (리스트가 떼어낸다)
- 답이 없다고 며칠 뒤 같은 글 재게시 — 리스트는 Slack 보다 느리다. 일주일은 기다린다
- `user@iceberg.apache.org` 로 보내기 (사용자 질문 리스트다)
- 제목에 이슈/PR 번호만 쓰기 (`#18027` 은 메일에서 아무 링크도 안 된다)
