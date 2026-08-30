# SETUP — 준비물 체크리스트

> 원칙: **Phase 0은 로컬에서 끝납니다. AWS는 Phase 2 전까지 띄우지 마세요.**
> Phase 0에서 "DV 체크 비중 < 1%"가 나오면 metal 인스턴스 자체가 필요 없어집니다.
> 예외는 **vCPU 쿼터 증설**뿐 — 승인에 며칠 걸릴 수 있어 지금 요청해야 합니다.

---

## 🔴 지금 (리드타임 있음)

### 1. AWS vCPU 쿼터 증설 요청 — 유일하게 급한 항목

`c7i.metal-24xl` = **96 vCPU**. 신규/저사용 계정의 기본 쿼터는 5~32인 경우가 많아 **요청 없이는 실행 자체가 실패**합니다.

Service Quotas 콘솔 → EC2 → 아래 두 개를 **128 이상**으로 요청:

| 쿼터 이름 | 코드 | 용도 |
|---|---|---|
| Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances | `L-1216C47A` | metal 온디맨드 폴백 |
| All Standard (A, C, D, H, I, M, R, T, Z) Spot Instance Requests | `L-34B43A08` | metal 스팟 (주 사용) |

- 리전은 **하나만** 정해서 거기에 요청 (쿼터는 리전별)
- 권장 리전: `us-east-1` 또는 `us-west-2` (c7i.metal 재고 안정적)
- `ap-northeast-2`(서울)는 c7i.metal 가용성을 먼저 확인:
  ```bash
  aws ec2 describe-instance-type-offerings --location-type availability-zone \
    --filters Name=instance-type,Values=c7i.metal-24xl --region ap-northeast-2
  ```
- 데이터는 로컬에서 생성하므로 리전 지연시간은 무관. **재고와 가격만 보고 고르세요.**

### 2. WSL2 + Ubuntu — Windows 환경의 전제조건

`perf`, `async-profiler`, `bpftrace`는 **전부 Linux 전용**입니다. Windows 네이티브로는 Phase 0도 못 합니다.

```powershell
wsl --install -d Ubuntu-24.04
```

> ⚠️ **WSL2에서 하드웨어 PMU는 안 나옵니다** (VM이라 PMU 비가상화).
> 하지만 Phase 0에 필요한 건 async-profiler의 상대 비중 attribution뿐이고, `-e ctimer`(perf 불필요) 폴백으로 충분합니다.
> 정밀 카운터(`branch-misses`, `LLC-load-misses`)는 Phase 2의 `.metal`에서만 필요합니다.

WSL2 리소스 상향 (`%UserProfile%\.wslconfig`):

```ini
[wsl2]
memory=16GB
processors=8
swap=8GB
```

### 3. AWS Budgets 알람 — 5분

`c7i.metal-24xl`을 켜둔 채 자면 **하루 $100 이상** 나갑니다.

- Billing → Budgets → 월 예산 설정, **$50 / $100 / $200** 임계 알림
- 추가 안전장치: 인스턴스에 self-terminate cron 걸기
  ```bash
  # 4시간 후 자동 종료 (인스턴스 안에서)
  sudo shutdown -h +240
  ```
  + EC2 인스턴스 속성 `Shutdown behavior = Terminate`

---

## 🟡 Phase 0 시작 전 (로컬)

> **이 절은 자동화되어 있습니다 → [INSTALL.md](../INSTALL.md) 를 따르세요.**
> `scripts/install-wsl.ps1` (관리자 1회) + `scripts/provision-wsl.sh` (자동)로 아래가 전부 처리됩니다.
> 아래 내용은 수동 설치가 필요할 때의 참고용입니다.

WSL2 Ubuntu 안에서:

### JDK 17

Spark 4.0은 Java 17+ 필수, Iceberg 1.11의 기본 빌드 타깃도 17입니다.
(Phase 3-B의 Vector API 프로토타입용 JDK 21은 나중에 추가)

```bash
sudo apt update && sudo apt install -y openjdk-17-jdk
java -version   # 17.x
```

### Spark 4.0 (Scala 2.13)

Spark 4.0은 **Scala 2.13 전용**입니다. Iceberg 아티팩트 접미사도 `_2.13`이어야 합니다.

```bash
cd ~ && curl -LO https://dlcdn.apache.org/spark/spark-4.0.4/spark-4.0.4-bin-hadoop3.tgz
tar xzf spark-4.0.4-bin-hadoop3.tgz
echo 'export SPARK_HOME=$HOME/spark-4.0.4-bin-hadoop3' >> ~/.bashrc
echo 'export PATH=$SPARK_HOME/bin:$PATH'               >> ~/.bashrc
source ~/.bashrc
```

### Iceberg 1.11.0 런타임

`--packages`로 자동 다운로드되므로 별도 설치 불필요. 좌표만 확인 (Maven Central 존재 확인 완료):

| Spark | 좌표 |
|---|---|
| 4.0 | `org.apache.iceberg:iceberg-spark-runtime-4.0_2.13:1.11.0` |
| 3.5 | `org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.11.0` |

첫 실행 시 다운로드에 몇 분 걸립니다. 오프라인 반복 실행을 원하면 미리 한 번 받아두세요:

```bash
spark-shell --packages org.apache.iceberg:iceberg-spark-runtime-4.0_2.13:1.11.0 -e ':quit'
```

### async-profiler 4.5

```bash
cd ~ && curl -LO https://github.com/async-profiler/async-profiler/releases/download/v4.5/async-profiler-4.5-linux-x64.tar.gz
tar xzf async-profiler-4.5-linux-x64.tar.gz
echo 'export AP_HOME=$HOME/async-profiler-4.5-linux-x64' >> ~/.bashrc
source ~/.bashrc
ls $AP_HOME/lib/libasyncProfiler.so $AP_HOME/bin/asprof
```

