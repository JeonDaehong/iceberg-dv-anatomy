# INSTALL — 설치

> 이 환경(Windows 11, build 22621)에 맞춰 자동화했습니다.
> **사용자가 직접 해야 하는 건 관리자 권한 명령 하나뿐입니다.**

---

## 현재 상태

| 항목 | 상태 |
|---|---|
| `VirtualMachinePlatform` | ✅ 이미 활성 (`vmcompute`, `hvhost`, `vmms` 실행 중) |
| `Microsoft-Windows-Subsystem-Linux` | ❌ **비활성** ← 이것만 관리자 권한 필요 |
| WSL 런타임 (Store 앱) | ❌ 미설치 (winget에 `Microsoft.WSL 2.7.11` 존재) |
| Ubuntu 배포판 | ❌ 미설치 |
| Windows Java | ✅ OpenJDK 21.0.10 (참고용, 실제로는 WSL 안의 JDK 17을 씀) |
| winget | ✅ 사용 가능 |
| RAM / 디스크 | ✅ 32GB / C:286GB, D:1076GB 여유 |

**왜 관리자 권한이 필요한가**: WSL 설치를 비권한으로 시도한 결과 —

```
0x80073d28 : The package installation failed because administrator
             privileges are required.
```

Windows 옵션 기능 활성화와 시스템 범위 MSIX 설치는 우회가 불가능합니다.

---

## 이미 준비된 것 (자동 완료)

`D:\dv-tools\dl\` 에 미리 받아뒀습니다. WSL 프로비저닝이 이걸 재사용하므로 재다운로드가 없습니다.

| 파일 | 크기 | 검증 |
|---|---|---|
| `async-profiler-4.5-linux-x64.tar.gz` | 437KB | ✅ 아카이브 내용 확인 (`lib/libasyncProfiler.so`, `bin/asprof`, `bin/jfrconv`) |
| `iceberg-spark-runtime-4.0_2.13-1.11.0.jar` | 45.7MB | ✅ **SHA1 일치** (`70df6b99…716e`) |
| `spark-4.0.4-bin-hadoop3.tgz` | 524MB | ✅ **SHA512 일치** |

---

## Step 1 — 관리자 PowerShell (사용자 직접, 1회)

1. `Win` 키 → `PowerShell` 입력
2. **`Windows PowerShell` 우클릭 → "관리자 권한으로 실행"**
3. 아래 두 줄 붙여넣기:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location <이 저장소를 클론한 경로>   # 예: C:\src\iceberg-dv-anatomy
.\scripts\install-wsl.ps1
```

스크립트가 하는 일:

1. 관리자 권한 확인 (아니면 즉시 중단)
2. `VirtualMachinePlatform` + `Microsoft-Windows-Subsystem-Linux` 활성화 (멱등)
3. WSL 런타임 설치 (winget → 실패 시 `wsl --install --no-distribution` 폴백)
4. `wsl --set-default-version 2`
5. Ubuntu-24.04 설치 — **`--no-launch` + `install --root`로 무인 설치**
   (사용자명/비밀번호 대화형 입력을 건너뛰고, 이후 sudo 비밀번호 프롬프트도 없앰. 일회용 개발 환경이라 내린 판단)
6. `wsl -d Ubuntu-24.04 -- echo WSL_OK` 로 동작 검증

### ⚠️ 재부팅

Windows 옵션 기능을 새로 켜면 **재부팅이 필요합니다.** 스크립트가 이걸 감지해서

```
=====================================================
 REBOOT REQUIRED
=====================================================
```

를 출력하고 종료합니다. 그러면 재부팅 후 **같은 명령을 한 번 더** 실행하세요. 두 번째 실행에서 나머지가 이어집니다.

> `VirtualMachinePlatform`이 이미 켜져 있어서 재부팅 없이 넘어갈 가능성도 있습니다. 스크립트 출력을 보고 판단하세요.

---

## Step 2 — WSL 내부 프로비저닝 (자동)

Step 1이 끝나면 Claude Code 세션에 알려주세요. 아니면 직접:

```powershell
# <REPO> 는 이 저장소를 클론한 경로 (예: /mnt/d/work/iceberg-dv-anatomy)
wsl -d Ubuntu-24.04 -- bash <REPO>/scripts/provision-wsl.sh
```

하는 일 (전부 멱등 — 실패하면 고치고 그냥 다시 돌리면 됨):

