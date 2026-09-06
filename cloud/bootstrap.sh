#!/usr/bin/env bash
# EC2 에서 같은 측정을 돌린다 — "이게 클라우드에서도 같은가" 를 닫기 위해.
#
# 왜 이 축인가:
#   F-001 ~ F-019 는 전부 한 대에서 나왔다. WSL2 / Zen 3 / 로컬 NVMe / local[4] / 웜 캐시.
#   현업에게 쓸모 있으려면 어디까지가 옮겨가는 주장인지 갈라야 하는데, 지금은 갈라놓기만
#   했지 재보지 않았다. F-019 가 "페이지 캐시" 축을 닫았지만 "CPU" 와 "오브젝트 스토리지"
#   축은 열려 있다.
#
# 무엇을 통제하는가:
#   - jar 두 개는 **로컬에서 빌드한 바이너리를 그대로 S3 로 올려서** 쓴다.
#     클라우드에서 다시 빌드하면 빌드 환경 차이가 CPU 효과로 둔갑한다.
#     md5: baseline aff77e23... / patched 8a492278...
#   - 테이블도 한 번만 만들어 S3 에 올리고 두 인스턴스가 **같은 바이트**를 읽는다.
#   - OS 는 Ubuntu 24.04 로 로컬과 맞춘다. Spark 4.0.4 / JDK 17 / async-profiler 4.5 동일.
#   - local[4] · DRIVER_MEM 8g · SCAN_ITERS 30 · REPS 6 전부 로컬과 동일.
#   => 바뀌는 것은 오직 **CPU 마이크로아키텍처**(그리고 2단계에서 스토리지)뿐이다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   P1. [판정용] DV 비중이 로컬과 ±10%p 안에서 일치한다.
#       로컬 d=6.1% c=1 은 49.4~51.2%, c=20 은 4.2~4.8% 였다.
#       근거: 비중은 '비트맵 탐색 CPU' 대 'Parquet 디코딩 CPU' 의 비이고, 둘 다
#       정수·메모리 작업이라 마이크로아키텍처가 바뀌어도 비가 크게 안 변할 것이다.
#       깨지면: 비중은 CPU 의 함수다 -> "스캔 CPU 의 43~53%" 를 환경 독립 수치로
#       인용하면 안 되고, 이슈 초안에 CPU 조건을 명시해야 한다.
#
#   P2. 패치의 DV 개선 배율이 6~9배 범위를 유지한다 (d=0.5%, d=6.1%, c=1).
#       패치는 DV 체크 내부만 바꾸므로 CPU 가 바뀌어도 배율은 유지돼야 한다.
#       깨지면 기전 설명이 틀린 것이다.
#
#   P3. wall-clock 노이즈 바닥이 로컬(9.7%)보다 **나쁘다**. 15% 이상으로 본다.
#       공유 하드웨어라 이웃 간섭이 있기 때문.
#       깨지면 좋은 소식이다 — 전용 인스턴스가 WSL2 보다 조용하다는 뜻이고,
#       그러면 로컬 측정의 노이즈 원인이 클라우드가 아니라 WSL2 였다는 증거가 된다.
#
#   P4. F-018 의 `wall 단축 = 0.46 × DV 비중` 이 ±5%p 로 성립한다.
#       F-019 의 콜드는 비중이 안 움직여서 약한 시험이었다. 여기서는 CPU 가 바뀌므로
#       비중과 wall 이 함께 움직인다 — **처음으로 제대로 된 out-of-sample 시험이다.**
#       깨지면 0.46 은 이 머신의 상수이고, 손익분기 21% 도 이 머신의 값이다.
#
#   ⚠️ 이 실험이 분리하지 못하는 것: 이 스크립트의 1단계는 CPU 축만 본다.
#      스토리지는 로컬 EBS gp3 다. S3 축은 2단계에서 따로 잰다.
#
# ⚠️ 안전: user-data 가 부팅 직후 `shutdown -h +240` 을 건다. 인스턴스는
#    instance-initiated-shutdown-behavior=terminate 로 뜨므로, 이 스크립트가 죽든
#    세션이 끊기든 4시간 뒤 스스로 사라진다.
set -uo pipefail

# cloud-init 의 user-data 환경에는 HOME/USER/LANG 이 없다. config.env 는 $HOME 으로
# 도구와 warehouse 경로를 잡고 스크립트들은 set -u 라, 그대로 두면
# "HOME: unbound variable" 로 죽는다 — 실제로 두 번째 인스턴스가 여기서 죽었다.
export HOME=/root
export USER=root
export LANG=C.UTF-8
cd /root

BUCKET="${BUCKET:-dv-anatomy-394686422456-apne2}"
TAG="${DV_TAG:-unknown}"
LOG=/var/log/dv-bootstrap.log
MARK=/root/dv-stage

exec > >(tee -a "$LOG") 2>&1

