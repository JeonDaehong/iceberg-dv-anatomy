#!/usr/bin/env bash
# 콜드 캐시 측정이 WSL2 에서 성립하는지부터 확인한다.
#
# 왜 필요한가:
#   07-coldcache.sh 는 /proc/sys/vm/drop_caches 에 3 을 써서 페이지 캐시를 비운다.
#   그런데 WSL2 는 리눅스 커널이 Windows 위의 VHD 를 읽는 구조라, 리눅스 쪽
#   페이지 캐시를 비워도 Windows 쪽에 블록이 그대로 캐시돼 있을 수 있다.
#   그러면 '콜드' 가 사실은 웜이고, R1(콜드에서 DV 비중이 낮아진다)이 안 나왔을 때
#   "I/O 가 분모에 안 들어왔다" 인지 "drop_caches 가 안 먹었다" 인지 구분할 수 없다.
#
#   즉 이 스크립트가 실패하면 07-coldcache.sh 의 결과는 해석 불가다. 먼저 돈다.
#
# 판정:
#   drop_caches 직후의 첫 읽기가 웜 대비 뚜렷하게 느려야 한다.
#   기준을 노이즈 바닥(9.7%)의 두 배인 20% 로 잡는다 — I/O 를 분모에 넣는 게
#   목적인데 20% 도 안 느려지면 애초에 분모가 안 바뀐 것이다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

TBL="${COLD_TABLE:-dv.g.d610}"

[[ -w /proc/sys/vm/drop_caches ]] || { echo "drop_caches 쓰기 불가 — root 로 실행하세요" >&2; exit 1; }

# 테이블이 실제로 어디에 있고 얼마나 큰지 — 캐시에 다 들어가는 크기인지 본다
DIR="${WAREHOUSE}/$(echo "${TBL#dv.}" | tr '.' '/')"
echo "테이블 경로 : $DIR"
echo "크기        : $(du -sh "$DIR" 2>/dev/null | cut -f1)"
echo "RAM         : $(free -h | awk '/^Mem:/{print $2" (free "$7")"}')"
echo

read_once() {   # $1 = drop 여부(1/0)
  if [[ "$1" == "1" ]]; then sync; echo 3 > /proc/sys/vm/drop_caches; fi
  # Spark 를 띄우지 않고 파일만 읽는다 — 측정 대상은 순수 I/O 다
  local T0 T1
  T0=$(date +%s.%N)
  find "$DIR" -name '*.parquet' -print0 | xargs -0 cat > /dev/null
  T1=$(date +%s.%N)
  echo "$T1 - $T0" | bc
}

echo "웜 3회 (캐시를 채운 뒤):"
read_once 1 > /dev/null    # 한 번 콜드로 읽어 캐시를 채운다
WARM=()
for i in 1 2 3; do W=$(read_once 0); WARM+=("$W"); printf "  %d: %.3fs\n" "$i" "$W"; done

echo "콜드 3회 (매번 drop_caches):"
COLD=()
for i in 1 2 3; do C=$(read_once 1); COLD+=("$C"); printf "  %d: %.3fs\n" "$i" "$C"; done

python3 - "${WARM[@]}" -- "${COLD[@]}" <<'PY'
import sys, statistics as st
a = sys.argv[1:]
i = a.index("--")
warm = [float(x) for x in a[:i]]
cold = [float(x) for x in a[i+1:]]
w, c = st.median(warm), st.median(cold)
print()
print("  웜  중앙값 %.3fs" % w)
print("  콜드 중앙값 %.3fs  (%.1f배, %+.0f%%)" % (c, c / w if w else 0, (c / w - 1) * 100 if w else 0))
if w and c / w >= 1.20:
    print("\n  ✅ drop_caches 가 실제로 먹는다. 07-coldcache.sh 를 돌려도 된다.")
else:
    print("\n  ❌ 콜드가 웜보다 20% 이상 느리지 않다.")
    print("     WSL2 의 Windows 쪽 블록 캐시가 살아 있을 가능성이 크다.")
    print("     이 상태로 07-coldcache.sh 를 돌리면 '콜드' 가 콜드가 아니므로")
    print("     결과를 해석할 수 없다. 측정하지 말고 조건을 먼저 고쳐야 한다.")
    sys.exit(2)
PY
