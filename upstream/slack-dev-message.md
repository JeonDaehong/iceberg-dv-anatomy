# Slack `#dev` 메시지 초안

> 채널: [Iceberg Slack](https://s.apache.org/iceberg-slack) → `#dev`
> 한 번만 올린다. 답이 없어도 며칠 뒤 재게시하지 않는다.

---

## A. 기본안 (추천)

```
Hi all — I opened #18027, which replaces the per-row `PositionDeleteIndex.isDeleted`
calls in Spark's vectorized reader with a single range traversal over the batch.
Delete-check CPU drops 2.6x–9.3x depending on delete density, and the largest gain
lands just below the array/bitmap container boundary, which is where the current
code is worst.

Measurements are in #18026 — including where the numbers don't hold. CI is green.
I kept the raw profiles, flame graphs and per-axis charts as well, so if anything
there would help the review, just say the word and I'll share it.

Would appreciate a review whenever someone has time, and I'm happy to change the
approach if there's a better one.
```

---

## B. 짧은 버전

```
Opened #18027 — Spark's vectorized reader probes the position delete index once per
row; this traverses the batch range once instead. 2.6x–9.3x less delete-check CPU,
measurements and caveats in #18026. CI is green.

I still have the raw profiles and charts behind those numbers, happy to share if
useful. Would appreciate a review when someone has a moment 🙏
```

---

## C. 방법론을 앞세우는 버전

측정 과정 자체에 관심을 끌고 싶을 때. 다만 자랑처럼 읽힐 위험이 있어 A/B 보다 조심스럽다.

```
Opened #18027 — replacing the per-row position delete index probes in Spark's
vectorized reader with a single range traversal (2.6x–9.3x less delete-check CPU).

I wrote the predictions down before each measurement, which is how I found out my
first explanation for the cost was wrong — it isn't branch misprediction. #18026 has
the full set, including the conditions where the numbers don't hold, and I still have
the raw profiles and charts if any of it would be useful. CI is green and a review
would be much appreciated.
```

---

## 왜 이렇게 썼나

- **숫자는 하나만.** 표는 안 붙인다 — Slack 에서 깨지고 PR 에 이미 있다.
- **사람을 @ 로 부르지 않는다.** PR 에서 이미 두 명을 태그했다. 여기서 또 부르면 중복 압박이다.
- **"CI is green" 을 넣었다.** 리뷰어가 "일단 CI 부터 보자" 하고 미루는 걸 막는다.
- **"happy to change the approach"** 로 끝낸다. 요청이 아니라 열린 제안으로 읽힌다.
- **"where the numbers don't hold"** — 한계를 먼저 말하는 쪽이 신뢰를 얻는다. 이 프로젝트의
  강점이 거기 있다.

## 하지 말 것

- `#general` 에 올리기 (사용자 질문 채널이다)
- 측정 표 붙여넣기
- 며칠 뒤 같은 메시지 재게시
- 세 명 이상 동시 소환
