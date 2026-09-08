#!/usr/bin/env bash
# 파일 수 축 — F-021 이 남긴 마지막 스토리지 공백.
#
# 왜 이 축인가:
#   F-021 은 S3 에서도 DV 비중이 안 흔들린다(EBS 56.2% vs S3 58.0%)를 냈는데
#   **파일 4개짜리 테이블**로만 쟀다. 실무의 MoR 테이블은 CDC 가 잦아 파일이 수백~수천 개다.
#   파일이 많으면 S3 는 파일마다 LIST/HEAD/GET 왕복이 붙는다.
#   F-021 의 한계 항목에 "수천 개면 요청 지연이 지배하는데, 그건 대기라 비중은 여전히
#   안 변하고 wall 만 나빠질 것이다. 미측정" 이라고 적어뒀다. 그 추론을 재러 왔다.
#
# 무엇을 통제하는가 — 컨테이너를 안 건드리는 것이 핵심이다:
#   파일을 쪼개면 파일 안 position 범위가 줄고, 그러면 **청크당 카디널리티**가 바뀌어
#   컨테이너 타입이 갈린다(F-004 의 4096 경계). 그러면 파일 수 축이 아니라
#   컨테이너 축을 재게 된다 — 이 프로젝트가 가장 조심해온 교란이다.
#
#   그래서 파일 하나를 **청크의 정수배** 로 맞춘다:
#     f4   : 4개   × 7,995,392행 (= 122청크)  = 31,981,568행
#     f488 : 488개 ×    65,536행 (=   1청크)  = 31,981,568행
#   행 수가 정확히 같고 청크 총수도 488로 같다. 파일 수만 122배 다르다.
#
#   ⚠️ 8,000,000 을 쓰면 안 된다 — 65,536 으로 안 나눠떨어져 파일마다 4,608행짜리
#      자투리 청크가 생기고, 그 청크는 카디널리티가 작아 컨테이너 타입이 갈린다.
#      (p 축 스크립트가 8,000,000 을 쓰는 것은 거기선 청크 '개수' 만 필요했기 때문이다.)
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   M1. [설계 검증] 두 테이블의 컨테이너 개수·합계 바이트가 10% 이내로 같고
#       타입 분포(array 우세)가 같다.
#       10% 를 준 이유: d=6.10% 는 경계 바로 아래라(청크 카디널리티 3,998 vs 4,096)
#       청크의 약 5%가 표본 흔들림으로 bitmap 이 된다(F-035 에서 실측). 두 테이블은
#       삭제 술어가 보는 id 집합이 다르므로 그 5%가 정확히 같을 수는 없다.
#       깨지면 파일 수 축이 아니라 컨테이너 축을 재는 것이므로 여기서 멈춘다.
#
#   M2. [판정용] **DV 의 스캔 내 비중이 두 파일 수에서 ±5%p 안으로 같다.**
#       근거: `ctimer` 는 CPU 시간이고 S3 요청 지연은 대기다(F-019 가 같은 이유로
#       콜드 캐시 가설을 죽였다). 파일이 많아지면 대기가 늘지 분자가 늘지 않는다.
#       깨지면 — 특히 비중이 **떨어지면** — 파일 핸들링 CPU(S3A 클라이언트, 매니페스트
#       계획, 파일별 Parquet 푸터 파싱)가 분모를 키운 것이고, F-021 의 "1.4%p" 는
#       파일이 적을 때만 맞는 값이 된다.
#
#   M3. wall-clock 은 f488 이 f4 보다 **느리다** (S3 에서 1.5배 이상).
#       파일마다 GET 왕복과 푸터 파싱이 붙기 때문. EBS 에서는 차이가 작을 것이다.
#
#   M4. 파일이 많아지면 **스캔 밖** 비중이 커진다 — 드라이버의 잡 계획(매니페스트 읽기,
#       태스크 488개 스케줄링)이 늘기 때문. 즉 DV/전체는 떨어지되 DV/스캔은 유지된다.
#       M2 와 M4 가 같이 서면 "비중을 인용할 때 분모를 밝혀야 한다" 가 다시 확인된다.
#
#   ⚠️ 이 실험이 재지 못하는 것: 수천 개 파일. 488개까지다. 그리고 단일 JVM 이라
#      태스크 스케줄링 비용이 분산 클러스터와 다르다.
#
# 사용:
#   로컬(EBS만):  phase2/scripts/20-manyfiles.sh
#   클라우드:     BUCKET=... S3_JARS=... MF_STORES="ebs s3" phase2/scripts/20-manyfiles.sh
set -euo pipefail
cd "$(dirname "$0")/.."

