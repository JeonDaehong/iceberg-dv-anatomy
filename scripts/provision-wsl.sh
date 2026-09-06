#!/usr/bin/env bash
# =====================================================================
#  provision-wsl.sh — WSL2 Ubuntu 안에서 Phase 0 실행 환경을 전부 구축한다.
#
#  실행:
#    wsl -d Ubuntu-24.04 -- bash /mnt/d/.../iceberg-dv-anatomy/scripts/provision-wsl.sh
#
#  멱등(idempotent)하다. 중간에 실패하면 고치고 그냥 다시 돌리면 된다.
# =====================================================================
set -uo pipefail

DL_DIR="/mnt/d/dv-tools/dl"          # Windows 쪽에 미리 받아둔 아카이브 (재다운로드 방지)
OPT="$HOME/opt"                       # 도구는 Linux 네이티브 fs 에 설치 (/mnt/* 는 I/O 가 느림)
SPARK_TGZ="spark-4.0.4-bin-hadoop3.tgz"
SPARK_DIR="$OPT/spark-4.0.4-bin-hadoop3"
AP_TGZ="async-profiler-4.5-linux-x64.tar.gz"
AP_DIR="$OPT/async-profiler-4.5-linux-x64"
ICEBERG_JAR_NAME="iceberg-spark-runtime-4.0_2.13-1.11.0.jar"

FAIL=0
say()  { printf '\n\033[36m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m[ok]\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33m[warn]\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m[FAIL]\033[0m %s\n' "$*"; FAIL=1; }

SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

say "=== iceberg-dv-anatomy : WSL provisioning ==="
echo "  user=$(id -un)  home=$HOME"

mkdir -p "$OPT"

# ---------------------------------------------------------------- 1. apt
say "[1/6] APT 패키지"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq 2>&1 | tail -2
$SUDO apt-get install -y -qq \
    openjdk-17-jdk python3 python3-venv curl ca-certificates tar procps \
    2>&1 | tail -3
if command -v java >/dev/null 2>&1; then ok "java: $(java -version 2>&1 | head -1)"; else bad "java 설치 실패"; fi
if command -v python3 >/dev/null 2>&1; then ok "python3: $(python3 --version 2>&1)"; else bad "python3 설치 실패"; fi

# JDK 17 을 명시적으로 고정 (Ubuntu 가 다른 기본 JDK 를 쓸 수 있음)
JAVA17="$(dirname "$(dirname "$(readlink -f "$(command -v javac || command -v java)")")")"
if [[ -d /usr/lib/jvm/java-17-openjdk-amd64 ]]; then
  JAVA17=/usr/lib/jvm/java-17-openjdk-amd64
fi
ok "JAVA_HOME 후보: $JAVA17"

# ---------------------------------------------------------------- 2. spark
say "[2/6] Spark 4.0.4"
if [[ -d "$SPARK_DIR" ]]; then
  ok "이미 설치됨: $SPARK_DIR"
else
  SRC=""
  if [[ -f "$DL_DIR/$SPARK_TGZ" ]]; then
    SZ=$(stat -c %s "$DL_DIR/$SPARK_TGZ")
    if [[ $SZ -gt 500000000 ]]; then
      SRC="$DL_DIR/$SPARK_TGZ"
      ok "미리 받아둔 아카이브 사용 ($((SZ/1024/1024))MB) — 재다운로드 안 함"
    else
      warn "다운로드가 불완전합니다 ($((SZ/1024/1024))MB). 직접 받습니다."
    fi
  fi
  if [[ -z "$SRC" ]]; then
    echo "  다운로드 중... (~524MB, Apache CDN)"
    curl -fL --retry 3 -o "/tmp/$SPARK_TGZ" \
      "https://dlcdn.apache.org/spark/spark-4.0.4/$SPARK_TGZ" \
      && SRC="/tmp/$SPARK_TGZ"
  fi
  if [[ -n "$SRC" ]]; then
    tar xzf "$SRC" -C "$OPT" && ok "설치: $SPARK_DIR"
  else
    bad "Spark 아카이브를 구하지 못했습니다"
  fi
fi

# ---------------------------------------------------------------- 3. async-profiler
say "[3/6] async-profiler 4.5"
if [[ -f "$AP_DIR/lib/libasyncProfiler.so" ]]; then
  ok "이미 설치됨: $AP_DIR"
