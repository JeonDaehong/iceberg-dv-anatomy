#!/usr/bin/env bash
# 스토리지 축 — 오브젝트 스토리지에서 DV 비중이 실제로 낮아지는가.
#
# 왜 이 축인가:
#   F-019 가 로컬 콜드 캐시로 "DV 비중은 스토리지에 안 흔들린다" 를 얻었는데,
#   그 결론에는 조건이 붙는다. `ctimer` 는 CPU 시간이라 **I/O 대기**가 분모에 안 들어간다.
#   로컬 콜드 읽기는 페이지 폴트와 DMA 라 CPU 를 거의 안 쓰므로 비중이 안 변한 것이다.
#
#   S3 는 다르다. HTTP 파싱, TLS 복호화, 체크섬 검증이 전부 **사용자 스레드의 CPU** 이고,
#   그건 ctimer 분모에 그대로 들어간다. 즉 S3 는 로컬 콜드와 달리 비중을 진짜로 낮출 수 있고,
#   **로컬에서는 이 축을 흉내낼 수 없다.** F-019 가 열어둔 채로 남긴 유일한 축이다.
#
# 무엇을 통제하는가:
#   같은 인스턴스, 같은 세션, 같은 jar, 같은 바이트(S3 의 warehouse 를 EBS 로 받아 쓴다).
#   반복 횟수도 두 스토리지가 같다 — S3 만 적게 돌리면 그게 곧 교란이다.
#   => 바뀌는 것은 **읽기 경로뿐**이다.
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   S1. [판정용] S3 에서 DV 비중이 EBS 보다 **뚜렷하게 낮아진다.** c=1 에서 10%p 이상.
#       S3 클라이언트의 CPU 가 분모에 더해지기 때문이다.
#       깨지는 방향이 둘 다 의미가 있다:
#         - 안 낮아지면 -> S3 클라이언트 CPU 가 생각보다 싸거나, 프로파일러가 그걸
#           스캔 서브트리 **밖**으로 귀속하는 것이다. 후자면 'DV 비중' 의 분모 정의를
#           다시 봐야 하고, F-019 의 결론이 오히려 더 강해진다.
#         - 낮아지면 -> "스캔 CPU 의 43~53%" 는 로컬 디스크 조건의 값이다.
#           이슈 초안과 공개 글에 스토리지 조건을 반드시 명시해야 한다.
#
#   S2. DV 체크의 **절대** 샘플 수는 EBS 와 S3 가 비슷하다 (범위가 겹친다).
#       같은 행 수에 같은 일을 하기 때문. F-019 R2 의 재확인이고, 깨지면 귀속이 틀린 것이다.
#
#   S3P. 패치의 DV 개선 배율은 두 스토리지에서 모두 유지된다 (d=6.1% 에서 6~9배).
#
#   S4. wall 단축 = 0.46 × DV 비중 이 S3 에서도 ±5%p 로 성립한다.
#       S3 는 비중을 낮추면서 wall 도 늘리므로, 두 값이 함께 움직이는 조건이다.
#
#   ⚠️ 공정성에 대한 정직한 단서: EBS 쪽은 반복하면서 페이지 캐시가 따뜻해지고 S3 는
#      그런 캐시가 없다. 이건 교란이 아니라 **오브젝트 스토리지의 성질 그 자체**이므로
#      그대로 둔다. 다만 "S3 가 느리다" 의 일부는 캐시 부재 때문이지 프로토콜 때문이 아니다.
#
#   ⚠️ 반복 횟수를 로컬 측정(30)보다 줄인다. S3 에서 20컬럼을 30회 읽으면 826MB × 30 이
#      매 프로파일마다 오간다. 두 스토리지에 **같은 횟수**를 쓰므로 비교는 성립하지만,
#      절대 샘플 수는 다른 실험과 직접 비교하면 안 된다.
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
source ./config.env
REPS="${_REPS_CALLER:-6}"

S3_BUCKET="${BUCKET:-dv-anatomy-394686422456-apne2}"
S3_WAREHOUSE="s3a://${S3_BUCKET}/warehouse-p1"
STO_TABLE="${STO_TABLE:-dv.g.d610}"
STO_TAG="${STO_TAG:-d610}"
STO_COLS="${STO_COLS:-1 20}"
# S3 왕복이 있으므로 로컬(30)보다 줄인다. 두 스토리지에 같은 값을 쓴다.
export SCAN_ITERS="${SCAN_ITERS:-8}"
export SCAN_WARMUP="${SCAN_WARMUP:-2}"

# S3A 에 필요한 jar. --packages 를 쓰면 Ivy 해석이 프로파일 대상 JVM 안에서 돌아
# 그 시간이 분모에 섞인다. 그래서 미리 받아 --jars 로 넘긴다.
S3_JARS="${S3_JARS:-}"
[[ -n "$S3_JARS" ]] || { echo "S3_JARS 가 비었다 (hadoop-aws + aws sdk bundle 경로)" >&2; exit 1; }

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }

run_one() {   # $1=arm $2=rep $3=cols $4=store(ebs|s3)
  local T="${STO_TAG}${4}c${3}"
  local PROF="${RESULTS}/profiles/${1}__${T}_c${3}_r${2}.collapsed"
  local JAR; case "$1" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;;
    *) echo "unknown arm $1" >&2; return 1 ;; esac

  local WH EXTRA=()
  if [[ "$4" == "s3" ]]; then
    WH="$S3_WAREHOUSE"
    EXTRA=(--conf spark.hadoop.fs.s3a.impl=org.apache.hadoop.fs.s3a.S3AFileSystem
           --conf spark.hadoop.fs.s3a.endpoint.region="${AWS_REGION:-ap-northeast-2}")
  else
    WH="$WAREHOUSE"
  fi

  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then echo "      skip $1 r$2"; return; fi

  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "${JAR},${S3_JARS}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    "${EXTRA[@]}" \
    ../phase0/spark/scan.py \
      --warehouse "$WH" --table "$STO_TABLE" --label "${1}_${T}_r${2}" \
      --cols "$3" --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${1}__${T}_r${2}.json" \
    2>&1 | grep -E "median=" | sed "s|^|      $1 r$2 ($4)\: |" || true
}

START=$(date +%s); N=0
echo "스토리지 축: EBS vs S3 × ${STO_COLS} 컬럼 × 2 arm × ${REPS}회"
echo "  S3 warehouse : $S3_WAREHOUSE"
echo "  EBS warehouse: $WAREHOUSE"
echo "  SCAN_ITERS=${SCAN_ITERS} SCAN_WARMUP=${SCAN_WARMUP} REPS=${REPS}"
echo

for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  # 순서는 라운드마다 뒤집는다 (F-015 의 교훈을 이 축은 처음부터 적용한다).
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for C in $STO_COLS; do
    for STORE in ebs s3; do
      echo "  ${C}컬럼 ${STORE}  [순서: ${ARMS[*]}]"
      for A in "${ARMS[@]}"; do
        run_one "$A" "$R" "$C" "$STORE"; N=$((N+1))
      done
    done
  done
  echo
done
echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
