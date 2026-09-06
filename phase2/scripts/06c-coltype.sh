#!/usr/bin/env bash
# 컬럼 타입을 통제한 축 — "손익분기 5컬럼" 이 정말 컬럼 수인가, 디코딩 비용인가.
#
# 왜 필요한가:
#   F-016 은 c=1,3,5,10,20 을 재서 손익분기가 c=5~10 사이라고 결론했다. 그런데
#   같은 데이터가 그 결론을 뒤집는 단서를 갖고 있었다 — 컬럼당 스캔 샘플이
#     1->3: 280,  3->5: 101,  5->10: 1,957,  10->20: 660
#   으로 균일하지 않다. ALL_COLS 의 앞 5개는 전부 정수이고 첫 문자열(md5)은
#   8번째다. 즉 c=5->10 구간의 절벽은 "컬럼이 5개 더 늘어서" 가 아니라
#   "문자열 3개가 들어와서" 일 수 있다.
#
#   지금까지의 모든 측정에서 컬럼 수와 컬럼 타입이 완전히 얽혀 있다. 폭을 넓히면
#   반드시 비싼 컬럼이 따라 들어온다. 두 축을 떼어놓지 않으면 초안의
#   "5컬럼 이하" 라는 문장이 이 스키마 밖에서는 아무 의미가 없다.
#
# 설계 — 컬럼 수와 디코딩 비용을 반대 방향으로 건다:
#   i10 : id,k01,k02,k03,k04,t11,t12,k13,k14,k15   컬럼 10개, 전부 정수 (싸다)
#   s1  : s07                                      컬럼 1개,  md5 문자열 (비싸다)
#   s3  : s07,s08,s09                              컬럼 3개,  md5 문자열 (아주 비싸다)
#
#   s3 는 F-016 이 절벽을 발견한 바로 그 세 컬럼이다.
#   두 가설이 정반대 순서를 예측한다 — 그래서 점추정이 빗나가도 판정이 된다.
#
# 모델 (F-016 실측에서 유도, d=6.1%):
#   scan(c) = DV + fixed + Σ per_col
#   DV ≈ 870,  fixed ≈ 705,  정수 ≈ 190/컬럼,  md5(32자) ≈ 3,130/컬럼
#   (fixed 와 정수 단가는 c=1 의 non-DV 895 와 1->3, 3->5 증분에서 나눠 얻었다.
#    문자열 단가는 5->10 증분 9,785 에서 double 2개를 정수 취급해 뺀 값이다.)
#
# 반증 가능한 예측 (측정 전에 적는다):
#
#   Q8.  [판정용] DV 비중의 순서가 컬럼 수 가설과 반대로 나온다:
#          비중(i10) > 비중(s3)
#        컬럼 수 가설(비중 = 870/(870+933c))은 i10=8.5% < s3=24% 를 예측한다.
#        디코딩 비용 가설은 i10=25% > s3=9.0% 를 예측한다.
#        **부호 하나로 갈린다.** 이게 이 실험의 전부다.
#
#   Q9.  [점추정] 비중 실측이 i10 ≈ 25%, s1 ≈ 18.5%, s3 ≈ 9.0% 의 ±5%p 안에 든다.
#        벗어나면 단가 모델(정수 190 / 문자열 3,130)이 틀린 것이고,
#        Q8 이 맞더라도 "얼마나 비싼가" 는 다시 재야 한다.
#
#   Q10. [wall-clock] 셋 다 노이즈 바닥(9.7%) 언저리이거나 아래다 —
#          i10 ≈ -9%, s1 ≈ -7%, s3 ≈ -3%.
#        (F-016 의 샘플→wall 보정 2.3배를 적용한 값. 즉 이 축은 wall-clock 으로는
#         판정하지 못하고 샘플로만 판정한다. 그렇게 설계했다.)
#        i10 에서 노이즈를 넘는 단축이 나오면 좋은 소식이고, 보정 2.3배가
#        과하게 잡혔다는 뜻이다 — 그건 그것대로 기록 대상이다.
#
#   Q11. [대조] DV 개선 배율은 세 구성 모두 6~9배 (d=0.5%, d=6.1%).
#        패치는 DV 체크 내부만 바꾸므로 투영과 무관해야 한다.
#        깨지면 귀속(attribution)이 틀린 것이고 F-015/F-016 전체를 다시 봐야 한다.
#
#   ⚠️ 이 실험이 분리하지 '못하는' 것: 문자열이라서 비싼지, 바이트가 많아서 비싼지.
#      s07 은 32자이고 k01 은 4바이트다. 여기서 얻는 결론은 "컬럼 수는 축이 아니다"
#      까지이고, "무엇이 축인가" 는 디코딩 비용이라는 이름만 붙일 수 있다.
#
# ⚠️ 다른 측정과 동시에 돌리지 말 것.
set -euo pipefail
cd "$(dirname "$0")/.."