else
  SRC=""
  [[ -f "$DL_DIR/$AP_TGZ" ]] && SRC="$DL_DIR/$AP_TGZ"
  if [[ -z "$SRC" ]]; then
    curl -fL --retry 3 -o "/tmp/$AP_TGZ" \
      "https://github.com/async-profiler/async-profiler/releases/download/v4.5/$AP_TGZ" \
      && SRC="/tmp/$AP_TGZ"
  fi
  if [[ -n "$SRC" ]]; then
    tar xzf "$SRC" -C "$OPT" && ok "설치: $AP_DIR"
  else
    bad "async-profiler 아카이브를 구하지 못했습니다"
  fi
fi
[[ -f "$AP_DIR/lib/libasyncProfiler.so" ]] && ok "libasyncProfiler.so 확인" || bad "libasyncProfiler.so 없음"

# ---------------------------------------------------------------- 4. env
say "[4/6] 환경변수 (~/.bashrc)"
MARK="# >>> iceberg-dv-anatomy >>>"
if grep -q "$MARK" "$HOME/.bashrc" 2>/dev/null; then
  ok "이미 등록됨 (건너뜀)"
else
  cat >> "$HOME/.bashrc" <<EOF

$MARK
export JAVA_HOME="$JAVA17"
export SPARK_HOME="$SPARK_DIR"
export AP_HOME="$AP_DIR"
export PATH="\$JAVA_HOME/bin:\$SPARK_HOME/bin:\$AP_HOME/bin:\$PATH"
# WSL2: /mnt/* (Windows drive) 는 I/O 가 매우 느리다.
# 워크로드 데이터는 반드시 Linux 네이티브 fs 에 둔다.
export DV_WAREHOUSE="\$HOME/dv-anatomy/warehouse"
# <<< iceberg-dv-anatomy <<<
EOF
  ok "~/.bashrc 등록 완료"
fi

# ~/.bashrc 만으로는 부족하다. Ubuntu 의 .bashrc 는 비대화형 셸에서
# 맨 앞에 return 하므로 `wsl -- bash -lc ...` 에는 전혀 반영되지 않는다.
# 로그인 셸이 확실히 읽는 /etc/profile.d 에도 심는다.
if [[ -w /etc/profile.d ]] || [[ "$(id -u)" -eq 0 ]]; then
  cat > /etc/profile.d/dv-anatomy.sh <<EOF
export JAVA_HOME="$JAVA17"
export SPARK_HOME="$SPARK_DIR"
export AP_HOME="$AP_DIR"
export PATH="\$JAVA_HOME/bin:\$SPARK_HOME/bin:\$AP_HOME/bin:\$PATH"
export DV_WAREHOUSE="\$HOME/dv-anatomy/warehouse"
EOF
  chmod 644 /etc/profile.d/dv-anatomy.sh
  ok "/etc/profile.d/dv-anatomy.sh 등록 (비대화형 로그인 셸용)"
else
  warn "/etc/profile.d 에 쓸 수 없음 — bash -lc 실행 시 PATH 가 안 잡힐 수 있습니다"
fi

export JAVA_HOME="$JAVA17"
export SPARK_HOME="$SPARK_DIR"
export AP_HOME="$AP_DIR"
export PATH="$JAVA_HOME/bin:$SPARK_HOME/bin:$AP_HOME/bin:$PATH"
export DV_WAREHOUSE="$HOME/dv-anatomy/warehouse"
mkdir -p "$DV_WAREHOUSE"

command -v spark-submit >/dev/null 2>&1 && ok "spark-submit: $(command -v spark-submit)" || bad "spark-submit 없음"

# ---------------------------------------------------------------- 5. iceberg jars
say "[5/6] Iceberg 1.11.0 런타임 jar"
# --packages 대신 로컬 fat jar 를 쓴다. 오프라인 실행뿐 아니라, Ivy 해석이
# '프로파일링 대상 JVM 안에서' 도는 것을 없애 측정 분모를 깨끗하게 만든다.
mkdir -p "$OPT/jars"
if [[ -f "$OPT/jars/$ICEBERG_JAR_NAME" ]]; then
  ok "이미 있음: $OPT/jars/$ICEBERG_JAR_NAME"
