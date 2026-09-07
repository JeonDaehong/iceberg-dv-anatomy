# Phase 0 — Go/No-Go 게이트

> **목적: 이 주제에 4주를 쓸 가치가 있는지 2~3일 안에 판정한다.**

## 왜 이게 먼저인가

원안은 "플레임그래프로 실제 스캔에서의 비중 확인"을 Phase 1의 **마지막 단계**에 두었습니다.
문제는 암달의 법칙입니다.

> `contains()`가 마이크로벤치에서 5배 차이 나도, 전체 스캔 CPU의 1%밖에 안 차지하면
> 그 5배를 완전히 없애도 쿼리는 **0.8%** 빨라집니다.

3주를 다 쓰고 나서 "사실 의미 없었다"를 알게 되는 게 최악이라 순서를 뒤집었습니다.

## 무엇을 재는가

**단 하나의 숫자**: 스캔 중 전체 CPU 샘플에서 아래 프레임이 스택에 포함된 비율.

- `org.apache.iceberg.spark.data.vectorized.ColumnarBatchUtil` — 배치당 행 단위 `isDeleted` 루프
- `org.apache.iceberg.deletes.*` — `PositionDeleteIndex` / `RoaringPositionBitmap`
- `org.roaringbitmap.*` — Roaring 내부 (`contains`, `RoaringArray.binarySearch`, 컨테이너)

## 판정 기준

