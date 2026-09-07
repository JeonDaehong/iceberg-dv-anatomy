#!/usr/bin/env bash
# 마스터 노드에서 실행. 분산 클러스터에서 C1~C3 을 재는 측정 루프.
#
# 단일 JVM 측정과 달라지는 것 두 가지:
#   1) --master spark://<자기IP>:7077  — 일이 익스큐터에서 돈다
#   2) 프로파일러를 spark.executor.extraJavaOptions 로 옮긴다.
#      file=...-%p.collapsed 로 익스큐터마다 따로 쓰고, 각 노드의 sync 루프가 S3 로 올린다.
#      => 분석은 한 실행의 모든 익스큐터 파일을 합산한다.
#
# 나머지는 전부 고정한다 — 같은 jar(md5 검증됨), 같은 Spark 4.0.4, 같은 JDK 17,
# 같은 테이블(S3 의 같은 바이트), 같은 SCAN_ITERS/REPS.
set -uo pipefail
export HOME=/root USER=root LANG=C.UTF-8

BUCKET="${BUCKET:-dv-anatomy-394686422456-apne2}"
SPARK_HOME=/root/opt/spark-4.0.4-bin-hadoop3
AP=/root/opt/async-profiler-4.5-linux-x64/lib/libasyncProfiler.so
JARNAME="iceberg-spark-runtime-4.0_2.13-1.11.0.jar"
BASE=/root/opt/jars-baseline/$JARNAME
PATCHED=/root/opt/jars-patched/$JARNAME
export PATH="$SPARK_HOME/bin:$PATH"
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

SELF=$(hostname -I | awk '{print $1}')
MASTER="spark://$SELF:7077"
WH="s3a://$BUCKET/warehouse-p1"
TABLE=dv.g.d610
REPS="${REPS:-6}"
ITERS="${ITERS:-10}"
WARMUP="${WARMUP:-2}"
RES=/root/clres
mkdir -p "$RES" /mnt/prof /root/logs

LOG=/var/log/dv-measure.log
exec > >(tee -a "$LOG") 2>&1
push() { aws s3 cp "$LOG" "s3://$BUCKET/logs/cl-measure.log" --only-show-errors 2>/dev/null || true; }
say() { echo ""; echo "########## [$(date -u +%H:%M:%S)] $* ##########"; push; }

say "0. S3A jar 확인 (부트스트랩이 SPARK_HOME/jars 에 넣었어야 한다)"
ls "$SPARK_HOME"/jars/ | grep -E "hadoop-aws|bundle" || { echo "!!!!! S3A jar 이 없다"; exit 1; }

say "1. 클러스터 상태 (증거로 남긴다)"
curl -s "http://$SELF:8080/json/" > "$RES/cluster-state.json" 2>/dev/null || true
curl -s "http://$SELF:8080/" > "$RES/cluster-ui.html" 2>/dev/null || true
python3 -c "
import json
d=json.load(open('$RES/cluster-state.json'))
print('  워커 %d개, 코어 %d개, 메모리 %s'%(len(d.get('workers',[])),d.get('cores',0),d.get('memory','?')))
for w in d.get('workers',[]): print('   -',w.get('host'),w.get('cores'),'코어',w.get('state'))
" 2>/dev/null || echo "  (상태 파싱 실패)"
aws s3 cp "$RES/cluster-state.json" "s3://$BUCKET/evidence/cluster-state.json" --only-show-errors || true
aws s3 cp "$RES/cluster-ui.html" "s3://$BUCKET/evidence/cluster-ui.html" --only-show-errors || true