| 단계 | 내용 |
|---|---|
| 1/6 | `openjdk-17-jdk`, `python3`, `curl` 등 APT 설치 |
| 2/6 | Spark 4.0.4 → `~/opt/` 전개 (`D:\dv-tools\dl` 재사용, 크기 검증 후) |
| 3/6 | async-profiler 4.5 → `~/opt/` 전개 |
| 4/6 | `~/.bashrc`에 `JAVA_HOME`/`SPARK_HOME`/`AP_HOME`/`DV_WAREHOUSE` 등록 |
| 5/6 | Iceberg fat jar → `~/opt/jars/` 복사 |
| 6/6 | **스모크 테스트**: Iceberg V3 테이블 생성 → DELETE → DV 생성 확인 |

스모크 테스트가 실제로 검증하는 것:

```
SMOKE live_rows=199000 (expect 199000)
SMOKE delete_files=1
SMOKE   content=... deleted=1000 bytes=...
SMOKE_RESULT=PASS
```

즉 "Spark가 뜬다"가 아니라 **"V3 테이블에서 DV가 실제로 만들어진다"** 까지 확인합니다. 여기까지 PASS면 Phase 0은 반드시 돕니다.

---

## Step 3 — Phase 0 실행

```bash
wsl -d Ubuntu-24.04
cd <REPO>/phase0
./scripts/run-all.sh
```

---

## 설치 과정에서 반영한 두 가지 설계 판단

### 1. warehouse는 반드시 Linux 네이티브 파일시스템에

WSL2에서 `/mnt/*`(Windows 드라이브)는 9p 프로토콜을 타서 I/O가 **수십 배** 느립니다.
프로젝트가 `/mnt/d/...`에 있으니 아무 생각 없이 돌리면 warehouse도 거기 생기고, 그러면
**Parquet 읽기 I/O가 프로파일 전체를 잡아먹어 DV 비중이 인위적으로 0에 수렴합니다.
게이트가 STOP으로 오판합니다.**

→ `phase0/config.env`가 `PROJECT_ROOT`가 `/mnt/*`이면 warehouse를 `$HOME/dv-anatomy/warehouse`로 자동 전환하고, `00-check-env.sh`가 `/mnt` 아래면 **FAIL** 처리합니다.
소스 코드는 `/mnt`에 둬도 무방합니다 (한 번만 읽으므로).

### 2. `--packages` 대신 로컬 fat jar (`--jars`)

`--packages`는 **매 `spark-submit`마다 Ivy 해석을 프로파일링 대상 JVM 안에서** 수행합니다.
그 시간이 통째로 분모에 섞여 DV 비중을 왜곡하고, 실행도 매번 10~30초씩 느려집니다
(Phase 0은 총 9회 submit).

→ `config.env`가 로컬 jar을 탐색해서 있으면 `--jars`, 없으면 `--packages`로 폴백합니다.
탐색 순서: `$DV_JAR_DIR` → `~/opt/jars/` → `/mnt/d/dv-tools/dl/` → `D:/dv-tools/dl/`

---

## 문제 해결

| 증상 | 원인 / 대응 |
|---|---|
| `install-wsl.ps1` 실행 안 됨 (`이 시스템에서 스크립트를 실행할 수 없으므로`) | `Set-ExecutionPolicy -Scope Process Bypass -Force`를 먼저 실행 |
| `REBOOT REQUIRED` 출력 | 재부팅 후 같은 명령 재실행 |
| `wsl -d Ubuntu-24.04` 가 응답 없음 | 재부팅 대기 중일 수 있음. 재부팅 후 `wsl -l -v` 확인 |
| `ubuntu2404.exe` 없음 | 시작 메뉴에서 `Ubuntu 24.04`를 한 번 실행해 사용자 생성 |
| 프로비저닝 중 apt 실패 | `wsl -d Ubuntu-24.04 -- apt-get update` 로 네트워크 확인 후 재실행 |
| 스모크 `SMOKE_RESULT=FAIL` | 출력 하단의 exception 확인. 대개 JDK 버전 불일치(17 필요) 또는 jar 손상 |

---

## 파일

```
scripts/
├── install-wsl.ps1      # 관리자 1회 실행 (영문 — PS 5.1 인코딩 문제 회피)
└── provision-wsl.sh     # WSL 내부 자동 설치 + 스모크 테스트
```

> `install-wsl.ps1`을 영문으로 쓴 이유: Windows PowerShell 5.1은 BOM 없는 UTF-8 스크립트를 ANSI로 읽어 한글 문자열이 깨집니다. 깨진 스크립트가 실행 중 오작동하는 것보다 영문이 안전합니다.
