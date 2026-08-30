#!/usr/bin/env bash
# 빌드한 패치 jar 을 phase0 의 탐색 경로에 설치한다.
#
# 이름을 베이스라인과 똑같이 두는 이유: spark-submit 인자에서 jar 이름이 바뀌면
# 클래스패스 순서/셰이딩이 달라질 여지가 생긴다. 파일명은 고정하고 디렉터리로 가른다.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./config.env

SRC="${1:-$HOME/src/iceberg/spark/v4.0/spark-runtime/build/libs}"

echo "빌드 산출물: $SRC"
ls -la "$SRC" 2>/dev/null || { echo "빌드 산출물이 없습니다. gradlew shadowJar 를 먼저 돌리세요." >&2; exit 1; }

# shadowJar 산출물을 고른다. -sources / -javadoc / 비-shadow jar 은 제외.
JAR=""
for cand in "$SRC"/*.jar; do
  base="$(basename "$cand")"
  case "$base" in
    *-sources.jar|*-javadoc.jar|*-tests.jar) continue ;;
  esac
  # shadow jar 은 셰이딩된 roaringbitmap 을 품고 있다. 그걸로 판별한다.
  # 주의: `grep -q` 는 첫 매치에서 파이프를 닫아 unzip 에 SIGPIPE 를 준다.
  # set -o pipefail 이면 그게 실패로 잡혀 항상 "못 찾음" 이 된다. 반드시 전량 소비할 것.
  if unzip -l "$cand" 2>/dev/null | grep -F "org/apache/iceberg/shaded/org/roaringbitmap/RoaringBitmap.class" >/dev/null; then
    JAR="$cand"
    break
  fi
done

[[ -n "$JAR" ]] || { echo "셰이딩된 runtime jar 을 못 찾았습니다." >&2; exit 1; }

mkdir -p "$DV_JAR_DIR"
cp -f "$JAR" "${DV_JAR_DIR}/${ICEBERG_JAR_NAME}"
echo "설치: $JAR"
echo "  -> ${DV_JAR_DIR}/${ICEBERG_JAR_NAME}"

# 패치가 정말 들어있는지 바이트 수준으로 확인한다.
# (빌드가 캐시된 클래스를 재사용해 조용히 옛날 코드를 넣는 사고를 막는다)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -o -q "${DV_JAR_DIR}/${ICEBERG_JAR_NAME}" \
  "org/apache/iceberg/deletes/PositionDeleteIndex.class" \
  "org/apache/iceberg/spark/data/vectorized/ColumnarBatchUtil*.class" -d "$TMP"

echo
echo "패치 확인:"
if javap -p -cp "$TMP" org.apache.iceberg.deletes.PositionDeleteIndex | grep -F "forEachInRange" >/dev/null; then
  echo "  ✅ PositionDeleteIndex.forEachInRange 존재"
else
  echo "  ❌ PositionDeleteIndex.forEachInRange 없음 — 패치가 안 들어갔습니다." >&2
  exit 1
fi

if javap -p -cp "$TMP" 'org.apache.iceberg.spark.data.vectorized.ColumnarBatchUtil$RowIdMappingBuilder' >/dev/null 2>&1; then
  echo "  ✅ ColumnarBatchUtil\$RowIdMappingBuilder 존재"
else
  echo "  ❌ ColumnarBatchUtil 의 구간 경로가 없습니다." >&2
  exit 1
fi

echo
echo "베이스라인: $BASELINE_JAR"
[[ -f "$BASELINE_JAR" ]] && echo "  ✅ 존재" || echo "  ❌ 없음"