run_one() {   # $1=arm $2=rep $3=query(scan|aggL)
  local TAG="cl$3"
  local LABEL="${1}_${TAG}_r${2}"
  local JAR; case "$1" in baseline) JAR="$BASE" ;; patched) JAR="$PATCHED" ;; esac
  local G=(); [[ "$3" == "aggL" ]] && G=(--group-col k01)
  local OUT="${RES}/scan_${1}__${TAG}_r${2}.json"
  [[ -s "$OUT" && "${FORCE:-0}" != "1" ]] && { echo "      skip $1 r$2"; return; }

  # 익스큐터마다 별도 파일. 라벨로 실행을 구분한다.
  local PROFOPT="-agentpath:${AP}=start,event=ctimer,collapsed,file=/mnt/prof/${LABEL}-%p.collapsed"

  spark-submit \
    --master "$MASTER" \
    --conf spark.executor.extraJavaOptions="$PROFOPT" \
    --conf spark.executor.cores=4 --conf spark.executor.memory=8g \
    --conf spark.sql.shuffle.partitions=16 \
    --jars "${JAR}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    --conf spark.hadoop.fs.s3a.impl=org.apache.hadoop.fs.s3a.S3AFileSystem \
    --conf spark.hadoop.fs.s3a.endpoint.region=ap-northeast-2 \
    /root/iceberg-dv-anatomy/phase0/spark/scan.py \
      --warehouse "$WH" --table "$TABLE" --label "$LABEL" \
      --spark-master "$MASTER" \
      --cols 2 --col-list k01,k04 "${G[@]}" \
      --warmup "$WARMUP" --iters "$ITERS" \
      --cores 4 --driver-mem 4g \
      --out-json "$OUT" \
    > "/root/logs/${LABEL}.out" 2>&1 || true
  grep -E "median=|master=" "/root/logs/${LABEL}.out" | sed "s|^|      $1 r$2 ($3)\: |" || true
  # 결과가 안 나왔으면 에러 꼬리를 로그에 남긴다 — 다시는 조용히 넘어가지 않게
  if [[ ! -s "$OUT" ]]; then
    echo "      !! 결과 JSON 없음 — spark-submit 마지막 15줄:"
    tail -15 "/root/logs/${LABEL}.out" | sed "s|^|        |"
  fi
}

say "1b. 게이트 — 정말 클러스터에서 도는지 먼저 확인한다"
# scan.py 가 한때 --master 를 덮어써서 클러스터가 놀고 있었다. 숫자는 그럴듯하게
# 나왔고 익스큐터만 하나도 안 떴다. 그래서 본 측정 전에 1회 돌려 확인한다.
run_one baseline 0 scan
# 게이트는 로그 줄이 아니라 **산출물**을 본다. 로그 한 줄만 보고 통과시켰다가
# 24회를 헛돌린 적이 있다 (영수증 말고 산출물을 보라 — 같은 실수를 세 번째로 반복).
if ! grep -q "master=spark://" /var/log/dv-measure.log; then
  echo "!!!!! master 가 spark:// 가 아니다 — local 로 폴백했다."
  push; exit 1
fi
if [[ ! -s "${RES}/scan_baseline__clscan_r0.json" ]]; then
  echo "!!!!! 검증 실행이 결과 JSON 을 안 냈다. 본 측정을 시작하지 않는다."
  tail -25 /root/logs/baseline_clscan_r0.out 2>/dev/null | sed "s|^|    |"
  aws s3 cp /root/logs/baseline_clscan_r0.out "s3://$BUCKET/logs/gate-fail.out" --only-show-errors || true
  push; exit 1
fi
echo "  게이트 통과 — 결과 JSON 확인:"
python3 -c "import json;d=json.load(open('${RES}/scan_baseline__clscan_r0.json'));print('    median=%.3fs iters=%d'%(d['median_s'],d['iters']))"
echo "  OK: $(grep -h "master=" /var/log/dv-measure.log | tail -1)"
sleep 25   # sync 루프가 익스큐터 프로파일을 올릴 시간
echo "  워커 프로파일 확인은 S3 clusterprof/ 에서 한다"

say "2. 측정 — 쿼리 2종 × 2 arm × ${REPS}회 (ITERS=${ITERS})"
START=$(date +%s); N=0
for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for Q in scan aggL; do
    echo "  ${Q}  [순서: ${ARMS[*]}]"
    for A in "${ARMS[@]}"; do run_one "$A" "$R" "$Q"; N=$((N+1)); done
  done
done
say "3. 완료 — ${N}회, $(( $(date +%s) - START ))초"
aws s3 sync "$RES" "s3://$BUCKET/clusterres/" --only-show-errors || true
aws s3 sync /root/logs "s3://$BUCKET/clusterlogs/" --only-show-errors || true
sleep 30   # sync 루프가 마지막 프로파일을 올릴 시간
say "4. 결과 업로드 완료"