elif [[ -f "$DL_DIR/$ICEBERG_JAR_NAME" ]]; then
  cp "$DL_DIR/$ICEBERG_JAR_NAME" "$OPT/jars/"
  ok "미리 받아둔 jar 복사 ($(( $(stat -c %s "$OPT/jars/$ICEBERG_JAR_NAME") / 1024 / 1024 ))MB)"
else
  echo "  다운로드 중... (~48MB)"
  curl -fL --retry 3 -o "$OPT/jars/$ICEBERG_JAR_NAME" \
    "https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.0_2.13/1.11.0/$ICEBERG_JAR_NAME" \
    && ok "다운로드 완료" || bad "Iceberg jar 다운로드 실패"
fi
ICEBERG_JAR="$OPT/jars/$ICEBERG_JAR_NAME"
[[ -f "$ICEBERG_JAR" ]] && ok "jar: $ICEBERG_JAR" || bad "Iceberg jar 없음"

# ---------------------------------------------------------------- 6. smoke test
say "[6/6] 스모크 테스트 (Spark + Iceberg V3 + DV)"
SMOKE="/tmp/dv_smoke.py"
cat > "$SMOKE" <<'PY'
import sys
from pyspark.sql import SparkSession
wh = sys.argv[1]
s = (SparkSession.builder.appName("dv-smoke").master("local[2]")
     .config("spark.sql.extensions",
             "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
     .config("spark.sql.catalog.dv", "org.apache.iceberg.spark.SparkCatalog")
     .config("spark.sql.catalog.dv.type", "hadoop")
     .config("spark.sql.catalog.dv.warehouse", wh)
     .config("spark.ui.enabled", "false")
     .getOrCreate())
s.sparkContext.setLogLevel("ERROR")
s.sql("CREATE NAMESPACE IF NOT EXISTS dv.smoke")
s.sql("DROP TABLE IF EXISTS dv.smoke.t PURGE")
s.sql("""CREATE TABLE dv.smoke.t USING iceberg
         TBLPROPERTIES ('format-version'='3','write.delete.mode'='merge-on-read')
         AS SELECT id, CAST(id%7 AS INT) g FROM range(0, 200000)""")
s.sql("DELETE FROM dv.smoke.t WHERE id % 200 = 0")
live = s.sql("SELECT count(*) c FROM dv.smoke.t").collect()[0].c
df = s.sql("SELECT content, record_count, file_size_in_bytes FROM dv.smoke.t.delete_files").collect()
print(f"SMOKE live_rows={live} (expect 199000)")
print(f"SMOKE delete_files={len(df)}")
for r in df:
    print(f"SMOKE   content={r.content} deleted={r.record_count} bytes={r.file_size_in_bytes}")
ok = (live == 199000) and len(df) > 0
print("SMOKE_RESULT=" + ("PASS" if ok else "FAIL"))
s.stop()
PY

SMOKE_OUT=$(spark-submit --master "local[2]" --jars "$ICEBERG_JAR" \
    --conf spark.ui.enabled=false "$SMOKE" "$DV_WAREHOUSE" 2>&1)
echo "$SMOKE_OUT" | grep -E "^SMOKE" || true
if echo "$SMOKE_OUT" | grep -q "SMOKE_RESULT=PASS"; then
  ok "Spark + Iceberg V3 + Deletion Vector 동작 확인"
else
  bad "스모크 테스트 실패"
  echo "$SMOKE_OUT" | grep -iE "exception|error|caused by" | head -15
fi

# ---------------------------------------------------------------- summary
echo
if [[ $FAIL -eq 0 ]]; then
  printf '\033[32m%s\033[0m\n' "====================================================="
  printf '\033[32m%s\033[0m\n' " 프로비저닝 완료"
  printf '\033[32m%s\033[0m\n' "====================================================="
  echo
  echo "  JAVA_HOME  = $JAVA_HOME"
  echo "  SPARK_HOME = $SPARK_HOME"
  echo "  AP_HOME    = $AP_HOME"
  echo "  워크로드 데이터: \$DV_WAREHOUSE = $DV_WAREHOUSE  (Linux 네이티브 fs)"
  echo
  echo "  다음:"
  echo "    cd $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/phase0"
  echo "    ./scripts/run-all.sh"
else
  printf '\033[31m%s\033[0m\n' " 실패 항목이 있습니다. 위 [FAIL] 을 확인하세요."
  exit 1
fi
