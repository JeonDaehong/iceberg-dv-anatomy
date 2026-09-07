#!/usr/bin/env bash
# 분산 클러스터 축 — 진짜 네트워크 셔플에서도 같은가.
#
# 왜 이 축인가:
#   F-001~F-026 이 전부 단일 JVM 이다. F-022 가 local[1~16] 로 태스크 병렬도를,
#   F-024 가 로컬 셔플(디스크 경유)을 쟀지만, **네트워크 셔플과 익스큐터 스케줄링**은
#   한 번도 안 들어갔다. 이슈 초안 caveats 에 그대로 남아 있는 마지막 항목이다.
#
# 왜 EMR 이 아니라 standalone 인가:
#   EMR 최신(7.14)도 Spark 3.5.8 이다. 우리 jar 은 iceberg-spark-runtime-**4.0**_2.13 이라
#   EMR 로 가면 Spark 3.5 용 런타임을 새로 빌드해야 하고, 그러면 이 프로젝트가 모든 축에서
#   지켜온 통제 — **같은 jar(md5 동일), 같은 Spark, 같은 JDK** — 가 깨진다.
#   클러스터 효과와 버전 차이가 섞이면 C1 같은 판정을 할 수 없다.
#   그래서 Spark 4.0.4 standalone 을 직접 올린다. 바이너리가 로컬과 같다.
#
# 익스큐터 프로파일링:
#   지금까지는 --driver-java-options 로 드라이버에 붙였다. local[N] 에서는 드라이버가
#   곧 전부였기 때문이다. 클러스터에서는 일이 **익스큐터**에서 돌므로
#   spark.executor.extraJavaOptions 로 옮기고, file=...-%p.collapsed 로 익스큐터마다
#   따로 쓴 뒤 각 노드가 S3 로 sync 한다. 분석은 한 실행의 모든 익스큐터 파일을 합산한다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   C1. [판정용] DV 의 **스캔 내** 비중이 단일 JVM 과 ±10%p 안에 든다.
#       로컬 d=6.1% c=1 은 49~58% 였다. 스캔 연산자가 하는 일은 분산돼도 같기 때문이다.
#       **이게 이슈의 핵심 수치이므로 이게 살아야 한다.**
#       깨지면 "스캔 CPU 의 43~58%" 에 '단일 JVM' 조건을 박아야 하고, 이슈의 첫 문단이 바뀐다.
#
#   C2. DV 의 **전체 대비** 비중이 F-024 의 로컬 스캔 전용(14.0%)보다 낮다.
#       네트워크 셔플·직렬화·익스큐터 오버헤드가 분모에 더해지므로.
#       집계 쿼리에서는 F-024 의 2.4% 보다도 낮을 것이다.
#
#   C3. 패치의 DV 개선 배율이 6~9배를 유지한다. 패치는 DV 체크 내부만 바꾼다.
#       깨지면 귀속이 분산 실행에 오염된 것이다.
#
#   ⚠️ wall-clock 은 처음부터 기대하지 않는다. 클러스터 노이즈가 단일 인스턴스보다 크고,
#      F-024 에서 이미 '잴 수 없는 크기를 예측하는' 실수를 했다. 이 축은 **샘플로만** 본다.
#
#   ⚠️ 이 실험이 재지 '못하는' 것: 익스큐터 2개짜리 작은 클러스터다. 수십 노드에서의
#      스트래글러 분포는 다르다. 방향만 본다.
set -uo pipefail

export HOME=/root
export USER=root
export LANG=C.UTF-8
cd /root

BUCKET="${BUCKET:-dv-anatomy-394686422456-apne2}"
ROLE="${DV_ROLE:-master}"          # master | worker
MASTER_IP="${DV_MASTER_IP:-}"
TAG="${DV_TAG:-cluster-$ROLE}"
LOG=/var/log/dv-bootstrap.log
exec > >(tee -a "$LOG") 2>&1

say() { echo ""; echo "########## [$(date -u +%H:%M:%S)] $* ##########"; push; }
push() { aws s3 cp "$LOG" "s3://$BUCKET/logs/${TAG}.log" --only-show-errors 2>/dev/null || true; }
die() { echo "!!!!! 실패: $* !!!!!"; env | sort | head -20; push; sleep 1800; exit 1; }

