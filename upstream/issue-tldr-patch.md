# 이슈 #18026 손보기 — TL;DR 추가 + Summary 축약

> 길이 자체는 문제가 아니다. 45,520자 중 30,904자가 `<details>` 안에 접혀 있어서
> 실제로 보이는 건 14,616자다. 이슈는 참고 문서라 깊이가 자산이다.
>
> 문제는 **Summary 3,387자 / 7문단**이다. 구조를 만나기 전에 산문 벽을 만난다.
> PR 리뷰어가 `way shorter letters and descriptions` 라고 한 게 이걸 가리킨다.
>
> 증거 섹션은 **건드리지 않는다.** 거기가 이 이슈의 값이다.

---

## 1. 맨 위에 TL;DR 을 넣는다

`### Summary` 바로 아래, 기존 첫 문단 **앞에** 붙인다.

```markdown
> **TL;DR** — Spark's vectorized reader asks the position delete index
> "is row N deleted?" once per row, 5,000 times per batch, while those
> positions are a contiguous ascending range. Passing the range once instead
> cuts delete-check CPU by **2.6x–9.3x**, and end-to-end query time by
> **13–19%** when the projection is narrow and made of integer columns.
> One rule for when it is worth it: the delete check is more than **~21% of
> scan CPU**. More deletes is *not* more benefit — the largest gain is just
> *below* 6.25% deletes, where the Roaring container is still a sorted array
> at its deepest.
>
> PR: #18027 · everything below is the measurement backing it, including the
> conditions under which these numbers do not hold.
```

여덟 줄이다. 이걸 읽고 덮어도 무엇을 제안하는지, 언제 쓸모가 있는지,
어디가 반직관적인지가 남는다.

---

## 2. Summary 를 줄인다

지금 7문단이 하는 일을 정리하면 이렇다.

| 문단 | 내용 | 어떻게 |
|---|---|---|
| 1 | 문제 진술 | **남긴다** |
| 2 | 43~58% + CPU·스토리지·캐시·병렬도·클러스터·파일수 + 패치 결과 | **쪼갠다.** 11개 주제가 한 문단에 있고 전부 Evidence 에 있다 |
| 3 | 정렬 처방 2.8x / 조건 / 겹침 | **한 문장으로.** 전용 Evidence 섹션이 이미 있다 |
| 4 | 13~19% 와 손익분기 | **남긴다.** 다만 짧게 |
| 5 | 단위 환산(정수 1.6~3.2컬럼), 0.53 보정 | **Caveats 로 옮긴다** |
| 6 | RoaringBitmap API 가 이미 있다 | 남긴다 |
| 7 | "브랜치 있다, PR 낼까?" | **고쳐야 한다 — PR 은 이미 열렸다** |

### 바꿔 넣을 Summary

```markdown
`ColumnarBatchUtil.buildRowIdMapping` and `buildIsDeleted` call
`PositionDeleteIndex.isDeleted(pos)` once for every row in a batch. Positions
within a batch are a contiguous ascending range and the DV-backed index is a
Roaring bitmap, so the same information can be obtained with a single range
traversal instead of `batchSize` independent probes.

**How much it costs.** On a V3 table read with a narrow projection this loop is
**43–58% of scan CPU**, depending on delete density. I reproduced that on three
CPU microarchitectures, on local disk and on S3, with warm and dropped page
cache, and at task parallelism 1 through 16 — it moves by at most a few points
across all of them. Two things do move it: a small distributed cluster gives
**42%**, and splitting the table into many small files costs about **11 points**.
Both enlarge the denominator rather than making the check cheaper.

**What the change buys.** Measured against `apache-iceberg-1.11.0` across delete
densities from 0.5% to 50%: delete-check CPU drops **2.6x–9.3x** for
`buildRowIdMapping` and **14.9x–18.9x** for `buildIsDeleted`, and the scan subtree
as a whole drops **12–45%**. Tables with equality deletes keep the existing loop
and show no regression.

**When it reaches query time.** The threshold is not a column count — it is the
share the delete check holds in scan CPU. Above roughly 20% the change is worth
**13–19% of end-to-end query time**; below it the difference falls inside my
measurement noise, even though the delete-check CPU is still reduced 3.0x–6.7x
there. The change removes the same absolute CPU either way.

**Something users can do today.** Sorting the table by the column the deletes
target makes the delete check **2.8x** cheaper on its own, when the deletes
concentrate on relatively few distinct key values. The two remedies overlap but
do not replace each other — sorting alone 2.8x, this patch alone 7.8x, both
together 12.4x. Conditions and costs are in *what a user can do before this is
fixed* below.

`RoaringBitmap` already provides the range APIs needed (`forEachInRange`,
`forAllInRange`, `rangeCardinality`), and they are present in the version Iceberg
pins (`1.6.14`), including the shaded copy in `iceberg-spark-runtime`.

**PR #18027 implements this against `spark/v4.2`.** `ColumnarBatchUtil` is
byte-identical in v3.5, v4.0 and v4.1, so I will backfill those in a follow-up if
the direction looks right.
```

`3,387자 → 약 1,900자`. 굵은 소제목이 문단마다 붙어서 **훑어 읽기가 된다** —
지금은 어디서 무엇이 시작되는지 표시가 없다.

---

## 3. 반드시 고쳐야 하는 것

지금 이슈 마지막 문단이 이렇게 되어 있다.

```
I have a working branch with the change, updated tests, and the measurements
below. Happy to open a PR if the direction sounds reasonable.
```

**PR 은 이미 열려 있다.** 이슈만 보고 온 사람은 아직 코드가 없는 줄 안다.
위 Summary 마지막 문단(`PR #18027 implements this…`)으로 교체한다.

이건 길이 문제와 무관하게 고쳐야 한다.

---

## 4. Caveats 로 옮길 것

Summary 5문단의 단위 환산과 0.53 보정은 Caveats 에 같은 이야기가 이미 있다.
Summary 에서 빼고 Caveats 에 한 줄만 더한다.

```markdown
- As a unit that transfers to other schemas: one delete check costs about as much
  as decoding 1.6–3.2 fixed-width integer columns, while a single 32-char string
  column costs 8–10 integer columns. So a column count is the wrong thing to
  quote — on my table ten integer columns still show the effect and three md5
  strings do not.
```

---

## 하지 말 것

- **`<details>` 섹션을 지우지 않는다.** 거기가 이 이슈의 값이고, 접혀 있어서
  안 읽는 사람에게 비용을 물리지 않는다.
- **Caveats 를 줄이지 않는다.** 한계를 먼저 말하는 쪽이 신뢰를 얻는다.
- 이슈 제목은 그대로 둔다. 검색과 링크의 식별자다.