say() { echo ""; echo "########## [$(date -u +%H:%M:%S)] $* ##########"; echo "$*" > "$MARK"; push; }
push() { aws s3 cp "$LOG" "s3://$BUCKET/logs/${TAG}.log" --only-show-errors 2>/dev/null || true; }
# 실패 시 바로 종료하면 로그 밖의 상태를 볼 수 없다. KEEP_ALIVE_MIN 만큼 살려두고
# 그 사이에 SSM 으로 들어가 확인한다. 어차피 user-data 가 건 shutdown -h +240 이
# 최종 안전망이므로 방치될 위험은 없다.
KEEP_ALIVE_MIN="${KEEP_ALIVE_MIN:-30}"
die() {
  echo "!!!!! 실패: $* !!!!!"
  echo "FAILED: $*" > "$MARK"
  echo "--- 진단용 환경 ---"; env | sort | head -30; echo "--- df ---"; df -h /
  push
  echo "인스턴스를 ${KEEP_ALIVE_MIN}분간 살려둔다 (SSM 조사용). 그 뒤 종료."
  sleep $(( KEEP_ALIVE_MIN * 60 ))
  push
  exit 1
}

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)  AP_ARCH=x64;   CLI_ARCH=x86_64;  JDK_ARCH=amd64 ;;
  aarch64) AP_ARCH=arm64; CLI_ARCH=aarch64; JDK_ARCH=arm64 ;;
  *) die "모르는 아키텍처: $ARCH" ;;
esac

say "0. 환경 — arch=$ARCH tag=$TAG"
echo "HOME=$HOME USER=$USER PWD=$PWD"; nproc; free -g | head -2; lscpu | grep -E "Model name|Vendor ID|BogoMIPS|Flags" | head -3
df -h / | tail -1

say "1. 패키지"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || die "apt update"
apt-get install -y -qq openjdk-17-jdk-headless python3 unzip curl git bc >/dev/null || die "apt install"
java -version 2>&1 | head -1

say "2. AWS CLI v2"
if ! command -v aws >/dev/null; then
  curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-${CLI_ARCH}.zip" -o /tmp/awscliv2.zip || die "cli 다운로드"
  unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install >/dev/null || die "cli 설치"
fi
aws --version

say "3. Spark 4.0.4"
mkdir -p /root/opt && cd /root/opt
if [[ ! -d spark-4.0.4-bin-hadoop3 ]]; then
  for U in "https://dlcdn.apache.org/spark/spark-4.0.4/spark-4.0.4-bin-hadoop3.tgz" \
           "https://archive.apache.org/dist/spark/spark-4.0.4/spark-4.0.4-bin-hadoop3.tgz"; do
    echo "  시도: $U"
    curl -fsSL "$U" -o spark.tgz && break
  done
  [[ -s spark.tgz ]] || die "spark 다운로드"
  tar xzf spark.tgz && rm spark.tgz
fi
ls -d /root/opt/spark-4.0.4-bin-hadoop3 || die "spark 없음"

say "4. async-profiler 4.5 ($AP_ARCH)"
cd /root/opt
if [[ ! -d "async-profiler-4.5-linux-${AP_ARCH}" ]]; then
  curl -fsSL "https://github.com/async-profiler/async-profiler/releases/download/v4.5/async-profiler-4.5-linux-${AP_ARCH}.tar.gz" \
    -o ap.tgz || die "async-profiler 다운로드"
  tar xzf ap.tgz && rm ap.tgz
fi
AP_HOME="/root/opt/async-profiler-4.5-linux-${AP_ARCH}"
[[ -f "$AP_HOME/lib/libasyncProfiler.so" ]] || die "libasyncProfiler.so 없음"
# ctimer 는 perf 이벤트가 아니라 타이머라 perf_event_paranoid 와 무관하지만,
# 스택 워킹을 위해 낮춰둔다. 로컬(WSL2)과 조건을 맞추는 쪽이 안전하다.
sysctl -w kernel.perf_event_paranoid=1 >/dev/null 2>&1 || true
sysctl -w kernel.kptr_restrict=0 >/dev/null 2>&1 || true

say "5. 저장소 + jar (로컬에서 빌드한 바이너리를 그대로 받는다)"
cd /root
[[ -d iceberg-dv-anatomy ]] || git clone -q https://github.com/JeonDaehong/iceberg-dv-anatomy.git || die "clone"
# 실행 비트 방어. 저장소는 Windows 에서 작성돼 한때 전부 100644 였고, 로컬 WSL 은
# /mnt/d(DrvFs)가 모든 파일을 0777 로 보고해서 이 문제가 안 보였다. 리눅스에서
# 클론하면 'Permission denied' 로 죽는다 — 실제로 첫 인스턴스가 여기서 죽었다.
find /root/iceberg-dv-anatomy -name '*.sh' -exec chmod +x {} +
mkdir -p /root/opt/jars-baseline /root/opt/jars-patched
JARNAME="iceberg-spark-runtime-4.0_2.13-1.11.0.jar"
aws s3 cp "s3://$BUCKET/jars/baseline.jar" "/root/opt/jars-baseline/$JARNAME" --only-show-errors || die "baseline jar"
aws s3 cp "s3://$BUCKET/jars/patched.jar"  "/root/opt/jars-patched/$JARNAME"  --only-show-errors || die "patched jar"
# 테이블 생성용으로도 로컬 jar 을 둔다. 없으면 config 가 --packages 로 폴백하는데,
# 그러면 spark-submit 마다 Ivy 해석이 프로파일 대상 JVM 안에서 돌아 분모에 섞인다.
mkdir -p /root/opt/jars && cp "/root/opt/jars-baseline/$JARNAME" "/root/opt/jars/$JARNAME"
echo "  md5 확인:"; md5sum /root/opt/jars-*/"$JARNAME"