# config.env 는 REPS 를 3 으로 심는다. 그 뒤에 REPS="${REPS:-6}" 를 써봐야
# 이미 값이 있으므로 6 이 절대 안 먹는다 — config.env 오버라이드 함정과 같은 모양이고
# 실제로 이 스크립트에서 한 번 밟았다. 호출자가 준 값을 source 전에 붙잡아 둔다.
_REPS_CALLER="${REPS:-}"
source ./config.env
# F-016 과 같은 조건으로 비교해야 하므로 이 축의 기본은 6 이다 (config 의 3 이 아니라).
REPS="${_REPS_CALLER:-6}"

# 세트 정의: 이름:컬럼목록
COL_SETS="${COL_SETS:-i10:id,k01,k02,k03,k04,t11,t12,k13,k14,k15 s1:s07 s3:s07,s08,s09}"
CT_TABLES="${CT_TABLES:-dv.g.d610:d610 dv.g.d50:d50 dv.g.d700:d700}"

mkdir -p "$RESULTS/profiles"
[[ -f "$AP_LIB" ]] || { echo "async-profiler 없음: $AP_LIB" >&2; exit 1; }
[[ -f "$PATCHED_JAR"  ]] || { echo "패치 jar 없음"  >&2; exit 1; }
[[ -f "$BASELINE_JAR" ]] || { echo "베이스라인 jar 없음" >&2; exit 1; }

run_one() {   # $1=table $2=tag $3=arm $4=rep $5=setname $6=collist $7=ncols
  local T="${2}${5}"
  local PROF="${RESULTS}/profiles/${3}__${T}_c${7}_r${4}.collapsed"
  local JAR; case "$3" in
    baseline) JAR="$BASELINE_JAR" ;; patched) JAR="$PATCHED_JAR" ;;
    *) echo "unknown arm $3" >&2; return 1 ;; esac

  if [[ -s "$PROF" && "${FORCE:-0}" != "1" ]]; then echo "      skip ${3} r${4}"; return; fi

  spark-submit \
    --master "local[${LOCAL_CORES}]" --driver-memory "${DRIVER_MEM}" \
    --driver-java-options "-agentpath:${AP_LIB}=start,event=${AP_EVENT},collapsed,file=${PROF}" \
    --jars "$JAR" \
    --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
    ../phase0/spark/scan.py \
      --warehouse "$WAREHOUSE" --table "$1" --label "${3}_${T}_r${4}" \
      --cols "$7" --col-list "$6" \
      --warmup "$SCAN_WARMUP" --iters "$SCAN_ITERS" \
      --cores "$LOCAL_CORES" --driver-mem "$DRIVER_MEM" \
      --out-json "${RESULTS}/scan_${3}__${T}_r${4}.json" \
    2>&1 | grep -E "median=" | sed "s/^/      ${3} r${4}: /" || true
}

N=0; START=$(date +%s)
SETS=(); for S in $COL_SETS; do SETS+=("$S"); done
CFG=();  for T in $CT_TABLES; do CFG+=("$T"); done
echo "컬럼세트 ${#SETS[@]}개 × 테이블 ${#CFG[@]}개 × 2 arm × ${REPS}회 = $(( ${#SETS[@]} * ${#CFG[@]} * 2 * REPS )) 프로파일"
for S in "${SETS[@]}"; do
  L="${S#*:}"; echo "  ${S%%:*} = ${L}  ($(( $(echo "$L" | tr ',' '\n' | wc -l) ))컬럼)"
done
echo "SCAN_ITERS=${SCAN_ITERS}  SCAN_WARMUP=${SCAN_WARMUP}  REPS=${REPS}"
echo

# arm 순서는 라운드마다 뒤집는다 (F-015 의 순서 효과 이후 신규 축의 기본).
for R in $(seq 1 "$REPS"); do
  echo "### 라운드 $R / $REPS"
  if (( R % 2 == 0 )); then ARMS=(patched baseline); else ARMS=(baseline patched); fi
  for S in "${SETS[@]}"; do
    SNAME="${S%%:*}"; SCOLS="${S#*:}"
    NCOL=$(echo "$SCOLS" | tr ',' '\n' | wc -l)
    for C in "${CFG[@]}"; do
      TABLE="${C%%:*}"; TAG="${C##*:}"
      echo "  ${TAG} ${SNAME}(${NCOL}컬럼)  [순서: ${ARMS[*]}]"
      for A in "${ARMS[@]}"; do
        run_one "$TABLE" "$TAG" "$A" "$R" "$SNAME" "$SCOLS" "$NCOL"; N=$((N+1))
      done
    done
  done
  echo
done

echo "✅ 완료 — ${N}회, $(( $(date +%s) - START ))초"
