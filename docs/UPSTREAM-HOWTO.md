# 업스트림에 내는 방법 — Apache Iceberg 제출 절차

> 2026-09-09 기준으로 `iceberg.apache.org` 와 저장소의 `site/docs/contribute.md` ·
> `community.md` 를 직접 확인해 정리했다. **절차는 바뀔 수 있으니 내기 직전에
> [contribute 페이지](https://iceberg.apache.org/contribute/)를 한 번 더 볼 것.**

---

## 0. 한 줄 요약

**메일이 아니다.** Apache Iceberg 는 **GitHub** 에서 이슈와 PR 을 받는다.
메일링 리스트(`dev@iceberg.apache.org`)는 *"논란이 될 만한 변경"* 을 미리 상의하는 자리이지
제출 창구가 아니다.

```
GitHub Issue 로 문제를 먼저 등록   →   (선택) dev 리스트/Slack 에서 방향 확인   →   PR
```

우리 건은 **성능 개선 + 동작 변경 없음**이라 이슈 → PR 로 곧장 가면 된다.
스펙 변경이 아니므로 커뮤니티 투표도 필요 없다.

---

## 1. 준비물 점검

| | 우리 상태 |
|---|---|
| 문제를 재현하는 근거 | ✅ `upstream/issue-columnarbatchutil.md` (570줄) |
| 실제 패치 | ✅ `upstream/range-scan.patch` (30 KB, 1.11.0 기준) |
| 추가한 테스트 | ✅ `upstream/TestPositionDeleteIndexForEachInRange.java` |
| 정확성 검증 | ✅ 7개 테이블 `count`/`sum(id)`/`min·max` 일치 + Iceberg 자체 테스트 72개 통과 |
| 회귀 확인 | ✅ equality delete 테이블에서 기존 루프로 폴백, 회귀 없음 |

---

## 2. 순서

### ① GitHub 계정 · 저장소 준비

계정은 있는 걸 쓰면 된다. **ASF 개인 계정(Apache ID)은 필요 없다** — 커미터가 아니어도
PR 을 낼 수 있다.

```bash
# 포크한 뒤
git clone https://github.com/<당신아이디>/iceberg.git
cd iceberg
git remote add upstream https://github.com/apache/iceberg.git
git checkout -b spark-dv-range-scan upstream/main
```

> ⚠️ 우리 패치는 **1.11.0 태그** 기준이다. `main` 은 그 뒤로 움직였을 수 있으므로
> 그대로 `git apply` 하지 말고 **충돌을 보고 손으로 옮길 것.** 특히
> `ColumnarBatchUtil.java` 는 Spark 버전별로 `spark/v3.4`, `v3.5`, `v4.0`, `v4.1` 에
> 같은 모양으로 복제돼 있다 — 어느 버전에 낼지 먼저 정해야 한다.
> (초안은 `v4.0` 기준으로 썼다.)

### ② 이슈 먼저

[github.com/apache/iceberg/issues/new](https://github.com/apache/iceberg/issues/new) 에서
**Bug report** 가 아니라 **일반 이슈**로 연다. 성능 문제이지 오동작이 아니기 때문이다.

- **제목**: `upstream/issue-columnarbatchutil.md` 의 첫 줄을 그대로 쓴다 —
  *"Spark: vectorized reads probe the position delete index once per row, ignoring that batch positions are contiguous"*
- **본문**: 그 파일의 `---` 아래 전체를 붙인다. 마크다운 그대로 렌더된다.
- 이슈 번호를 적어둔다 (아래에서 쓴다).

> **길이가 걱정되면**: 570줄은 이슈로는 길다. Summary + Where + Why + 핵심 증거 두 절만
> 남기고, 나머지는 **저장소 링크**로 돌리는 방법도 있다. 다만 이 프로젝트의 강점이
> "조건을 다 밝힌 것" 이므로 Caveats 절은 반드시 남길 것.

### ③ PR

```bash
./gradlew spotlessApply                       # 코드 스타일 자동 정리 (필수)
./gradlew build -DsparkVersions=4.0           # 빌드 + 테스트
./gradlew spotlessCheck                       # CI 가 보는 것
```

- **PR 제목에 접두사를 붙인다.** 우리 건은 `Spark:` 다.
  예: `Spark: Use Roaring range traversal in ColumnarBatchUtil delete checks`
- **PR 본문 첫 줄에 `Closes #1234`** (②의 이슈 번호) — 자동으로 연결된다.
- 아직 다듬는 중이면 **Draft** 로 연다.

### ④ 지켜야 하는 규칙

| 규칙 | 우리 해당 여부 |
|---|---|
| Google Java Format (`spotlessCheck`) | ✅ 반드시 `spotlessApply` 돌릴 것 |
| 모든 파일에 Apache 라이선스 헤더 | ✅ 새 테스트 파일에 필요 |
| 새 테스트는 JUnit 5 | ✅ 확인할 것 |
| 단언은 AssertJ 로 | ✅ 확인할 것 |
| `Thread.sleep()` 금지 (Awaitility 쓸 것) | 해당 없음 |
| `@Deprecated` + 제거 버전 javadoc | 해당 없음 (공개 API 안 바꿈) |
| 공개 API 추가는 24시간 리뷰 대기 | 해당 없음 |

`iceberg-api` 를 안 건드리므로 **Revapi 호환성 검사에 걸릴 일이 없다.**
`ColumnarBatchUtil` 은 `spark` 모듈의 내부 클래스다.

### ⑤ 리뷰

- **커미터 1명 승인이면 머지된다** (스펙 변경만 투표 대상).
- 리뷰어가 "분산에서는?", "파일 많으면?" 을 물을 가능성이 높다.
  → 답이 초안에 이미 있다. `docs/threats.md` 를 먼저 읽고 들어갈 것.

---

## 3. 내기 전에 반드시 할 것

### ⚠️ AI 사용 고지

ASF 에는 [Generative Tooling Guidance](https://www.apache.org/legal/generative-tooling.html)
가 있고, contribute 문서가 이를 명시적으로 가리킨다.
이 프로젝트는 **측정 설계·스크립트·문서 작성에 LLM 을 광범위하게 썼다.**

- 가이드의 요지는 "쓰지 마라" 가 아니라 **호환되지 않는 라이선스의 코드가 섞여 들어가지
  않게 하라** 는 것이다.
- 우리 패치는 **RoaringBitmap 의 공개 API 를 호출하는 것**이고 알고리즘도 자명하지만,
  제출 전에 가이드를 직접 읽고 판단할 것. 필요하면 PR 에 한 줄 밝히는 쪽이 안전하다.

### ⚠️ 저장소 링크를 걸 거라면

이슈에서 `github.com/JeonDaehong/iceberg-dv-anatomy` 를 링크하게 된다.
그러면 **공개 저장소의 모든 내용이 리뷰 대상이 된다.** 내기 전에:

- [ ] `findings.md` 의 정정 표시가 다 붙어 있는가 (F-037 의 +29.1% 등)
- [ ] 초안과 `findings.md` 의 숫자가 어긋나지 않는가
- [ ] 측정 조건이 안 적힌 숫자가 없는가

### ⚠️ 숫자 최종 점검

| 인용할 숫자 | 반드시 같이 적을 조건 |
|---|---|
| 43–58% | **단일 JVM · 좁은 투영 · 파일 적음** |
| 42% (분산) | **하한** — 클러스터가 2.3배 느릴 만큼 작았다 |
| 2.6x–9.3x | 밀도 0.5~50% 전 구간 · 1컬럼 |
| 9.28x | **최악 지점(d=6.1%) · 1컬럼** |
| 13–19% 쿼리 시간 | **DV 비중 20% 위일 때만** |
| 2.8x (정렬) | **조건 넷** (F-026·F-029·F-035·F-036) |

조건 없이 인용해도 되는 것은 **전이 경계 4096 과 패치의 정확성** 둘뿐이다.

---

## 4. 채널 정리

| 어디 | 주소 | 언제 |
|---|---|---|
| 이슈·PR | [github.com/apache/iceberg](https://github.com/apache/iceberg) | **여기가 본선** |
| 개발 메일링 | `dev@iceberg.apache.org` | 논란이 될 변경을 미리 상의할 때 |
| └ 구독 | `dev-subscribe@iceberg.apache.org` 로 빈 메일 | 리스트를 읽으려면 먼저 구독 |
| └ 해지 | `dev-unsubscribe@iceberg.apache.org` | |
| 이슈 알림 메일 | `issues-subscribe@iceberg.apache.org` | 이슈 트래픽을 메일로 받고 싶으면 |
| Slack | [s.apache.org/iceberg-slack](https://s.apache.org/iceberg-slack) | 가볍게 물어볼 때 |

**메일링 리스트는 구독해야 글이 나간다.** 구독 안 한 주소로 보내면 모더레이션에 걸린다.

---

## 5. 우리 건에 대한 권고

**dev 리스트를 먼저 거칠 필요는 없다고 본다.** 이유:

- 공개 API 를 안 바꾸고, 동작도 안 바꾸고(결과 동일 검증됨), 스펙과 무관하다
- 이미 구현·측정·테스트가 다 돼 있어서 이슈 본문만으로 판단이 가능하다
- contribute 문서가 dev 리스트를 지목하는 경우는 *"potentially problematic changes"* 인데
  여기 해당하지 않는다

**다만 Slack 에 한 줄 먼저 던져 보는 것은 값이 싸다** — "이 루프를 구간 API 로 바꾸는
이슈를 준비했는데 관심 있는 분?" 정도. 리뷰어가 붙을 확률이 올라간다.

---

*이 문서의 근거: [contribute](https://iceberg.apache.org/contribute/) ·
`site/docs/contribute.md` · `site/docs/community.md` (2026-09-09 확인)*
