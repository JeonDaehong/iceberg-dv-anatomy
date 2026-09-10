# PR 리뷰 요청 코멘트 초안

> 올린 직후 말고, **2~3일 조용할 때** 다는 걸 권한다.
> 그리고 처음엔 두 명(core 한 명 + Spark 한 명)만 부르는 게 눈총을 덜 받는다.

---

## A. 기본안 (추천)

```
@aokolnychyi @huaxingao — tagging you as the people who have worked most on
these files. No rush at all; a look whenever you have time would be much
appreciated.

I tried to make this easy to evaluate rather than something you have to take on
trust. The change is measured across delete densities from 0.5% to 50%, on three
CPU microarchitectures, on local disk and on S3, and I used hardware counters to
check the mechanism instead of inferring it from timings — which is how I found
out that my first explanation for the cost was wrong. The linked issue also
lists the conditions under which these numbers do not hold.

I am happy to change the approach if you see a better one. If the direction
looks right, I will backfill v3.5, v4.0 and v4.1 in a follow-up.
```

---

## B. 더 짧은 버전

```
@aokolnychyi @huaxingao — tagging you as the people who have worked most on
these files. No rush; a look when you have time would be much appreciated.

The change is measured across delete densities from 0.5% to 50%, on three CPU
microarchitectures, on local disk and on S3, with hardware counters used to
confirm the mechanism rather than infer it. The linked issue also lists the
conditions where the numbers do not hold.

Happy to adjust the approach if you see a better one.
```

---

## C. 셋째 사람까지 부를 때 (A·B 이후에도 조용하면)

```
@anuragmantri — you reviewed #17864, which addressed a similar per-row cost in
core, so you may find this one familiar. Would appreciate your eyes on it if you
have time.
```

---

## 왜 이렇게 썼나

- **"PR을 머지해달라"고 직접 요청하지 않았다.** Apache 문화에서는 결례에 가깝다.
  대신 *"방향이 맞아 보이면"*, *"더 나은 방법이 있으면 바꾸겠다"* 로 판단을 넘긴다.
  실제로는 같은 요청이지만 받는 쪽이 편하다.
- **PR 내용을 다시 요약하지 않았다.** 본문에 이미 있고, 중복하면 읽는 사람이 지친다.
- **"내 첫 설명이 틀렸다"를 한 줄 넣었다.** 측정을 많이 했다는 주장보다
  *틀린 걸 찾아냈다*는 사실이 신뢰를 준다. 자랑이 아니라 검증 강도의 증거다.
- **백필 약속을 마지막에 뒀다.** 리뷰어가 "나머지 버전은?"을 묻기 전에 답한다.

## 하지 말 것

- 셋 이상 동시 소환
- 며칠 간격으로 반복해서 올리기 (한 번이면 충분하다)
- 측정 숫자를 코멘트에 다시 붙여넣기