say "0. 환경 — role=$ROLE master=$MASTER_IP $(uname -m) $(nproc)vCPU"

say "1. 패키지"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || die "apt update"
apt-get install -y -qq openjdk-17-jdk-headless python3 unzip curl git >/dev/null || die "apt install"

say "2. AWS CLI"
if ! command -v aws >/dev/null; then
  curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/a.zip || die "cli 다운로드"
  unzip -q /tmp/a.zip -d /tmp && /tmp/aws/install >/dev/null || die "cli 설치"
fi

say "3. Spark 4.0.4 + async-profiler + jar (로컬과 같은 바이너리)"
mkdir -p /root/opt && cd /root/opt
if [[ ! -d spark-4.0.4-bin-hadoop3 ]]; then
  curl -fsSL "https://dlcdn.apache.org/spark/spark-4.0.4/spark-4.0.4-bin-hadoop3.tgz" -o spark.tgz \
    || curl -fsSL "https://archive.apache.org/dist/spark/spark-4.0.4/spark-4.0.4-bin-hadoop3.tgz" -o spark.tgz \
    || die "spark 다운로드"
  tar xzf spark.tgz && rm spark.tgz
fi
if [[ ! -d async-profiler-4.5-linux-x64 ]]; then
  curl -fsSL "https://github.com/async-profiler/async-profiler/releases/download/v4.5/async-profiler-4.5-linux-x64.tar.gz" -o ap.tgz || die "ap 다운로드"
  tar xzf ap.tgz && rm ap.tgz
fi
JARNAME="iceberg-spark-runtime-4.0_2.13-1.11.0.jar"
mkdir -p /root/opt/jars-baseline /root/opt/jars-patched /root/opt/jars
aws s3 cp "s3://$BUCKET/jars/baseline.jar" "/root/opt/jars-baseline/$JARNAME" --only-show-errors || die "baseline jar"
aws s3 cp "s3://$BUCKET/jars/patched.jar"  "/root/opt/jars-patched/$JARNAME"  --only-show-errors || die "patched jar"
cp "/root/opt/jars-baseline/$JARNAME" "/root/opt/jars/$JARNAME"
[[ "$(md5sum /root/opt/jars-baseline/$JARNAME | cut -d' ' -f1)" == aff77e2376f83e4f98d2963b05f68c09 ]] || die "baseline md5 불일치"
[[ "$(md5sum /root/opt/jars-patched/$JARNAME  | cut -d' ' -f1)" == 8a4922784a79a2d0323a0530ace81240 ]] || die "patched md5 불일치"
echo "  md5 검증 통과 — 로컬에서 빌드한 바이너리와 동일"

export SPARK_HOME=/root/opt/spark-4.0.4-bin-hadoop3
export AP_HOME=/root/opt/async-profiler-4.5-linux-x64
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export PATH="$SPARK_HOME/bin:$SPARK_HOME/sbin:$JAVA_HOME/bin:$PATH"
sysctl -w kernel.perf_event_paranoid=1 >/dev/null 2>&1 || true