_REPS_CALLER="${REPS:-}"
_ITERS_CALLER="${SCAN_ITERS:-}"
source ./config.env
REPS="${_REPS_CALLER:-6}"
# S3 왕복이 있으므로 로컬 기본(30)보다 줄인다. 두 테이블에 같은 값을 쓴다.
export SCAN_ITERS="${_ITERS_CALLER:-8}"
export SCAN_WARMUP="${SCAN_WARMUP:-2}"

MF_DENSITY_BP="${MF_DENSITY_BP:-610}"
MF_COLS="${MF_COLS:-1}"
MF_STORES="${MF_STORES:-ebs}"
S3_BUCKET="${BUCKET:-dv-anatomy-394686422456-apne2}"
S3_WAREHOUSE="s3a://${S3_BUCKET}/warehouse-p1"
S3_JARS="${S3_JARS:-}"
CHUNK=65536

# tag:파일수:파일당행수 — 청크 총수가 같아야 한다 (4×122 = 488×1).
MF_LAYOUTS="${MF_LAYOUTS:-f4:4:7995392 f488:488:65536}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음" >&2; exit 1; }
if [[ "$MF_STORES" == *s3* && -z "$S3_JARS" ]]; then
  echo "S3_JARS 가 비었다 (hadoop-aws + aws sdk bundle 경로)" >&2; exit 1
fi

echo "파일 수 축  (d=${MF_DENSITY_BP}bp, ${MF_COLS}컬럼, 스토리지: ${MF_STORES})"
for L in $MF_LAYOUTS; do
  T="${L%%:*}"; REST="${L#*:}"; NF="${REST%%:*}"; RPF="${REST##*:}"
  echo "  ${T}: 파일 ${NF}개 × ${RPF}행 = $(( NF * RPF ))행,  파일당 청크 $(( RPF / CHUNK )),  청크 총 $(( NF * RPF / CHUNK ))"
done
echo "  SCAN_ITERS=${SCAN_ITERS} REPS=${REPS}"
echo

gen_one() {  # $1=tag $2=num_files $3=rows_per_file
  local TAG="mf${MF_DENSITY_BP}$1"
  local OUT="${RESULTS}/gen_${TAG}.json"
  if [[ -f "$OUT" && -d "${WAREHOUSE}/g/${TAG}" && "${FORCE:-0}" != "1" ]]; then
    echo "    skip gen ${TAG}"; return
  fi
  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    "${SPARK_ICEBERG_ARGS[@]}" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase1/spark/gen_grid.py \
      --warehouse "$WAREHOUSE" --table "dv.g.${TAG}" \
      --density-bp "$MF_DENSITY_BP" \
      --rows-per-file "$3" --num-files "$2" --seed "$SEED" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "$OUT" \
    2>&1 | grep -E "^===|삭제 |DV |데이터 파일" || true
  [[ -s "$OUT" ]] || { echo "    *** gen 결과가 없다 (${TAG})" >&2; return 1; }
}

