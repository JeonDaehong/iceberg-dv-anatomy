# 이슈 #18026 — 자책을 덜어내는 손질

> 원칙 하나로 정리된다. **정정은 남기고 자책은 뺀다.**
>
> 숫자가 바뀐 사실은 남겨야 한다. 리뷰어가 다른 데서 인용된 옛 숫자를 보고 혼란을
> 겪으면 안 되고, 한계를 숨긴 문서는 결국 더 큰 대가를 치른다. 그러나 **내가 몇 달을
> 헤맸는지, 무엇을 확인 안 했는지는 리뷰어가 패치를 판단하는 데 쓰이지 않는다.**
>
> 아래는 "사실은 그대로, 화자만 지운" 교체안이다.
> 블로그는 별개다 — 거기서는 "19번 틀렸다" 가 글의 논지다.

---

## 1. Caveats — PMU

**지금**

> **Correction to an earlier version of this text:** I had written that no hardware PMU
> was available and that cycle- and branch-level attribution would need a bare-metal run.
> That was wrong — recent WSL2 kernels expose a Hyper-V vPMU, and the counter evidence in
> *hardware counters* above was collected with it. I had asserted the limitation in eight
> places over eight months without checking it.

**바꿔서**

```
Hardware counters were collected on the same machine: recent WSL2 kernels expose a
Hyper-V vPMU. I verified it against a known-answer control (sorted vs shuffled branch
experiment, 11.8x, matching theory to within 3%) before trusting any counter value.
```

기술적으로 중요한 것은 **"이 환경의 카운터를 믿어도 되는지 검증했다"** 이고, 그건 남는다.
여덟 달 이야기는 리뷰어에게 아무 정보가 아니다.

---

## 2. Caveats — 문자열 길이 적합

**지금** — 24자 점이 어긋나 보였던 이유를 길게 설명한다(평균을 썼고, 한 라운드가 오염됐고…).

**바꿔서**

```
- I ran a length-controlled axis (four md5-derived string columns of 8, 16, 24 and 32
  characters, same type, one column projected at a time). Fitting scan samples against
  length — medians across six rounds, as everywhere else in this issue — gives
  **1,808 + 61.6 x length** (R² = 0.95). So it is neither purely "because it is a string"
  nor purely "because it is wide": both terms are real. At 8 characters the fixed
  per-column term and the length term are about equal; at 32 characters length dominates
  2:1. Against an integer column the same measurement gives 2.1x for an 8-char string and
  4.2x for a 32-char one, so the "8-10 integer columns" figure above applies to
  full-length md5, not to short strings.
```

**틀렸던 계수(1,099 + 63.8)를 아예 안 싣는다.** 옛 숫자를 적고 정정하는 것보다
맞는 숫자만 싣는 쪽이 짧고 혼란도 없다. 옛 계수는 어디에도 인용된 적이 없다.

---

## 3. 이식성 섹션 — "예측을 적어놓고 틀렸다"

**지금**

> Two of those results were surprises worth stating, because both were predictions I wrote
> down first and got wrong:

**바꿔서**

```
Two of those results run against the intuitive expectation, so they are worth stating
explicitly:
```

---

## 4. 같은 섹션 — 분기 예측 귀속

**지금**

> The patch helps *more* on newer cores, not less … I first attributed that to branch
> prediction. Hardware counters do not support it (see below), so I state it as an
> observation, not a mechanism.

**바꿔서**

```
**The patch helps *more* on newer cores**, not less: 11.3x-17.9x on the cloud instances
against 8.6x-9.3x on mine, because the per-row probe gets relatively more expensive while
the bulk range traversal gets cheaper. Hardware counters do not attribute this to branch
prediction (see below), so I state it as an observation rather than a mechanism.
```

한 문장만 지우면 된다. 결론은 똑같다.

---

## 5. 같은 섹션 — 노이즈 바닥

**지금**

> One methodological note that cuts against my own earlier numbers: … I was looking at a
> regression that was not there.

**바꿔서**

```
One methodological note on the noise floor: the run-to-run spread on the dedicated
instances was **7-13%**, against **27% median** on my WSL2 box. The 9.7% floor used
throughout this issue is a property of my development environment more than of the
workload, so anything marked "inside the noise" may be resolvable on quieter hardware.
In one case it was: a shuffle-heavy query that looked slower under the patch on my
machine (1 of 6 paired rounds favouring it) is **faster in 12 of 12** on a dedicated
instance with 12 rounds.
```