### 시스템 요건

| 항목 | 최소 | 비고 |
|---|---|---|
| RAM | 16GB | Spark local 드라이버 힙 8GB 배정 |
| 디스크 | 20GB 여유 | 기본 설정 8M행 × 20컬럼 ≈ 600MB~1GB + Ivy 캐시 |
| CPU | 4코어+ | 데이터 생성 시간에 직결 |

---

## ⚙️ 지금 결정해야 하는 설계 파라미터

**행 폭 / 파일당 행 수** — 생성기를 돌리기 전에 정해야 하고, Phase 1 격자 설계에 직결됩니다.

Roaring 청크는 **65,536 position** 단위입니다. 따라서:

```
파일당 청크 수 = 파일당 행 수 / 65,536
```

| 파일당 행 수 | 청크 수 | 평가 |
|---|---|---|
| 500,000 | 7.6 | 너무 적음 — 청크 단위 통계가 안 나옴 |
| **2,000,000** | **30.5** | **기본값. Phase 0에 충분** |
| 4,000,000 | 61 | Phase 1 격자 측정에 권장 |

Phase 0은 attribution만 보므로 기본값 2M으로 시작하고, Phase 1 진입 시 4M으로 올리세요.
`phase0/config.env`의 `ROWS_PER_FILE`로 조정합니다.

> 데이터는 md5 기반 고엔트로피로 생성합니다. **압축이 잘 되는 저엔트로피 데이터를 쓰면 Parquet 디코드가 비현실적으로 빨라져 DV 비중이 과대평가되고, go/no-go 게이트가 오판합니다.** 이건 의도된 설계입니다 — 바꾸지 마세요.

---

## 🟢 Phase 2 직전 (약 2주 뒤)

### hsdis — 여기서 반나절 날리는 사람 많습니다

JMH `-prof perfasm`에 필요한데 JDK에 기본 포함이 아닙니다.
**metal 인스턴스를 띄우기 전에 로컬에서 먼저 성공시켜 두세요.** 시간당 과금되는 머신 위에서 빌드 삽질하는 게 최악입니다.

```bash
# 확인 방법
java -XX:+UnlockDiagnosticVMOptions -XX:+PrintAssembly -version 2>&1 | head
# "Could not load hsdis" 가 안 뜨면 성공
```

### 인스턴스 설정

| 항목 | 값 | 이유 |
|---|---|---|
| AMI | Ubuntu 24.04 LTS | `linux-tools-$(uname -r)` 설치가 AL2023보다 덜 번거로움 |
| 인스턴스 | `c7i.metal-24xl` (스팟) | Sapphire Rapids → **AVX-512 지원**. §4.4 CRoaring 비교의 전제 |
| EBS | gp3 200GB, 3000 IOPS | 데이터 생성 |
| SG | inbound SSH(22) only | |

> **c7i를 고른 게 우연이 아닙니다.** Graviton(c7g)이나 AMD 계열이었으면 AVX-512 비교가 반쪽이 됩니다.

부팅 후 즉시:

```bash
sudo apt install -y linux-tools-common linux-tools-$(uname -r) cmake clang build-essential

# ✅ PMU 동작 확인 — 이게 안 되면 metal이 아니거나 설정 문제
perf stat -e cycles,instructions,branches,branch-misses,cache-misses sleep 1
#   "<not supported>" 가 하나라도 뜨면 정밀 측정 불가

# 측정 환경 고정
sudo cpupower frequency-set -g performance
sudo sh -c 'echo 0 > /proc/sys/kernel/nmi_watchdog'
sudo sh -c 'echo -1 > /proc/sys/kernel/perf_event_paranoid'
sudo sh -c 'echo never > /sys/kernel/mm/transparent_hugepage/enabled'
```

### CRoaring 빌드 (§4.4 비교용)

```bash
git clone https://github.com/RoaringBitmap/CRoaring && cd CRoaring
cmake -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS="-march=native"
cmake --build build -j
# AVX-512 무효화 버전도 별도 빌드해서 SIMD 기여분을 분리할 것
```

---

## 🔵 Phase 4 직전 (약 4주 뒤)

- S3 버킷 1개 + **IAM instance profile** (access key 하드코딩 금지)
- 버킷과 EC2를 **같은 리전**에 (전송비 + 레이턴시)
- 라이프사이클 규칙: 실험 데이터 7일 후 자동 만료
- Iceberg 카탈로그: Hadoop catalog on S3 또는 Glue catalog (둘 다 무방, Glue가 설정 덜 번거로움)

---

## 버전 고정 (재현성)

측정 결과를 공개할 때 아래를 **전부 기록**해야 합니다. `results/`에 `env.txt`로 덤프하세요.

| 항목 | 값 |
|---|---|
| Iceberg | 1.11.0 |
| RoaringBitmap (Java) | **1.6.20** (Iceberg가 핀) |
| Spark | 4.0.4 / Scala 2.13 |
| JDK | 17 (+ Phase 3-B용 21) |
| async-profiler | 4.5 |
| CRoaring | 빌드 시점 커밋 해시 기록 |
| 인스턴스 | `c7i.metal-24xl`, AZ, AMI ID |
| 시드 | 생성기 시드 고정값 |

---

## 요약 — 오늘 할 일 3개

1. ⬜ AWS vCPU 쿼터 증설 요청 (`L-1216C47A`, `L-34B43A08` → 128) — **리드타임 있음, 지금 걸어두기**
2. ⬜ WSL2 + Ubuntu 24.04 → **[INSTALL.md](../INSTALL.md) Step 1** (관리자 PowerShell 1회)
3. ⬜ AWS Budgets 알람

**EC2 인스턴스도, S3도 지금은 만들지 마세요.**