# 로컬에서 올린 것과 같은 바이너리인지 확인한다. 다르면 CPU 효과가 아니라 빌드 차이를 잰다.
EXP_B=aff77e2376f83e4f98d2963b05f68c09
EXP_P=8a4922784a79a2d0323a0530ace81240
[[ "$(md5sum /root/opt/jars-baseline/$JARNAME | cut -d' ' -f1)" == "$EXP_B" ]] || die "baseline jar md5 불일치"
[[ "$(md5sum /root/opt/jars-patched/$JARNAME  | cut -d' ' -f1)" == "$EXP_P" ]] || die "patched jar md5 불일치"

export AP_HOME
export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-${JDK_ARCH}"
export SPARK_HOME=/root/opt/spark-4.0.4-bin-hadoop3
export PATH="$SPARK_HOME/bin:$JAVA_HOME/bin:$PATH"
export DV_WAREHOUSE=/root/dv-anatomy/warehouse
[[ -d "$JAVA_HOME" ]] || die "JAVA_HOME 없음: $JAVA_HOME"

say "6. 테이블 — S3 에 있으면 받고, 없으면 만들어서 올린다"
mkdir -p /root/dv-anatomy
if aws s3 ls "s3://$BUCKET/warehouse-p1/" >/dev/null 2>&1 && \
   [[ -n "$(aws s3 ls "s3://$BUCKET/warehouse-p1/" 2>/dev/null)" ]]; then
  echo "  S3 에 있음 — 내려받는다 (두 인스턴스가 같은 바이트를 읽어야 한다)"
  aws s3 sync "s3://$BUCKET/warehouse-p1/" /root/dv-anatomy/warehouse-p1/ --only-show-errors || die "warehouse 다운로드"
else
  echo "  없음 — 생성한다 (밀도 3개만)"
  cd /root/iceberg-dv-anatomy/phase1
  DENSITIES_BP="50 610 700" ./scripts/01-gen-grid.sh || die "테이블 생성"
  echo "  S3 로 올린다"
  aws s3 sync /root/dv-anatomy/warehouse-p1/ "s3://$BUCKET/warehouse-p1/" --only-show-errors || die "warehouse 업로드"
fi
du -sh /root/dv-anatomy/warehouse-p1 || die "warehouse 없음"

say "7. 측정 — 폭 1·20, 밀도 3, arm 2, 반복 6 = 72 프로파일"
cd /root/iceberg-dv-anatomy/phase2
WIDTHS="1 20" REPS=6 FLIP_MODE=alternate ./scripts/06b-breakeven.sh || echo "  (측정이 0이 아닌 코드로 끝났다 — 결과는 아래에서 센다)"

say "8. 결과 수집"
cd /root/iceberg-dv-anatomy/phase2
N=$(ls results/profiles/*.collapsed 2>/dev/null | wc -l)
echo "  프로파일 $N 개"
[[ "$N" -ge 72 ]] || echo "  ⚠️ 72개 미만이다. 로그를 확인할 것."
python3 tools/breakeven.py results > "results/cloud_${TAG}.txt" 2>&1 || true
tail -60 "results/cloud_${TAG}.txt"

# 프로파일 원본은 크다(수십 MB). 집계 결과와 wall-clock JSON 만 올린다.
aws s3 cp "results/cloud_${TAG}.txt" "s3://$BUCKET/results/${TAG}/summary.txt" --only-show-errors || true
aws s3 cp results/breakeven.json "s3://$BUCKET/results/${TAG}/breakeven.json" --only-show-errors || true
aws s3 sync results/ "s3://$BUCKET/results/${TAG}/raw/" --exclude "profiles/*" --only-show-errors || true
# 프로파일은 압축해서 올린다 — 사후에 다시 귀속할 수 있어야 한다.
tar czf /tmp/profiles.tgz -C results profiles && \
  aws s3 cp /tmp/profiles.tgz "s3://$BUCKET/results/${TAG}/profiles.tgz" --only-show-errors || true

say "9. 완료 — lscpu 요약도 남긴다"
lscpu > /tmp/lscpu.txt; aws s3 cp /tmp/lscpu.txt "s3://$BUCKET/results/${TAG}/lscpu.txt" --only-show-errors || true
echo "DONE" > "$MARK"
push
echo "=== 부트스트랩 끝. 인스턴스는 자동 종료 타이머로 사라진다. ==="
