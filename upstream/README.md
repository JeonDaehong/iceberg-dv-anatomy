# §7.1 업스트림 작업 기록

`ColumnarBatchUtil` 이 배치의 연속 구간을 활용하지 못하고 행마다 삭제 인덱스를 프로브하는 문제.
**이슈를 내기 전에 실제로 고쳐서 재본다**는 원칙에 따라, Iceberg 소스를 받아 패치하고
같은 테이블에서 패치 전후를 측정했다.

## 왜 이 순서인가

2026-08-18 이전에 이슈 초안(`issue-columnarbatchutil.md`)을 먼저 써두었지만 제출하지 않았다.
이유: 마이크로벤치(F-002)의 6~146배는 **Iceberg 바깥에서** 잰 숫자다.
실제 코드에 넣었을 때 얼마가 남는지는 별개의 질문이고, 그 답 없이 이슈를 내면
"해보니 별거 없더라"로 끝날 위험이 있다. 결정적인 숫자를 먼저 만든다.

## 대상

| | |
|---|---|
| 저장소 | `apache/iceberg` |
| 커밋 | `6976e02` (`apache-iceberg-1.11.0` 태그가 정확히 이 커밋을 가리킴) |
| 브랜치 | `dv-anatomy/range-scan` (로컬 클론, 포크·PR 아님) |
| 위치 | `~/src/iceberg` (WSL2 Ubuntu 24.04) |

Phase 0/1 에서 쓴 릴리스 jar 이 정확히 이 태그에서 나온 것이라, 소스와 측정 대상이 일치한다.

## 패치 내용

```
core/src/main/java/org/apache/iceberg/deletes/
  PositionDeleteIndex.java        +22   default forEachInRange(long, int, LongConsumer)
  BitmapPositionDeleteIndex.java   +5   RoaringPositionBitmap 으로 위임
  EmptyPositionDeleteIndex.java    +5   no-op
  RoaringPositionBitmap.java      +39   RoaringBitmap.forEachInRange 위임, 키 경계 처리

spark/v4.0/spark/src/main/java/org/apache/iceberg/spark/data/vectorized/
  ColumnarBatchUtil.java         +104   equality delete 가 없을 때 구간 경로
```

전체 diff: [`range-scan.patch`](range-scan.patch)

### 초안에서 고친 것 — 게이트 조건

이슈 초안은 빠른 경로를 `eqDeleteFilter == null` 로 게이트하자고 썼다. **틀렸다.**

```java
// DeleteFilter.java:245
public Predicate<T> eqDeletedRowFilter() {
  if (eqDeleteRows == null) {
    eqDeleteRows =
        applyEqDeletes().stream().map(Predicate::negate).reduce(Predicate::and).orElse(t -> true);
  }
  return eqDeleteRows;
}
```

equality delete 가 없으면 `null` 이 아니라 **`t -> true`** 를 돌려준다.
`eqDeleteFilter == null` 로 걸었다면 빠른 경로는 **영원히 안 탔을 것이고**, 패치는
"정확하지만 효과 0" 으로 측정됐을 것이다. 실제로 코드를 빌드해서 돌려보지 않았으면
못 잡았을 종류의 오류다.

실제 게이트는 `!deletes.hasEqDeletes()` 를 쓴다.

### 구현 메모

- `RoaringPositionBitmap.forEachInRange` 는 64비트 position 을 (32비트 키, 32비트 위치)로
  쪼개는 구조를 그대로 따른다. `length` 가 `int` 라서 구간이 걸칠 수 있는 키는 최대 2개다
  (한 키가 덮는 position 수 2³²가 `Integer.MAX_VALUE` 보다 크므로).
- `ColumnarBatchUtil` 의 `RowIdMappingBuilder` 는 삭제 position 을 오름차순으로 받으며
  그 사이 구간을 살아있는 행으로 채운다. 한 번의 순회로 끝난다.
- `incrementDeleteCount()` 는 삭제 행 수만큼 호출한다 (기존 동작 유지).
- equality delete 가 있으면 기존 행별 루프를 그대로 탄다. `eqDeleteFilter.test(row)` 가
  행을 필요로 하고 `ColumnarBatchRow.rowId` 가 그 루프 안에서 진행되기 때문이다.

## 테스트

### 기존 테스트를 왜 고쳐야 했나