| DV 비중 | 판정 | 조치 |
|---|---|---|
| **≥ 5%** | 🟢 GO | 원안대로 전면 진행. 헤드라인 = "스캔 CPU의 X%를 차지하는 DV 체크" |
| **1 ~ 5%** | 🟡 PIVOT | 진행하되 무게중심 이동. 마이크로벤치는 유지, 헤드라인은 [§7.1 `ColumnarBatchUtil` 개선](../README.md#71--columnarbatchutil-배치-루프-최적화--성공-확률-최고)과 레이아웃 처방으로 |
| **< 1%** | 🔴 STOP | 방향 전환. "DV 읽기 비용은 무시 가능하다"를 짧은 글로 내고, 병목이 실제로 있는 곳(DV 파일 I/O, planning, Puffin 파싱)으로 이동 |

**STOP도 실패가 아닙니다.** "무시 가능하다 — 근거는 이것"은 지금 웹에 없는 결과이고, 그 자체로 공개할 가치가 있습니다.

## 실행

```bash
cd phase0
./scripts/run-all.sh
```

개별 실행:

```bash
./scripts/00-check-env.sh      # 환경 점검 (여기서 실패하는 건 뒤에서 더 비싸게 실패함)
./scripts/01-gen-tables.sh     # V3 테이블 3종 + DV 생성
./scripts/02-profile-scans.sh  # 패턴 x 컬럼폭 = 6회 프로파일링
./scripts/03-attribute.sh      # 귀속 + 게이트 판정
```

설정은 `config.env`에서 조정합니다. 값을 바꾸면 `results/`를 비우고 다시 돌리세요.

## 실험 설계

### 조건 3종

| 패턴 | 삭제 | 기대 컨테이너 | 역할 |
|---|---|---|---|
| `none` | 없음 | — | **대조군.** DV 없는 테이블에서 분류기가 오탐하지 않는지 검증 + wall-clock 기준선 |
| `sparse` | `d=0.5%`, `C=1` (해시 분산) | **array** | 이진 탐색 경로. 실무 CDC에 가장 가까움 |
| `run` | 파일당 position 35%~65% 연속 | **run** | RLE 경로 |

> Phase 0은 양 극단 + 대조군만 봅니다. 밀도를 6.25% 경계 근처로 촘촘히 스윕하는 건
> [Phase 1의 (d, C, p) 격자](../README.md#41-️-설계-결함-삭제-패턴-abc는-독립변수가-아니다)에서 합니다.

### 컬럼 폭 2종

DV 체크 비용은 **컬럼 수와 무관하게 행당 1회**입니다. 따라서 비중은 컬럼 수에 반비례합니다.

- `narrow` (1컬럼) — DV 비중의 **상한**
- `wide` (20컬럼) — DV 비중의 **하한**

게이트 판정은 **상한(narrow) 기준**입니다. 상한조차 낮으면 주제가 성립하지 않기 때문입니다.
반대로 상한만 높고 하한이 0에 가까우면, 결론은 "1컬럼 스캔에서만 의미 있다"가 되어야 합니다.

### 핵심 불변량

```
파일 i 의 row position p  ↔  id = i * ROWS_PER_FILE + p     (즉 position = id % ROWS_PER_FILE)
```

`spark.range(0, N, 1, NUM_FILES)`가 파티션당 연속 id를 담고, `write.distribution-mode=none`이
셔플 없이 파티션당 파일 1개를 쓰기 때문에 성립합니다.
**이게 깨지면 "id 조건으로 삭제" ≠ "position 패턴 제어"가 되어 모든 분석이 무의미해집니다.**
`gen_table.py`가 쓰기 직후 `assert_layout()`으로 검증하고, 위반 시 exit 2로 중단합니다.

### 편향 통제

의도적으로 넣은 설계이니 바꾸지 마세요.

| 통제 | 이유 |
|---|---|
| **md5 기반 고엔트로피 데이터** | 저엔트로피면 Parquet 디코드가 비현실적으로 싸져서 DV 비중이 **과대**평가됨 → 게이트 오판 |
| **`count(*)` 대신 noop 싱크** | `count(*)`는 메타데이터로 최적화되어 실제 물질화 비용이 안 잡힘 |
| **JVM 기동이 분모에 포함** | DV 비중을 **과소**평가하는 방향. 게이트에서는 보수적인 쪽이 안전 |
| **`none` 대조군** | 분류기 오탐 검출. 여기서 DV 샘플이 0.2% 넘게 잡히면 나머지 결과에서 빼고 해석 |
| **폭마다 별도 JVM** | 한 프로파일에 섞이면 narrow/wide 분리가 불가능 |
| **AQE 비활성 + 고정 코어 수** | 파일 1:1 불변량 보호 + 실행 결정론 |

## 출력물

```
results/
├── env.txt              # 버전/호스트/설정 (재현성)
├── gen_<pattern>.json   # 파일 레이아웃, DV 카디널리티/바이트
├── scan_<label>.json    # 반복별 스캔 시간
├── profiles/*.collapsed # async-profiler 원시 스택
├── attribution.txt      # ★ 게이트 판정
└── attribution.json
```

## 결과 읽는 법

```
  DV 체크 (union)                    12,043     3.21%
       └ 스캔 서브트리 대비:                      4.87%
```

- **`DV/전체`** — 게이트 판정 기준. JVM 기동/JIT/GC가 분모에 섞인 보수적 수치.
- **`DV/스캔`** — 리더 내부에서의 비중. 참고치.
- **그룹별 수치는 합산 금지.** `ColumnarBatchUtil` → `deletes` → `roaringbitmap`이
  같은 스택에서 중첩되므로 inclusive 집계가 겹칩니다. `union` 값을 쓰세요.

### 이런 경고가 뜨면

| 경고 | 의미 | 대응 |
|---|---|---|
| `행 기반 삭제 경로 샘플 감지` | 벡터화 리더가 꺼짐 → `ColumnarBatchUtil` 대신 `DeleteFilter` 경로 | 측정 대상이 다릅니다. `scan.py`의 벡터화 경고와 실행 계획을 확인 |
| `대조군에서 DV 샘플 검출` | 분류기 오탐 또는 잔여 delete 파일 | 그 값만큼 baseline noise로 빼고 해석 |
| `프로파일이 비었습니다` | agent 미부착 또는 비정상 종료 | `--driver-java-options` 전달 여부, `AP_LIB` 경로 확인 |
| `레이아웃 불변량 위반` | 파일 1:1 매핑 깨짐 | 삭제 패턴 제어가 무의미. `write.distribution-mode`, AQE, target-file-size 확인 |

## 알려진 한계

- `ctimer` 이벤트로 CPU 시간 기반 샘플링을 씁니다. 상대 비중 귀속에는 이걸로 충분합니다.
- ~~**WSL2/가상화 환경에서는 하드웨어 PMU가 없습니다.**~~ → **있습니다** (F-028). WSL2 가 Hyper-V vPMU 를 노출해서 `branch-misses` 같은 마이크로아키텍처 지표를 로컬에서 잴 수 있습니다. `.metal` 인스턴스는 필요하지 않았습니다. 확인: `perf stat -e branch-misses sleep 1`
- 이 수치는 **"스캔 CPU 안에서의 비중"**이지 "쿼리 시간 단축 가능폭"이 아닙니다. DV 체크를 0으로 만들어도 절감폭은 이 비중을 넘지 못합니다.
- Spark `local` 모드는 단일 JVM이라 executor 격리가 없습니다. 절대 성능이 아니라 **비중**을 보는 용도로만 유효합니다.

## 다음 단계

판정 결과와 근거를 `docs/findings.md`에 기록한 뒤:

- **GO** → Phase 1. `tools/dv-inspect` (Puffin → 컨테이너 타입 덤퍼)부터 작성
- **PIVOT** → Phase 1 축소 + [§7.1](../README.md#71--columnarbatchutil-배치-루프-최적화--성공-확률-최고) 프로토타입 착수
- **STOP** → [§5.1](../README.md#51-phase-0--gono-go-게이트-23일-)의 방향 전환
