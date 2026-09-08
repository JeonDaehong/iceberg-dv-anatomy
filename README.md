# Iceberg Deletion Vector Anatomy

> Apache Iceberg V3 Deletion Vector의 Roaring Bitmap **컨테이너 타입**이
> 삭제 워크로드에 의해 어떻게 결정되고, 그것이 MoR 스캔의 **CPU 비용**을 어떻게 가르는지
> 하드웨어 카운터로 측정하고, 테이블 레이아웃과 리더 코드 양쪽에서 회복시키는 프로젝트.

**Status**: Phase 0·1 완료 + **§7.1 업스트림 패치 구현·측정 완료** (2026-08-18) · **Target**: Apache Iceberg 1.11.x / Spark 4.0 / JDK 17+21

📊 **대시보드**: <https://claude.ai/code/artifact/8457d86b-b711-4e1f-9e9d-2b9ae013cd77>

> ## 📈 핵심 결과 — [docs/findings.md](docs/findings.md)
>
> 아래 수치는 모두 **설정당 3회 반복 측정**의 평균입니다.
> 반복 흩어짐은 중앙값 9.7% / 최대 25.9%이며, **그보다 작은 차이는 주장하지 않습니다** (F-010).
>
> **측정 조건** — Ryzen 5 5600X(Zen 3) · WSL2 · Spark 4.0 `local[4]` · JDK 17 ·
> 로컬 NVMe **웜 캐시**(825 MB 테이블, 측정 중 I/O ≈ 0) · **1컬럼 좁은 투영** ·
> 배치 크기 Iceberg 기본 **5,000**.
> 전이 경계·컨테이너 분포·패치 정확성은 환경 무관이지만,
> **"스캔 CPU의 53%"는 이 조건에서의 상한**입니다 — 20컬럼으로 넓히면 3.75%로 떨어집니다.
> 항목별 이식성 등급: [docs/findings.md#측정-조건](docs/findings.md).
> 이 결과를 공격하는 방법과 방어 상태는 [docs/threats.md](docs/threats.md)에 따로 정리해 뒀습니다.
>
> ### ① 삭제율이 비용을 가른다 — 톱니 곡선
>
> | 삭제율 d | 컨테이너 | DV 샘플 (범위) | |
> |---|---|---|---|
> | 0.50% | array 100% | 599 (576–633) | |
> | 5.00% | array 100% | 746 (700–798) | 오르막 (이진탐색 O(log n)) |
> | **6.10%** | array 95% | **872 (838–909)** | ← **정점** |
> | 6.25% | 혼합 47:53 | 654 (617–702) | 절벽 |
> | 7.00% | bitmap 100% | **394 (381–406)** | ← **최저** |
> | 12.00% | bitmap 100% | 422 (410–447) | |
> | 50.00% | bitmap 100% | 496 (454–529) | 다시 오름 (기전 미상 — 분기 가설은 F-028에서 약해짐) |
>
> **삭제를 더 많이 할수록 행당 체크가 싸진다 — 어느 지점까지만.**
> 최악은 d≈6.1%(경계 직전)이고, 최적은 6.4~7%라는 좁은 골짜기입니다.
> 비싼 쪽은 한 점이 아니라 **경계 아래 구간 전체**입니다.
>
> - 경계 전후 **2.22배**, 전이 구간 폭 **0.30%p** (이항분포 예측과 3.7%p 이내 일치)
> - DV 체크가 스캔 CPU에서 차지하는 비중은 **투영 폭의 함수**입니다 — 1컬럼 **53.2%** ↔ 20컬럼 **3.75%**.
>   큰 쪽만 인용하면 오해를 부르므로 **범위로 적습니다.** 0.5%만 삭제해도 wall-clock +13%
> - 대조군(삭제 0)에서 DV 샘플 **정확히 0** → 분류기 오탐 없음
>
> ### ② 삭제율을 안 바꾸고도 3.53배 — 단, 문턱이 있다
>
> 밀도를 0.5%로 고정하고 클러스터링만 바꾸면 비용 **3.53배**, DV 크기 **1,115배** 차이.
> 다만 `L=1…8` 구간은 서로 구분되지 않습니다 — **청크당 run 개수가 한 자릿수로 떨어져야**
> 효과가 나옵니다. `runOptimize()`의 전환 임계값은 공간 기준이지 속도 기준이 아닙니다.
>
> **그리고 그 3.53배의 이유는 'run 컨테이너' 가 아니었습니다** (F-036).
> 컨테이너 **타입을 그대로 두고 개수만** 7.2배 줄여도 **2.22배**가 나옵니다.
> run 구조 자체의 몫은 ≈1.59배이고, 나머지는 **삭제가 있는 청크가 줄어든 것**입니다.
> 그래서 처방의 문장은 "run 이 되게 하라"가 아니라 **"청크를 비워라"** 입니다.
>
> ### ③ 실제로 고쳤습니다 — 최악 지점에서 9.28배
>
> `apache-iceberg-1.11.0`을 클론해 175줄을 고치고 다시 빌드해, **같은 테이블에 jar만 바꿔** 측정:
>
> | 설정 | 컨테이너 | 현행 | 패치본 | 개선 |
> |---|---|---|---|---|
> | 0.5% | array | 674 | **79** | **8.56배** |
> | **6.1%** | array | **873** | **94** | **9.28배** |
> | 7.0% | bitmap | 420 | 113 | 3.73배 |
> | 8.0% | bitmap | 499 | 142 | 3.51배 |
> | 50.0% | bitmap | 584 | 225 | **2.60배** ← 하한 |
> | 0.5% 정렬 (L=4096) | run | 185 | 61 | 3.05배 |
>
> 밀도 전 구간에 patched 값이 있습니다. **어디서도 손해가 아니고, 최대 수혜 구간이
> 정확히 현행의 최악 지점(d=6.1%)입니다.** 고삭제율 쪽 하한은 **2.60배** (F-039).
>
> DV 체크가 스캔 CPU의 **49.4% → 9.6%**, 스캔 서브트리 전체가 **−44.8%**.
> 정확성 검증(7개 테이블 `count`/`sum(id)`/`min·max`) 전부 일치, Iceberg 자체 테스트 72개 통과.
> **가장 나쁜 곳에서 가장 크게 이깁니다** — 없앤 것이 행당 이진 탐색이고, 그게 가장 비싼 곳이 array이기 때문입니다.
>
> 작업 기록: [upstream/README.md](upstream/README.md) · 패치: [upstream/range-scan.patch](upstream/range-scan.patch)
>
> **원안이 가설로 적었던 사슬이 전부 측정으로 채워졌고, 마지막 고리까지 닫혔습니다.**

---

## 0. TL;DR — 검증 결과 요약

원안을 그대로 승인하지 않고, 실제 Iceberg / RoaringBitmap 소스를 읽어 전제를 하나씩 검증했습니다.

| 원안의 주장 | 검증 결과 | 조치 |
|---|---|---|
| Iceberg 1.11.0(2026-05)에서 DV가 V3 기본 삭제 방식으로 안정화 | ✅ **사실** (1.11.0, 2026-05-19 릴리스) | 시의성 유지 |
| 삭제 체크가 실제로 행 단위 hot loop에 있다 | ✅ **사실, 그리고 원안보다 강함** — `ColumnarBatchUtil`에서 배치당 `batchSize`번 `contains()` 호출 | 최우선 공략 지점으로 승격 |
| Java RoaringBitmap은 명시적 SIMD가 없다 | ✅ **사실** (컨테이너 소스 전체에 `jdk.incubator.vector` 0건) | 단, 아래 ⚠️ 참조 |
| Phase 2에서 `runOptimize()` 호출을 강제해 효과 측정 | ❌ **Iceberg가 이미 호출 중** (`BitmapPositionDeleteIndex.java:126`) | **주제 재설계** → §4.2 |
| Phase 2에서 "DV compaction(rewrite) 주기" 측정 | ❌ `rewrite_position_delete_files`는 DV에 적용 안 됨 | `rewrite_data_files`로 교체 → §4.3 |
| 삭제 패턴 A(1% 랜덤) / B(연속 30%) / C(희소) 3종 비교 | ❌ **설계 결함: A와 C가 같은 컨테이너를 낳음** | 실험 변수 재정의 → §4.1 |
| "SIMD 부재로 CRoaring 대비 손해" | ⚠️ **머지(OR)에만 해당. 행 단위 `contains()`에는 SIMD가 원래 안 쓰임** | 가설 분리 → §4.4 |
| "삭제 패턴에 따라 N배 느려진다" (블로그 제목) | ⚠️ **마이크로 레벨에선 참일 가능성 높으나 쿼리 레벨에선 미검증** | **Phase 0 go/no-go 게이트 신설** → §5.1 |

**결론: 진행 가치 있음. 단 원안 그대로는 안 됨.** 가장 큰 변경은 두 가지입니다.

1. **"플레임그래프로 비중 확인"을 맨 마지막이 아니라 맨 처음에** 두어, 이 주제가 유의미한지부터 판정한다 (Phase 0).
2. 실험의 독립변수를 "삭제 패턴 A/B/C"가 아니라 **"청크당 삭제 밀도 × 클러스터링 계수 × 청크 점유율"** 로 재정의한다.

---

## 1. 선행 연구 조사 — 내가 선두주자인가?

### 1.1 조사 범위와 결과

| 영역 | 존재하는 것 | 존재하지 않는 것 (= 기여 지점) |
|---|---|---|
| Roaring Bitmap 자료구조 | Chambi et al. 2016, Lemire et al. 2018 (SPE) — 라이브러리 레벨 설계·SIMD 알고리즘 | **실제 워크로드가 컨테이너 분포를 어떻게 만드는지**에 대한 측정 |
| Iceberg DV | 스펙 문서, 벤더 블로그(Dremio/AWS/Databricks) 다수 — 전부 "Roaring이라 효율적" 수준의 개념 설명 | 컨테이너 타입별 CPU 비용, 리더 코드 경로 분석 |
| DV 성능 벤치마크 | MERGE/DELETE **쓰기** 비용, V2 positional delete 대비 파일 수 감소 | **읽기 경로의 마이크로아키텍처 비용** (IPC / branch-miss / cache-miss) |
| Java vs 네이티브 Roaring | CRoaring README의 라이브러리 마이크로벤치 | Iceberg 워크로드에 연결한 격차 측정 |

### 1.2 판정

**"삭제 워크로드 → 컨테이너 타입 분포 → 마이크로아키텍처 비용 → 테이블 설계 처방"을 끝까지 연결한 공개 자료는 확인되지 않았습니다.** 이 사슬 전체가 novelty입니다.

단, 정직하게 말하면 novelty의 성격은 **"새로운 발견"이 아니라 "아무도 측정 안 한 것의 측정"** 입니다. Roaring 컨테이너가 밀도에 따라 갈린다는 사실 자체는 2016년부터 알려져 있습니다. 그러므로 이 프로젝트의 가치는 다음 두 가지에서 나옵니다.

- **(a) 처방의 구체성**: "이 레이아웃이면 스캔 비용이 X배" 같은, 실무자가 내일 적용 가능한 수치
- **(b) 업스트림 기여**: §7의 코드 개선이 머지되면 novelty 논쟁이 무의미해짐

### 1.3 시의성 (강함)

- Iceberg **1.11.0 (2026-05-19)**: V3 + DV가 실험 기능에서 **프로덕션 기본 경로**로 승격
- Iceberg V4 논의에서 equality delete 폐기 방향 → DV 중요도 상승
- **Impala가 자체 Iceberg-conform Roaring bitmap을 구현 중** (IMPALA-14586), Comet/DataFusion은 Rust `croaring` 사용 → **"Java Roaring vs 네이티브 Roaring"은 가상의 질문이 아니라 지금 생태계에서 실제로 갈라지고 있는 선택**

이게 Java-vs-네이티브 비교에 명분을 줍니다. "Java가 SIMD 없어서 아쉽다"가 아니라 **"엔진들이 왜 자체 구현으로 갈라지고 있는지를 수치로 설명한다"** 가 됩니다.

---

## 2. 검증된 코드 레벨 사실 (이 프로젝트의 기반)

> 아래는 전부 `apache/iceberg` `main` 브랜치와 `RoaringBitmap/RoaringBitmap` `master`를 2026-08-17 기준으로 직접 읽어 확인한 내용입니다. 개념 설명이 아니라 근거입니다.

### 2.1 Iceberg의 DV는 표준 `Roaring64Bitmap`이 아니다

`core/src/main/java/org/apache/iceberg/deletes/RoaringPositionBitmap.java`

```java
import org.roaringbitmap.RoaringBitmap;   // 32비트 Roaring. Roaring64Bitmap 아님.
```

- 64비트 position을 상위 32비트 `key` / 하위 32비트로 쪼개고, `RoaringBitmap[] bitmaps` 배열을 `key`로 **직접 인덱싱**합니다 (`allocateBitmapsIfNeeded`).
- 사용 라이브러리 버전: `gradle/libs.versions.toml` → `roaringbitmap = "1.6.20"`

```java
// RoaringPositionBitmap.java:142
public boolean contains(long pos) {
  ...
  return key < bitmaps.length && bitmaps[key].contains(pos32Bits);
}
```

→ **행 하나당 2단 간접 참조**: 배열 경계 체크 + `RoaringBitmap.contains()`.

### 2.2 `RoaringBitmap.contains()`는 매 호출마다 최상위 이진 탐색을 한다

`RoaringArray.java`

```java
// :739
int getIndex(char x) { ... return this.binarySearch(0, size, x); }   // :221 binarySearch
```

→ 컨테이너를 찾기 위한 이진 탐색이 **행마다 반복**됩니다. 배치 안의 position은 단조 증가인데도 재사용되지 않습니다.

### 2.3 Spark 벡터화 리더의 실제 hot loop

`spark/v4.0/spark/src/main/java/org/apache/iceberg/spark/data/vectorized/ColumnarBatchUtil.java`

```java
// :57  buildRowIdMapping(...)
PositionDeleteIndex deletedPositions = deletes.deletedRowPositions();   // :66
int[] rowIdMapping = new int[batchSize];                                 // :69
for (int rowId = 0; rowId < batchSize; rowId++) {                        // :72
  if (isDeleted(pos, row, deletedPositions, eqDeleteFilter)) { ... }     // :75
  else { rowIdMapping[liveRowId] = rowId; ... }                          // :78
}

// :137 private static boolean isDeleted(...)
if (deletedPositions != null && deletedPositions.isDeleted(pos)) { ... } // :143
```

`buildIsDeleted()` (`:110`~`:134`)도 동일한 루프 형태입니다.

**→ 배치 크기(기본 5000)만큼 `contains()` 호출 = 5000번의 최상위 이진 탐색 + 5000번의 컨테이너 내 탐색.**
이게 원안 가설이 겨냥한 바로 그 지점이고, 원안보다 훨씬 구체적인 공략 지점입니다. §7.1의 업스트림 PR 후보가 여기서 나옵니다.

### 2.4 Iceberg는 이미 쓰기 경로에서 `runOptimize()`를 호출한다

`BitmapPositionDeleteIndex.java`

```java
// :125
public ByteBuffer serialize() {
  bitmap.runLengthEncode();   // :126  run-length encode the bitmap before serializing
  ...
}
```

`RoaringPositionBitmap.java:176`의 `runLengthEncode()`가 내부 비트맵마다 `runOptimize()`를 호출합니다.

**중요한 함의**: `RoaringBitmap.runOptimize()`는 **RLE가 공간을 절약할 때만** run 컨테이너로 변환합니다. 스캔이 빨라지는지는 판단 기준이 아닙니다. 즉 Iceberg의 DV는 **space-optimal하게 직렬화되고, 읽기 측은 그 컨테이너를 그대로 역직렬화해서 씁니다.** "공간 최적 ≠ 스캔 최적"의 간극이 실재한다면 그건 이 프로젝트의 가장 좋은 발견이 됩니다 (§4.2).

### 2.5 Java RoaringBitmap에는 명시적 SIMD가 없다

`roaringbitmap/src/main/java/org/roaringbitmap/` 의 `ArrayContainer`(1649줄), `BitmapContainer`(1961줄), `RunContainer`(2990줄), `Util`(1287줄), `RoaringArray`(1128줄) 전체에서 `jdk.incubator.vector` **0건**. 리포지토리 트리에 Vector API 모듈 자체가 없음.

**단, "SIMD가 전혀 안 돈다"는 아닙니다.** HotSpot C2는 `BitmapContainer`의 `long[]` AND/OR 루프 같은 단순 형태를 자동 벡터화합니다. 정확한 표현은 **"명시적 SIMD intrinsic이 없고, JIT 자동 벡터화에 의존한다"** 이며, 자동 벡터화가 실패하는 곳(`ArrayContainer`의 이진 탐색/갤러핑)이 진짜 격차 지점입니다. **이건 주장하지 말고 `-prof perfasm`으로 확인해야 합니다.**

---

## 3. 배경 지식 (최소한만)

Roaring bitmap은 32비트 정수 공간을 상위 16비트 기준 **청크(2^16 = 65,536개 position)** 로 나누고, 청크마다 밀도에 따라 컨테이너를 고릅니다.

| 컨테이너 | 조건 | 표현 | 멤버십 체크 | 마이크로아키텍처 특성 |
|---|---|---|---|---|
| **array** | 카디널리티 ≤ 4096 | 정렬된 `char[]` | 이진 탐색 O(log n) | 행당 283~370 명령어 (F-028) |
| **bitmap** | 카디널리티 > 4096 | 고정 8KB `long[1024]` | 비트 연산 O(1) | 분기 거의 없음, L1에 딱 맞음 |
| **run** | RLE가 더 작을 때 | `(start, len)` 쌍 배열 | 이진 탐색 O(log r) | run 수가 적으면 매우 빠름, 많으면 array보다 나쁨 |

**핵심 임계값**: array → bitmap 전환은 `4096 / 65536` = **청크 밀도 6.25%**.
이 6.25% 경계가 이 프로젝트의 "money shot"입니다. 경계 양쪽에서 저장 크기는 연속적으로 변하는데 **CPU 비용은 불연속적으로 점프할 것**이라는 게 검증 대상입니다.

> ✅ **2026-08-17 확인됨 — [findings.md F-001, F-004](docs/findings.md)**
> 경계가 정확히 카디널리티 4096임을 실측했고(4096→array, 4097→bitmap),
> 그 경계에서 **행당 체크 비용이 16.0ns → 4.0ns 로 4.1배 점프**한다.
> **방향이 직관과 반대다: 삭제를 더 많이 할수록 행당 체크가 싸진다.**
> 6.25% 아래 구간 전체가 오히려 행당 비용이 가장 비싼 영역이다.

---

## 4. 원안 대비 핵심 수정 사항

### 4.1 ⚠️ 설계 결함: 삭제 패턴 A/B/C는 독립변수가 아니다

원안: `A: 전체 1% 균등 랜덤` / `B: 연속 30% 범위` / `C: 파일당 수십 건 희소`

**문제**: 컨테이너 타입을 결정하는 건 "파일 전체의 삭제 비율"이 아니라 **"65,536-position 청크 하나 안의 삭제 밀도"** 입니다.

계산해 봅시다. 128MB Parquet, 행당 ~64B 가정 → 파일당 약 200만 행 → **청크 약 31개**.

- **패턴 A**: 200만 × 1% = 20,000개 삭제 / 31청크 ≈ **청크당 645개** → 4096 미만 → **array 컨테이너**
- **패턴 C**: 청크당 1~2개 → **array 컨테이너**
- **패턴 B**: 30% 연속 → 청크 ~9개가 완전히 채워짐 → **run 컨테이너 (run 1개)** + 경계 청크 1~2개

**→ A와 C가 같은 컨테이너 타입을 만듭니다. 3개 조건 중 2개가 중복이고, bitmap 컨테이너는 아예 등장하지 않습니다.**
가장 중요한 array↔bitmap 경계를 못 건드리는 실험 설계입니다.

**수정: 실험 독립변수를 3축으로 재정의합니다.**

| 축 | 기호 | 수준 | 의미 |
|---|---|---|---|
| **청크 내 삭제 밀도** | `d` | 0.05%, 0.5%, 3%, **5%, 6.25%, 8%**, 12%, 50%, 95% | array↔bitmap 경계(6.25%)를 촘촘히 스윕 |
| **클러스터링 계수** (평균 run 길이) | `C` | 1(완전 랜덤), 8, 64, 1024, ∞(단일 연속 블록) | run 컨테이너 물질화 여부 |
| **청크 점유율** (삭제가 존재하는 청크 비율) | `p` | 5%, 50%, 100% | `RoaringArray` 최상위 이진 탐색 비용 + 컨테이너 부재 fast-path |

- 전수 조사는 9×5×3 = 135조합 → **fractional design**으로 축소: `d` 전체 스윕 × `C=1` (주 효과) + `C` 전체 스윕 × `d=3%, 50%` (교호작용) + `p` 는 `d=0.5%, C=1` 에서만.
- **원안의 A/B/C는 이 공간의 3개 점으로 재해석해서, "현실 워크로드가 이 격자의 어디에 떨어지는가"를 매핑하는 데 씁니다.** 버리는 게 아니라 격자에 좌표를 찍는 용도입니다.
  - A(1% 랜덤) → `d≈1%, C=1, p=100%`
  - B(연속 30%) → `d=100%, C=∞, p=30%`
  - C(희소) → `d≈0.05%, C=1, p=100%`
  - + CDC 현실 패턴: `d≈0.1~2%, C=1~4, p=60~100%` ← **실무자가 가장 궁금해할 영역**

### 4.2 `runOptimize()` 주제 재설계: "space-optimal ≠ scan-optimal"

Iceberg는 이미 직렬화 직전 `runLengthEncode()`를 호출합니다(§2.4). 원안의 "호출 강제" 실험은 성립하지 않습니다.

**대신 훨씬 좋은 질문으로 바꿉니다:**

> `runOptimize()`는 **바이트를 줄일 때만** run으로 바꾼다. 그런데 run 컨테이너가 항상 더 빨리 읽히는가?

- 가설: run 수가 많은(파편화된) 구간에서 `RunContainer.contains()`의 이진 탐색은 `BitmapContainer`의 O(1) 비트 테스트보다 느리다. 그런데 RLE가 몇 바이트라도 작으면 Roaring은 run을 선택한다.
- **`runOptimize()`가 크기는 줄이지만 스캔은 느려지는 (d, C) 영역이 존재하는가?** 존재한다면 그 영역의 넓이는?
- 이건 Iceberg에도, Roaring 라이브러리에도, 어떤 논문에도 답이 없는 질문이고, **답이 "yes"면 곧바로 업스트림 이슈**가 됩니다.

### 4.3 DV compaction 실험 수정

`rewrite_position_delete_files` 프로시저는 **DV에 적용되지 않습니다.** V3 DV의 "compaction"은 `rewrite_data_files`로 데이터 파일을 다시 쓰면서 삭제를 물리적으로 반영하고 DV를 소멸시키는 것입니다.

**수정된 실험**: 반복 DELETE로 DV를 누적시키며(스냅샷마다 DV 교체) **DV 카디널리티 성장 곡선에 따른 컨테이너 타입 전이(array → bitmap)** 를 추적하고, `rewrite_data_files` 임계점을 **"6.25% 경계를 넘기 전/후"** 기준으로 제안합니다.
→ 산출물: **"DV 카디널리티가 파일 행수의 6.25%를 넘으면 compaction하라"** 같은 형태의, 근거 있는 운영 규칙.

### 4.4 CRoaring 비교 가설 분리

**SIMD는 스칼라 `contains()`를 빠르게 하지 않습니다.** Lemire의 벡터화 알고리즘은 **array 간 교집합/합집합/차집합** 대상입니다. 따라서:

| 벤치마크 | Java vs CRoaring 격차의 원인 | SIMD 관련성 |
|---|---|---|
| 행 단위 `contains()` | 경계 체크, 2단 간접, JIT 코드 품질, 메모리 레이아웃 | ❌ 무관 |
| 비트맵 머지 `or()` / `and()` | 벡터화된 array 교집합/합집합 | ✅ **여기가 SIMD 영역** |
| `rank` / `select` / 벌크 반복 | popcount 벡터화 | ✅ 일부 |

가설 3을 **"머지 연산에서만 SIMD 격차가 나타나고, 멤버십 체크 격차는 다른 원인에서 온다"** 로 수정하고, 두 원인을 분리해 보이는 것 자체를 결과로 삼습니다. 원안대로 뭉뚱그리면 리뷰어에게 바로 지적당합니다.

### 4.5 ⚠️ 최대 리스크: 진폭 문제 (amplitude)

**`contains()`가 마이크로벤치에서 5배 차이 나도, 전체 스캔의 1%밖에 안 차지하면 쿼리는 0.8% 빨라집니다.**

- 행당 `contains()`: 대략 5~30 사이클 추정
- 행당 Parquet 디코드 + ColumnVector 물질화 + Spark 소비: 수백~수천 사이클
- → DV 체크 비중이 **1~3%** 일 가능성이 현실적으로 있습니다.

원안은 이 확인을 Phase 1의 **마지막 단계**에 뒀습니다. **순서를 뒤집어야 합니다.** 3주 measurement 다 하고 나서 "사실 별 의미 없었다"를 알게 되는 건 최악입니다.

**→ Phase 0 (go/no-go 게이트) 신설.** §5.1.

또한 블로그 제목/주장을 처음부터 정직하게 설계합니다.

- ❌ "Iceberg DV는 삭제 패턴에 따라 N배 느려진다" (쿼리 레벨로 오독됨)
- ✅ "Iceberg DV의 삭제 체크는 패턴에 따라 N배 느려진다 — 그게 쿼리에 얼마나 보이는가"

비중이 작다는 결과가 나와도 **그 자체가 공개할 가치가 있는 결과**입니다("DV 읽기 오버헤드는 무시 가능하다 — 근거는 이것"). 다만 프로젝트의 무게중심은 §7.1의 코드 개선으로 옮깁니다.

---

## 5. 실행 계획

### 5.1 Phase 0 — Go/No-Go 게이트 (2~3일) 🚦

**목적**: 이 주제에 3주를 쓸 가치가 있는지 먼저 판정한다.

1. Spark local + Iceberg 1.11.x, V3 테이블(`format-version=3`), 200만 행 × 파일 10개
2. 극단 두 조건만 생성: `d=0.5%, C=1` (array 컨테이너) / `d=100%, C=∞` (run 컨테이너)
3. `SELECT count(*)` 및 `SELECT <몇 개 컬럼>` 풀스캔을 async-profiler로 CPU 프로파일
4. **측정할 단 하나의 숫자**: 전체 CPU 샘플 중 `ColumnarBatchUtil` + `RoaringPositionBitmap` + `org.roaringbitmap.*` 서브트리의 비율

| 결과 | 판정 |
|---|---|
| **≥ 5%** | ✅ 원안대로 전면 진행. 헤드라인은 "스캔 CPU의 X%를 차지하는 DV 체크" |
| **1~5%** | ⚠️ 진행하되 무게중심 이동: 마이크로벤치는 유지, 헤드라인은 **§7.1 코드 개선**과 **레이아웃 처방**으로 |
| **< 1%** | 🛑 방향 전환. "DV 읽기 비용은 무시 가능하다"를 짧은 글로 내고, 병목이 실제로 있는 곳(DV 파일 I/O, planning, Puffin 파싱)으로 주제 이동 |

> `count(*)`는 메타데이터로 최적화될 수 있으니 반드시 컬럼을 물질화하는 쿼리도 함께 돌릴 것. 그리고 **DV 체크 비중은 선택하는 컬럼 수에 반비례**하므로, "1컬럼 스캔"과 "20컬럼 스캔" 양쪽을 재서 비중의 범위를 보고할 것.

### 5.2 Phase 1 — 컨테이너 물질화 매핑 (1주)

**산출물: 삭제 워크로드 → 컨테이너 분포 지도**

1. **DV 생성기 작성** (`tools/dv-generator`): `(d, C, p)` 파라미터로 결정론적 DV를 만드는 유틸. 시드 고정.
2. **컨테이너 통계 덤퍼** (`tools/dv-inspect`): Puffin blob을 읽어 `RoaringPositionBitmap`으로 역직렬화한 뒤, 리플렉션 또는 `RoaringArray` 접근으로 청크별 `(컨테이너 타입, 카디널리티, run 수, 바이트 크기)` 를 CSV로 덤프.
   - Iceberg 쪽 진입점: `BitmapPositionDeleteIndex.deserialize(byte[], DeleteFile)`
3. **격자 전 조합 실행** → `container_map.csv`
4. **검증 대상 예측**:
   - `d < 6.25%, C=1` → array 100%
   - `d > 6.25%, C=1` → bitmap 100%
   - `C ≥ 64` → run 비율 상승, `runOptimize()` 발동 조건과 일치하는지 확인
   - **경계(5%/6.25%/8%)에서 실제 전환이 예측대로 일어나는가** ← 여기가 핵심 확인

### 5.3 Phase 2 — 마이크로아키텍처 비용 측정 (1주)

**환경 요건**

- ~~**PMU 하드웨어 카운터는 AWS `.metal` 인스턴스에서만 신뢰 가능합니다.**~~ → **WSL2 에서도 나옵니다** (F-028). Hyper-V vPMU 가 `cycles`/`branch-misses`/`cache-misses` 를 노출하고, sorted-vs-shuffled 통제 실험으로 값이 진짜인지도 확인했습니다(11.8배 차이). 클라우드 가상화 인스턴스는 여전히 확인이 필요합니다.
- 검증: `perf stat -e cycles,instructions,branch-misses,cache-misses sleep 1` 이 `<not supported>` 없이 나오는지 **먼저** 확인
- `c7i.metal-24xl` 정밀 측정 세션(짧게, 스팟) + `c7i.2xlarge`(개발 반복). **개발은 로컬/저가 인스턴스, metal은 최종 측정에만 붙이고 즉시 내림.**
- `cpupower frequency-set -g performance`, C-state 제한, THP 설정 고정, 코어 핀 고정(`taskset`)
- JDK 17 (Iceberg 기본) + JDK 21 (Vector API 비교용)

**JMH 벤치 3종** (`bench/`)

| 벤치 | 대상 | 왜 |
|---|---|---|
| `B1_PerRowContains` | 현행 `ColumnarBatchUtil.buildRowIdMapping` 루프 재현 (batchSize=5000, position 단조 증가) | 실제 hot loop의 충실한 복제 |
| `B2_BitmapMerge` | `RoaringPositionBitmap.setAll()` (내부 `or`) | 여러 DV 머지 / SIMD 영역 |
| `B3_BatchAlternatives` | §7.1 후보 구현들 (컨테이너 hoisting, `BatchIterator`, `forEachInRange`) | 개선 프로토타입 |

**측정 프로토콜**

```bash
# JMH 내장 perf 프로파일러 (metal 필수)
java -jar bench.jar B1 -prof perfnorm -prof perfasm \
     -f 5 -wi 10 -i 20 -bm avgt -tu ns

# 독립 perf stat 교차 검증
perf stat -e cycles,instructions,branches,branch-misses,\
L1-dcache-loads,L1-dcache-load-misses,LLC-load-misses \
  -- java -jar bench.jar B1 ...
```

- `-prof perfnorm` 이 **연산당 정규화된 사이클/명령어/분기 실패**를 직접 줍니다. 원안의 `perf stat` 수동 파싱보다 이게 정확합니다.
- `-prof perfasm` 으로 **어떤 어셈블리에서 사이클이 타는지** 확인 → §2.5의 "자동 벡터화 여부"를 추측이 아니라 관찰로 확정. (`hsdis` 필요)
- **통계**: fork ≥ 5, 신뢰구간 겹치면 "차이 없음"으로 보고. 단일 숫자로 "N배"라고 쓰지 않기.
- **주의**: JMH 마이크로벤치는 비트맵이 L1/L2에 상주해 실제보다 유리합니다. 컨테이너 8KB × N개가 실제 스캔에서 겪는 캐시 압력을 재현하려면 **캐시 오염 배리어**(더미 배열 순회)를 넣은 변형도 함께 돌릴 것.

**CRoaring 비교** (§4.4대로 분리)

- 동일 Puffin blob의 portable serialization을 CRoaring `roaring_bitmap_portable_deserialize()`로 로드 → 동일 연산 측정
- 빌드 시 `-march=native`, AVX-512 유무 두 버전
- **보고 형식**: `contains` 격차와 `or` 격차를 **따로** 표기하고, 후자에만 SIMD를 원인으로 귀속

### 5.4 Phase 3 — 최적화 (1~1.5주)

#### 3-A. 테이블 설계 레벨

- 동일 논리 삭제(`DELETE WHERE user_id IN (...)`)를 **삭제 키로 정렬/클러스터링된 테이블** vs **무정렬 테이블**에 적용
- 측정: 컨테이너 분포 변화, DV 총 바이트, 스캔 CPU
- **⚠️ 반드시 통제할 교란 변수**: 정렬 테이블은 삭제가 소수 파일에 집중되어 **DV가 아예 없는 파일이 늘어납니다.** 그러면 이득의 상당 부분이 "컨테이너 타입"이 아니라 **"파일 스킵"** 에서 옵니다.
  - 통제 방법: **DV가 존재하는 파일만** 대상으로 한 `스캔된 행당 CPU 비용`을 별도 지표로 보고. 두 효과를 분리해서 각각 정량화.
- 산출물: **레이아웃 처방 표** — (워크로드 유형 × 정렬 키 선택 × 예상 컨테이너 × 예상 비용)

#### 3-B. 코드 레벨 (§7 참조)

배치 루프 개선 프로토타입 → 개선 전후를 Phase 2와 **동일 방법**으로 측정 → `"개선율 X%, 기전은 branch-miss Y% 감소 / 이진 탐색 N회 제거"` 형태로 기전까지 설명.

### 5.5 Phase 4 — 클러스터 일반화 (3~4일)

- `c7i.2xlarge × 3` + S3, 동일 테이블 분산 스캔
- **불변량**: ① 삭제 행 1개당 체크 비용 ② 컨테이너 타입 간 비용 비율
- **⚠️ 원안 수정**: 비-metal executor에서는 PMU를 못 씁니다. `±10% 일치` 검증은 하드웨어 카운터가 아니라 **async-profiler CPU 샘플 비율 + wall-clock 기반 프록시**로 해야 하고, **프록시임을 명시**해야 합니다. 사이클 정밀도를 클러스터에서 주장하지 마세요.
- S3 도입 시 DV GET 레이턴시가 새 변수 → **"CPU 비용은 불변, I/O가 상대 비중을 희석"** 을 분리해 보이기. (DV는 파일당 수 KB이므로 GET 레이턴시(~20-50ms)가 CPU를 완전히 압도할 가능성이 높음 → 이것 자체가 좋은 결과: **"단일 노드에서 유의미했던 CPU 차이가 S3에서는 I/O에 묻힌다. 따라서 이 최적화는 로컬 SSD/캐시 환경에서 가치가 있다"**)

---

## 6. 기대 효과 (무엇을 얻는가)

| 대상 | 효과 |
|---|---|
| **실무자** | "내 CDC 테이블은 격자의 어디에 있고, 정렬 키를 이렇게 바꾸면 스캔 CPU가 X% 준다"는 즉시 적용 가능한 처방 |
| **Iceberg 프로젝트** | 행 단위 `contains()` 루프 개선 (§7.1) — 작고, 명백하고, 측정 가능한 PR |
| **Roaring 생태계** | "space-optimal `runOptimize()`가 scan-optimal이 아닌 영역"의 최초 정량화 (§4.2) |
| **본인** | 스펙 → 소스 → 마이크로아키텍처 → 분산 실행까지 한 사슬로 꿰는 능력의 증명. 이 조합을 보여주는 포트폴리오는 드묾 |
| **콘텐츠** | 개념 반복이 아니라 **원본 측정치**를 가진 글. 인용될 가능성이 있는 유일한 종류 |

---

## 7. 업스트림 기여 후보 (우선순위)

### 7.1 🥇 `ColumnarBatchUtil` 배치 루프 최적화 — 성공 확률 최고

**현상** (§2.3): 배치당 `batchSize`번 `contains()` 호출. 각각이 `RoaringArray` 최상위 이진 탐색 + 컨테이너 내 탐색.

**관찰**: 한 배치의 position은 `[rowStart, rowStart + batchSize)` 의 **연속 단조 증가 구간**입니다. 최상위 이진 탐색 결과는 배치 내내(보통) 동일합니다.

> ✅ **2026-08-17 측정 완료 — [findings.md F-002](docs/findings.md)**
> `RoaringBitmap 1.6.20`(Iceberg가 핀한 그 버전)에 이미 벌크 API가 있고,
> **rowIdMapping 경로 6.4~146배, isDeleted 경로 13~67배** 개선을 확인했다.
> 가장 나쁜 조건(청크 밀도 12%)에서도 4.7~6.4배. 6개 구현이 baseline과 동일한 결과를 냄을 assert로 검증.
> 권장안은 **`forAllInRange` + `RelativeRangeConsumer`**.

**개선 방향** (모두 프로토타입 후 B3 벤치로 비교):

| 방식 | 아이디어 | 예상 이득 |
|---|---|---|
| (a) 컨테이너 hoisting | 루프 밖에서 컨테이너를 1회 찾고 안에서는 컨테이너 내 체크만 | 이진 탐색 N회 → 1~2회 |
| (b) 역발상 반복 | 5000행을 순회하며 "삭제됐나?" 묻는 대신, **삭제된 position만 반복**해서 그 자리에만 마킹 (`forEachInRange` / `BatchIterator`) | O(batchSize) → **O(해당 구간 삭제 수)**. 희소 삭제(가장 흔한 케이스)에서 극적 |
| (c) fast-path | 해당 구간에 삭제가 0개면 (`rangeCardinality == 0`) 루프 전체 스킵 | DV는 있지만 이 배치엔 삭제 없는 매우 흔한 경우 |

(b)+(c) 조합이 유력합니다. **삭제율 1% 테이블에서 5000번의 체크가 50번의 마킹으로 줄어듭니다.**

- 변경 범위: `ColumnarBatchUtil.java` 한 파일, 두 메서드. 작은 diff.
- 정확성 논증이 자명 (동치 변환)
- JMH 수치 첨부 가능
- → **Iceberg에 이슈 + PR 제출**. 이게 프로젝트 최고 가치 산출물이 될 가능성이 큽니다.

> ⚠️ PR 전에 반드시 `git log`/이슈 검색으로 이미 논의된 적 없는지 확인할 것. `#12061 (Refactor delete logic in batch reading)` 히스토리부터 읽으세요.

### 7.2 🥈 `runOptimize()` 휴리스틱 이슈 (§4.2)

"크기는 줄지만 스캔은 느려지는" 영역이 실측되면 → RoaringBitmap 리포에 데이터와 함께 이슈. 라이브러리 정책 변경은 어렵겠지만, **Iceberg 쪽에서 조건부로 호출을 조정**하는 건 가능할 수 있음.

### 7.3 🥉 Vector API 프로토타입 (탐색적)

- JDK 21+ `jdk.incubator.vector`로 `ArrayContainer` 탐색 벡터화
- **기대치를 낮게 잡을 것**: 단일 `contains()`는 벡터화 이득이 거의 없고(§4.4), 벌크 연산이라야 의미가 있습니다. 그리고 Iceberg는 JDK 17을 지원해야 하므로 incubator 모듈 의존은 업스트림 머지 가능성이 낮습니다.
- **"업스트림 기여"가 아니라 "무엇이 가능했을지에 대한 측정"** 으로 포지셔닝. 블로그의 마지막 섹션 소재.

---

## 8. 리스크 레지스터

| # | 리스크 | 확률 | 영향 | 완화 |
|---|---|---|---|---|
| R1 | DV 체크 비중이 쿼리 대비 미미 | **높음** | 헤드라인 붕괴 | **Phase 0 게이트** (§5.1). 무게중심을 §7.1로 이동 준비 |
| R2 | metal 스팟 확보 실패 / 비용 초과 | 중 | 정밀 측정 불가 | 개발은 저가 인스턴스, metal은 최종 세션만(수 시간). 온디맨드 폴백 예산 확보. 로컬 리눅스 물리 머신이 있으면 그게 최선 |
| R3 | JIT가 벤치를 최적화해 없애버림 | 중 | 결과 무효 | `Blackhole` 소비, `@State` 격리, fork≥5, `-prof perfasm`으로 생성 코드 육안 확인 |
| R4 | 마이크로벤치가 캐시 상 유리해 과대평가 | 중 | 결과 과장 | 캐시 오염 배리어 변형 병행 (§5.3) |
| R5 | 컨테이너 통계 접근에 리플렉션 필요 → 버전 취약 | 낮 | 도구 깨짐 | `RoaringBitmap` 1.6.20 고정 명시, `getContainerPointer()` 같은 공개 API 우선 탐색 |
| R6 | §7.1 개선이 이미 논의/구현됨 | 낮~중 | 기여 가치 하락 | PR 전 이슈/커밋 히스토리 검색 (`#12061` 등) |
| R7 | 정렬 레이아웃 효과가 파일 스킵으로 설명됨 | **높음** | 인과 오귀속 | DV 보유 파일만 대상으로 한 정규화 지표 병행 (§5.4-A) |
| R8 | Java vs C 비교가 unfair하다는 비판 | 중 | 신뢰도 하락 | 워밍업/할당/컴파일 플래그 전부 공개, 재현 스크립트 제공, `contains`/`or` 원인 분리 (§4.4) |

---

## 9. 일정 & 비용

| Phase | 기간 | 인스턴스 | 비고 |
|---|---|---|---|
| Phase 0 (게이트) | 2~3일 | 로컬 or c7i.2xlarge | **여기서 진행 여부 결정** |
| Phase 1 (컨테이너 매핑) | 1주 | c7i.2xlarge 스팟 | PMU 불필요 |
| Phase 2 (마이크로아키텍처) | 1주 | c7i.2xlarge + **metal 수 시간** | metal은 최종 측정만 |
| Phase 3 (최적화) | 1~1.5주 | c7i.2xlarge + metal 수 시간 | |
| Phase 4 (클러스터) | 3~4일 | c7i.2xlarge × 3 + S3 | |
| **합계** | **4~5주** | | metal 총 사용 목표 **10시간 이내** |

> 비용은 `c7i.metal-24xl` 온디맨드가 시간당 수 달러 수준입니다(리전·시점에 따라 다르므로 실행 전 콘솔에서 확인). metal 사용을 최종 측정 세션으로만 제한하면 총 컴퓨트 비용은 관리 가능한 범위입니다. **인스턴스를 띄운 채로 개발하지 마세요 — 프로젝트 비용의 90%가 거기서 샙니다.**

---

## 10. 산출물

```
iceberg-dv-anatomy/
├── README.md                  # 이 문서
├── tools/
│   ├── dv-generator/          # (d, C, p) 파라미터 DV 생성기
│   └── dv-inspect/            # Puffin → 컨테이너 통계 CSV 덤퍼
├── bench/
│   ├── B1_PerRowContains.java
│   ├── B2_BitmapMerge.java
│   └── B3_BatchAlternatives.java
├── native/                    # CRoaring 비교 하네스
├── scripts/
│   ├── setup-metal.sh         # 주파수 고정, C-state, perf 검증
│   ├── run-phase{0..4}.sh
│   └── cluster/               # Spark 클러스터 재현
├── results/                   # 원시 CSV + 노트북 (재현성)
└── docs/
    └── findings.md
```

- **블로그**: "Iceberg Deletion Vector의 컨테이너 해부 — 삭제 패턴이 스캔 CPU를 어떻게 가르는가"
  - Phase 0 결과에 따라 부제 결정 (§4.5)
- **업스트림**: 이슈/PR 링크 (§7)
- **재현성**: 시드·인스턴스 타입·JDK 빌드·라이브러리 버전 전부 고정 기록. 원시 CSV 커밋.

---

## 11. 확장 아이디어

- **후속편(분산시스템 레벨)**: optimistic concurrency 커밋 충돌 재시도의 tail latency 분석 — DV는 데이터 파일당 1개이므로, 동일 파일을 건드리는 동시 DELETE가 재시도를 유발합니다. V2 positional delete 대비 충돌 특성이 달라졌을 가능성이 있고 이것도 미측정 영역입니다.
- **크로스 엔진**: Comet(Rust `croaring`) vs Iceberg Java 리더의 동일 테이블 스캔 비교 — §1.3의 생태계 분기를 실측으로 확인.

---

## 12. 레퍼런스

### 자료구조 / SIMD (인용하고 그 위에 쌓을 기반)

- Chambi, Lemire, Kaser, Godin. *Better bitmap performance with Roaring bitmaps.* Software: Practice and Experience, 2016. [arXiv:1402.6407](https://arxiv.org/pdf/1402.6407)
- Lemire, Ssi-Yan-Kai, Kaser. *Consistently faster and smaller compressed bitmaps with Roaring.* SPE, 2016. [arXiv:1603.06549](https://arxiv.org/pdf/1603.06549)
- **Lemire et al. *Roaring Bitmaps: Implementation of an Optimized Software Library.* Software: Practice and Experience 48(4), 2018.** [arXiv:1709.07821](https://arxiv.org/abs/1709.07821) — SIMD 알고리즘의 근거 논문. **핵심 인용.**
- [RoaringBitmap/CRoaring](https://github.com/RoaringBitmap/CRoaring) — AVX2/AVX-512/NEON. Doris·ClickHouse·StarRocks·Redpanda 채택
- [RoaringBitmap/RoaringBitmap](https://github.com/RoaringBitmap/RoaringBitmap) (Java, Iceberg 사용 버전 **1.6.20**)

### Iceberg 스펙 / 소스 (직접 확인 대상)

- [Iceberg Spec — Deletion Vectors](https://iceberg.apache.org/spec/) · [Puffin Spec](https://iceberg.apache.org/puffin-spec/)
- `core/src/main/java/org/apache/iceberg/deletes/RoaringPositionBitmap.java`
- `core/src/main/java/org/apache/iceberg/deletes/BitmapPositionDeleteIndex.java`
- `core/src/main/java/org/apache/iceberg/deletes/BaseDVFileWriter.java`
- `spark/v4.0/spark/src/main/java/org/apache/iceberg/spark/data/vectorized/ColumnarBatchUtil.java` ← **hot loop**
- `gradle/libs.versions.toml` (roaringbitmap 버전 핀)

### 릴리스 / 생태계 맥락

- [What's New in Apache Iceberg 1.11.0 — Dremio](https://www.dremio.com/blog/whats-new-in-apache-iceberg-1-11-0/)
- [An In-Depth Overview of the Apache Iceberg 1.11.0 Release](https://datalakehousehub.com/blog/2026-05-apache-iceberg-1-11-0-deep-dive/)
- [Apache Iceberg 1.11.0 Release: Deletion Vectors, Variant Type, and V3 Maturity](https://dataverses.io/resources/blog/iceberg-1.11.0-release)
- [What's new in Apache Iceberg v3 — Google Open Source Blog](https://opensource.googleblog.com/2025/08/whats-new-in-iceberg-v3.html)
- [Apache Iceberg v3: Moving the Ecosystem Towards Unification — Databricks](https://www.databricks.com/blog/apache-icebergtm-v3-moving-ecosystem-towards-unification)
- [Accelerate data lake operations with Iceberg V3 deletion vectors and row lineage — AWS](https://aws.amazon.com/blogs/big-data/accelerate-data-lake-operations-with-apache-iceberg-v3-deletion-vectors-and-row-lineage/)
- [Iceberg Deletion Vectors: The Better Way to Delete Rows — Dremio](https://www.dremio.com/blog/dremio-iceberg-v3-deletion-vectors/)
- [Why Iceberg V4 Wants to Retire Equality Deletes](https://datalakehousehub.com/blog/equality-deletes-iceberg-v4/)
- [IMPALA-14586: Implement Iceberg-conform Roaring bitmap](http://www.mail-archive.com/issues-all@impala.apache.org/msg50381.html) — 생태계 분기의 증거
- [Delta Lake Deletion Vectors](https://delta.io/blog/2023-07-05-deletion-vectors/) — 비교 대상

### 선행 Iceberg PR/이슈 (§7.1 제출 전 필독)

- [#3141 Support row-level delete in vectorized reader](https://github.com/apache/iceberg/issues/3141)
- [#3287 Support pos-delete in vectorized read](https://github.com/apache/iceberg/pull/3287)
- [#12061 Spark 3.4: Refactor delete logic in batch reading](https://www.mail-archive.com/commits@iceberg.apache.org/msg13763.html)

### 측정 도구

- JMH (`-prof perfnorm`, `-prof perfasm` — `hsdis` 필요) · async-profiler · `perf` (linux-tools) · bpftrace
- [JEP 508/529: Vector API](https://openjdk.org/jeps/508) (Phase 3-B 탐색용)

---

*코드 인용의 파일 경로·행 번호는 `apache/iceberg@main`, `RoaringBitmap/RoaringBitmap@master` 를 2026-08-17에 확인한 기준입니다. 구현 시점에 재확인하세요.*