`TestColumnarBatchUtil` 은 `PositionDeleteIndex` 를 Mockito 로 목킹하고 `isDeleted` 만 스텁한다.
패치 후 position-only 경로는 `forEachInRange` 를 호출하는데, 목 객체의 default 메서드는
아무 일도 하지 않으므로 "삭제 없음" 으로 보인다.

목을 **실제 인덱스**(`Deletes.toPositionIndex`)로 바꿨다. 실제 Roaring 비트맵을 태우므로
더 나은 테스트이기도 하다.

또 equality delete 가 섞인 테스트는 `hasEqDeletes()` 를 스텁해야 한다. 기존 테스트는
`hasPosDeletes()` 를 스텁하는데 `ColumnarBatchUtil` 은 그걸 보지 않는다.

### 결과

```
:iceberg-core:test --tests "org.apache.iceberg.deletes.*"
  TestBitmapPositionDeleteIndex            9   ✅
  TestEqualityFilter                       4   ✅
  TestPositionDeleteIndexForEachInRange   12   ✅  (신규)
  TestPositionFilter                       4   ✅
  TestRoaringPositionBitmap               26   ✅
                                          55   실패 0

:iceberg-spark:iceberg-spark-4.0_2.13:test --tests "...TestColumnarBatchUtil"
                                          17   실패 0  (기존 12 + 신규 5)
```

신규 `TestPositionDeleteIndexForEachInRange` 가 검증하는 것:

- 빈 인덱스 / 길이 0 / 음수 길이
- 구간 경계 (포함–배타)
- 오름차순 보장
- Roaring 청크(65,536) 경계를 걸치는 구간
- 32비트 키 경계(2³²)를 걸치는 구간
- 할당되지 않은 키 너머의 구간
- run 컨테이너 (`runLengthEncode` 후)
- **무작위 500개 구간 × 2 (array/bitmap, run) 를 `isDeleted` 행별 스캔 결과와 대조** —
  비트맵 구현과 default 구현 양쪽 모두

신규 `TestColumnarBatchUtil` 케이스:

- 배치 시작 position 이 0 이 아닌 경우 (첫 배치 이후 전부가 이 경우다)
- 삭제가 배치 구간 밖에만 있는 경우
- position 만으로 전 행이 삭제되는 경우
- **무작위 인덱스 × 60개 배치를 행별 프로브 결과와 대조**, 삭제 카운터 호출 횟수까지 검증

## 빌드

```bash
cd ~/src/iceberg
./gradlew -DsparkVersions=4.0 -DscalaVersion=2.13 -DflinkVersions= -DkafkaVersions= \
  :iceberg-spark:iceberg-spark-runtime-4.0_2.13:shadowJar
```

베이스라인 jar 은 **같은 트리에서 패치만 stash 하고 다시 빌드**했다.
Maven 에서 받은 릴리스 jar 과 비교하면 빌드 환경 차이(JDK 패치 레벨, 셰이딩 순서,
의존성 해석)가 패치 효과로 둔갑할 수 있다. 두 jar 의 유일한 차이가 우리 diff 여야 한다.

```
~/opt/jars-baseline/iceberg-spark-runtime-4.0_2.13-1.11.0.jar   47,926,314 B
~/opt/jars-patched/iceberg-spark-runtime-4.0_2.13-1.11.0.jar    47,929,417 B
```

바이트 수준 확인 (`phase2/scripts/00-install-jar.sh`):

```
baseline  PositionDeleteIndex.forEachInRange        없음
patched   PositionDeleteIndex.forEachInRange        있음
baseline  ColumnarBatchUtil.class                   1개
patched   ColumnarBatchUtil{,$RowIdMappingBuilder,$IsDeletedBuilder}.class  3개
```

## 측정

`phase2/` 참조. 결과는 [`docs/findings.md`](../docs/findings.md) F-011.

## 재현

```bash
git clone --depth 1 --branch apache-iceberg-1.11.0 https://github.com/apache/iceberg.git ~/src/iceberg
cd upstream && python3 apply_patch.py ~/src/iceberg && python3 patch_tests.py ~/src/iceberg
# 또는:  cd ~/src/iceberg && git apply /path/to/upstream/range-scan.patch
```

## 제출 상태

**미제출.** `issue-columnarbatchutil.md` 는 초안이며 아직 apache/iceberg 에 올리지 않았다.
측정 결과를 반영해 갱신한 뒤 제출 여부를 판단한다.