run_one() {  # $1=arm $2=tag $3=rep $4=store
  local T="mf${MF_DENSITY_BP}${2}${4}"
  local PROF="${RESULTS}/profiles/${1}__${T}_c${MF_COLS}_r${3}.collapsed"
  local SJ="${RESULTS}/scan_${1}__${T}_r${3}.json"
  local JAR; case "$1" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;;
    *) echo "unknown arm $1" >&2; return 1 ;; esac
  [[ -s "$PROF" && -s "$SJ" && "${FORCE:-0}" != "1" ]] && { echo "      skip $1 r$3"; return; }

  local WH EXTRA=() JARS="$JAR"
  if [[ "$4" == "s3" ]]; then
    WH="$S3_WAREHOUSE"; JARS="${JAR},${S3_JARS}"
    EXTRA=(--conf spark.hadoop.fs.s3a.impl=org.apache.hadoop.fs.s3a.S3AFileSystem
           --conf spark.hadoop.fs.s3a.endpoint.region="${AWS_REGION:-ap-northeast-2}")
  else
    WH="$WAREHOUSE"
  fi

  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JARS" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    "${EXTRA[@]}" \
    ../phase0/spark/scan.py \
      --warehouse "$WH" --table "dv.g.mf${MF_DENSITY_BP}${2}" --label "${1}_${T}_r${3}" \
      --cols "$MF_COLS" --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "$SJ" \
    >/dev/null 2>&1 || true
  [[ -s "$PROF" ]] || { echo "      *** 프로파일이 비었다 ($1 ${2}/${4} r$3)" >&2; return 1; }
  [[ -s "$SJ"   ]] || { echo "      *** 스캔 JSON 이 없다 ($1 ${2}/${4} r$3)" >&2; return 1; }
  echo "      $1 ${2}/${4} r$3: ok"
}

echo "① 테이블 생성"
for L in $MF_LAYOUTS; do
  T="${L%%:*}"; REST="${L#*:}"; gen_one "$T" "${REST%%:*}" "${REST##*:}"
done

echo
echo "② 설계 검증 (M1) — 컨테이너가 같은가"
export PYTHONIOENCODING=utf-8
for L in $MF_LAYOUTS; do
  T="${L%%:*}"; TAG="mf${MF_DENSITY_BP}${T}"
  CSV="${RESULTS}/containers_${TAG}.csv"
  shopt -s nullglob
  PUFFINS=( "${WAREHOUSE}/g/${TAG}/data"/*.puffin )
  if [[ ${#PUFFINS[@]} -eq 0 ]]; then echo "    ${T}: puffin 없음"; continue; fi
  python3 ../tools/dv_inspect.py "${PUFFINS[@]}" --csv "$CSV" --limit 0 >/dev/null 2>&1 \
    || { echo "    ${T}: 파싱 실패"; continue; }
  python3 tools/container_summary.py "$CSV" "$T"
done

if [[ "$MF_STORES" == *s3* ]]; then
  echo
  echo "③ S3 업로드 (두 스토리지가 같은 바이트를 읽어야 한다)"
  for L in $MF_LAYOUTS; do
    T="${L%%:*}"; TAG="mf${MF_DENSITY_BP}${T}"
    aws s3 sync "${WAREHOUSE}/g/${TAG}/" "s3://${S3_BUCKET}/warehouse-p1/g/${TAG}/" \
      --only-show-errors || { echo "    *** 업로드 실패 ${TAG}" >&2; exit 1; }
    echo "    ${TAG} 업로드 완료"
  done
fi

echo
echo "④ 측정"
START=$(date +%s); N=0
for R in $(seq 1 "$REPS"); do
  echo "  라운드 $R / $REPS"
  # 순서는 라운드마다 뒤집는다 (F-015 의 교훈).
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for STORE in $MF_STORES; do
    for L in $MF_LAYOUTS; do
      T="${L%%:*}"
      for A in "${ARMS[@]}"; do run_one "$A" "$T" "$R" "$STORE" || true; N=$((N+1)); done
    done
  done
done

echo
echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
echo "채점: python3 tools/manyfiles.py results"