say "3b. S3A jar 을 SPARK_HOME/jars 에 직접 넣는다"
# ⚠️ --jars 로 넘기면 안 된다. Hadoop 의 FileSystem.get() 은 s3a:// 를 만나면
# 시스템 클래스로더로 S3AFileSystem 을 찾는데, --jars 는 Spark 의 user 클래스로더에
# 들어간다. local 모드에서는 같은 JVM 이라 우연히 되지만, standalone client 모드에서는
# 드라이버가 **플랜 수립 중** S3 를 읽을 때 클래스를 못 찾고 죽는다.
# 실제로 첫 시도에서 24회 전부 이렇게 죽었다 (실행당 13.5초, median 한 줄도 못 찍음).
if ! ls "$SPARK_HOME"/jars/hadoop-aws-*.jar >/dev/null 2>&1; then
  HV=$(ls "$SPARK_HOME"/jars/hadoop-client-api-*.jar | head -1 | sed 's|.*hadoop-client-api-||; s|[.]jar$||')
  echo "  Spark 번들 hadoop 버전: $HV"
  printf '%s\n' 'from pyspark.sql import SparkSession' 'SparkSession.builder.getOrCreate().stop()' > /root/noop.py
  "$SPARK_HOME/bin/spark-submit" --master "local[1]" \
    --packages "org.apache.hadoop:hadoop-aws:${HV}" \
    --conf spark.jars.ivy=/root/ivy /root/noop.py > /root/ivy.log 2>&1 \
    || { tail -20 /root/ivy.log; die "hadoop-aws 해석 실패"; }
  cp /root/ivy/jars/*.jar "$SPARK_HOME/jars/" || die "S3A jar 복사 실패"
  echo "  복사한 jar: $(ls /root/ivy/jars/*.jar | wc -l)개 -> SPARK_HOME/jars"
fi
ls "$SPARK_HOME"/jars/ | grep -E "hadoop-aws|bundle" | head -3

say "4. 프로파일 수집 루프 — 모든 노드가 /mnt/prof 를 S3 로 sync"
mkdir -p /mnt/prof
HN=$(hostname)
nohup bash -c "while true; do aws s3 sync /mnt/prof s3://$BUCKET/clusterprof/$HN/ --only-show-errors 2>/dev/null; sleep 20; done" \
  > /dev/null 2>&1 &
echo "  sync 루프 시작 (호스트 $HN)"

say "5. Spark standalone 기동 — role=$ROLE"
mkdir -p "$SPARK_HOME/logs"
if [[ "$ROLE" == "master" ]]; then
  SELF=$(hostname -I | awk '{print $1}')
  SPARK_MASTER_HOST="$SELF" "$SPARK_HOME/sbin/start-master.sh" || die "master 기동"
  sleep 5
  echo "  master: spark://$SELF:7077"
  echo "$SELF" > /root/master_ip
  aws s3 cp /root/master_ip "s3://$BUCKET/cluster/master_ip" --only-show-errors || true
  # 워커가 붙을 때까지 기다린다
  say "6. 워커 대기 (최대 10분)"
  for i in $(seq 1 60); do
    N=$(curl -s "http://$SELF:8080/json/" 2>/dev/null | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('workers',[])))" 2>/dev/null || echo 0)
    echo "  워커 $N 개"
    [[ "$N" -ge "${DV_EXPECT_WORKERS:-2}" ]] && break
    sleep 10
  done
  [[ "$N" -ge "${DV_EXPECT_WORKERS:-2}" ]] || die "워커가 안 붙었다 ($N 개)"
  echo "READY" > /root/cluster_ready
else
  [[ -n "$MASTER_IP" ]] || die "MASTER_IP 가 비었다"
  for i in $(seq 1 30); do
    curl -s --max-time 3 "http://$MASTER_IP:8080/" >/dev/null 2>&1 && break
    echo "  master 대기 $i"; sleep 10
  done
  "$SPARK_HOME/sbin/start-worker.sh" "spark://$MASTER_IP:7077" || die "worker 기동"
  sleep 3
  echo "  worker 붙음 -> spark://$MASTER_IP:7077"
  say "9. 워커 역할 끝 — sync 루프만 남기고 대기"
  # 워커는 여기서 끝. 프로파일 sync 루프가 계속 돈다.
  sleep infinity
fi

say "7. 저장소 + 테이블"
cd /root
[[ -d iceberg-dv-anatomy ]] || git clone -q https://github.com/JeonDaehong/iceberg-dv-anatomy.git || die "clone"
find /root/iceberg-dv-anatomy -name '*.sh' -exec chmod +x {} +
export DV_WAREHOUSE=/root/dv-anatomy/warehouse
mkdir -p /root/dv-anatomy
cd /root/iceberg-dv-anatomy/phase1
DENSITIES_BP="610" ./scripts/01-gen-grid.sh || die "테이블 생성"
D=/root/dv-anatomy/warehouse-p1/g/d610
[[ -d "$D" ]] || die "테이블 없음"
NP=$(find "$D" -name '*.parquet' | wc -l)
echo "  d610 parquet ${NP}개"
[[ "$NP" -ge 4 ]] || die "parquet 이 ${NP}개뿐"
say "8. warehouse 를 S3 로 (워커도 읽어야 한다)"
aws s3 sync /root/dv-anatomy/warehouse-p1/ "s3://$BUCKET/warehouse-p1/" --only-show-errors || die "warehouse 업로드"
echo "READY" > /root/cluster_ready
say "9. 마스터 준비 완료 — 측정 스크립트를 기다린다"
push
sleep infinity