사실은 전부 남았고 마지막 한 문장만 없앴다.

---

## 6. 정렬 비용 — 저장이 커진 것

**지금**

> Storage grows **2.6%**, which surprised me — I had predicted it would shrink.

**바꿔서**

```
Storage grows **2.6%**, which is the opposite of what compression intuition suggests.
```

그리고 그 뒤의 per-column 설명(정렬 키는 399배 줄고 `id` 는 두 배로 늘어난다)은
**그대로 둔다.** 거기가 실제로 유용한 부분이다.

---

## 7. 클러스터 섹션

**지금**

> Two things worth stating, both of which contradict what I predicted:
> … I originally contrasted this with … **That contrast did not survive re-measurement.**
> … I have removed the claim rather than the data.

**바꿔서**

```
Two things worth stating:

- **The share drops to about 42% and the speedup to about 5x.** …(그대로)…
- **In the cluster the shuffle does not move the share** — 41.58% with no shuffle against
  41.57% with a large one.

  An earlier version of this text contrasted that with a single-JVM pair of 53.95%
  against 43.19%. That contrast does not reproduce: the 43.19% came from my development
  machine, whose round-to-round spread is about three times that of a dedicated instance.
  Re-running the same three queries on an `m7i.xlarge` with 12 rounds instead of 6 gives
  **59.6% / 58.0% / 55.6%** — a spread of 4.0 points, not 10.8. The shuffle does not move
  the share in a single JVM either.
```

정정 자체는 남겨야 한다. 43.19% 가 초기 본문에 있었으니 그걸 본 사람이 있을 수 있다.
다만 `I originally` · `did not survive` · `I have removed the claim rather than the data`
세 군데의 화자를 지운다.

---

## 8. 하드웨어 카운터 섹션 제목과 첫 줄

**지금**

> `<summary>Evidence — hardware counters, and a mechanism I had wrong</summary>`
>
> I had assumed the per-row probe was expensive because the `array` container does a binary
> search whose branches mispredict. **That is not what the counters say.**

**바꿔서**

```
<summary><b>Evidence — hardware counters</b> (65 lines)</summary>

A binary search with data-dependent branches points at branch misprediction as the
obvious cause. **The counters do not support it.**
```

가설을 세우고 반증한 것으로 읽힌다. 실제로 그게 일어난 일이고,
"내가 틀렸다" 가 아니라 "이 가설이 기각됐다" 가 더 정확한 서술이기도 하다.

---

# 반대로 — 반드시 남겨야 하는 것

아래는 실수가 아니라 **범위의 한계**다. 빼면 문서가 약해지고,
리뷰어가 나중에 발견하면 훨씬 나쁘다.

- **안 잰 것들** — 파일 수천 개, 크로스 리전 S3, 다중 컬럼 equality delete,
  중첩 타입, 조인 쿼리, 한 스키마 한 테이블
- **노이즈 바닥 9.7% 와 "그 안의 차이는 주장하지 않는다"**
- **0.53 보정** — 프로파일러 CPU 절감의 절반만 벽시계로 온다는 것.
  이게 없으면 모든 CPU 숫자가 과장으로 읽힌다
- **42% 를 하한으로 읽으라는 단서**, 그리고 43~58% 가 단일 JVM 값이라는 것
- **철회한 숫자 두 개가 있다는 Caveats 항목** — 다만 "조용한 기계에서 다시 쟀다"
  까지만, 왜 시끄러운 기계에서 쟀는지는 뺀다
- **마이크로벤치가 출력 버퍼를 재사용한다는 것** (실제 코드는 매번 할당)
- **`RUN_CONTIG` 3,800배는 퇴화값이라 인용하지 않는다는 주석**

이것들은 자책이 아니라 **독자가 숫자를 오독하지 않게 하는 장치**다.

---

# 적용 순서

`issue-tldr-patch.md` 의 축약과 같이 하면 한 번의 편집으로 끝난다.

1. Summary 교체 + TL;DR 추가 (`issue-tldr-patch.md`)
2. `Happy to open a PR` → `PR #18027 implements this…`
3. 위 1~8 교체
4. Caveats 에서 PMU·문자열 항목 교체
