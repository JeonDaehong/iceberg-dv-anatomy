#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스캔 서브트리 **밖**의 샘플이 무엇인지 쪼갠다 — F-018 보정 0.53 의 나머지.

왜 이 도구인가:
  F-018 은 `wall = 0.53 x 샘플` 이라는 회귀를 냈고, 그 기전을 "꼬리 태스크인가,
  스캔 밖 고정비용인가, 대역폭 경합인가" 로 열어뒀다. F-031 이 그중 하나를 닫았다 —
  샘플링 방식(ctimer vs cycles)의 문제는 아니다. 남은 것은 "스캔 밖" 이 뭐냐는 것이다.

  그런데 그 답은 **이미 찍어둔 프로파일 안에** 있다. 전체 샘플의 69.4% 가 스캔 밖이다.
  다시 잴 필요 없이 쪼개면 된다.

⚠️ 이건 예측을 걸고 한 측정이 아니라 **이미 모은 데이터의 탐색적 분해**다.
   F-018 의 0.53 기울기 자체가 그랬듯이, 여기서 나오는 것은 가설이지 판정이 아니다.
   그렇게 표시해서 보고한다.

읽는 법:
  '스캔' 은 attribute.py 의 SCAN_ROOTS 와 같은 정의를 쓴다 (일관성 유지).
  나머지를 JVM 기동/JIT/GC/셔플/드라이버 등으로 나눈다. 분류는 배타적이고,
  어디에도 안 걸리는 것은 '미분류' 로 남겨 **숨기지 않는다.**
"""
import glob
import io
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, SCAN_ROOTS  # noqa: E402

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

# 배타적 분류. 위에서부터 먼저 걸리는 것으로 귀속한다 (순서가 곧 우선순위).
BUCKETS = [
    ("JIT 컴파일", [
        "CompileBroker", "C2Compiler", "Compile::", "C1_", "Compilation::",
        "NMethodSweeper", "ciEnv", "PhaseChaitin", "PhaseIdealLoop", "Matcher::"]),
    ("GC", [
        "G1CollectedHeap", "G1ParTask", "GCTaskThread", "ConcurrentGCThread",
        "WorkerThread::run", "G1Remark", "G1Conc", "SATBMarkQueue", "ParallelGC",
        "VM_G1", "MemAllocator"]),
    ("클래스 로딩·검증", [
        "ClassLoader", "SystemDictionary", "ClassFileParser", "Verifier",
        "java.lang.invoke", "MethodHandle", "LambdaForm", "java.util.zip",
        "jdk.internal.loader"]),
    ("셔플·직렬화", [
        "org.apache.spark.shuffle", "org.apache.spark.serializer",
        "org.apache.spark.storage", "org.apache.spark.util.collection",
        "com.esotericsoftware.kryo", "java.io.ObjectOutput"]),
    ("Spark 실행 골격", [
        "org.apache.spark.scheduler", "org.apache.spark.executor",
        "org.apache.spark.rdd", "org.apache.spark.sql.execution",
        "org.apache.spark.SparkContext", "org.apache.spark.rpc"]),
    ("Catalyst·계획 수립", [
        "org.apache.spark.sql.catalyst", "org.apache.spark.sql.internal",
        "org.codehaus.janino", "org.apache.spark.sql.Dataset"]),
    ("Iceberg 메타데이터", [
        "org.apache.iceberg.hadoop", "org.apache.iceberg.BaseTable",
        "org.apache.iceberg.ManifestReader", "org.apache.iceberg.SnapshotScan",
        "org.apache.iceberg.avro", "org.apache.iceberg.util.Tasks"]),
    ("VM·스레드 관리", [
        "VMThread", "VM_Operation", "SafepointSynchronize", "WatcherThread",
        "os::", "Monitor::", "ObjectMonitor", "Unsafe_Park", "JVM_"]),
]


def classify(stack):
    for name, needles in BUCKETS:
        for n in needles:
            if n in stack:
                return name
    return None


def decompose(path):
    stacks = parse_collapsed(path)
    total = sum(c for _, c in stacks)
    scan = 0
    buckets = Counter()
    unclassified = Counter()
    for st, c in stacks:
        if any(r in st for r in SCAN_ROOTS):
            scan += c
            continue
        b = classify(st)
        if b:
            buckets[b] += c
        else:
            buckets["미분류"] += c
            # 미분류의 정체를 알려면 대표 프레임을 세어둔다.
            frames = [f for f in st.split(";") if f and not f.startswith("/")]
            unclassified[frames[-1] if frames else "(빈 스택)"] += c
    return total, scan, buckets, unclassified


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else \
        "results/profiles/baseline__qpd610_cycles__r*.collapsed"
    paths = sorted(glob.glob(pattern))
    if not paths:
        p("프로파일이 없다: %s" % pattern); return

    tot = scan = 0
    agg = Counter(); unc = Counter()
    for path in paths:
        t, s, b, u = decompose(path)
        tot += t; scan += s; agg += b; unc += u

    p("=" * 84)
    p("스캔 밖은 무엇인가 — F-018 보정 0.53 의 나머지 (탐색적 분해, 판정 아님)")
    p("=" * 84)
    p("프로파일 %d개 합산 · 전체 샘플 %s" % (len(paths), f"{tot:,}"))
    p("")
    p("%-22s %12s %9s %9s" % ("구간", "샘플", "전체 대비", "스캔밖 대비"))
    p("-" * 56)
    p("%-22s %12s %8.1f%% %9s" % ("스캔 (분석 대상)", f"{scan:,}", scan/tot*100, "—"))
    outside = tot - scan
    for name, c in agg.most_common():
        p("%-22s %12s %8.1f%% %8.1f%%" % (name, f"{c:,}", c/tot*100, c/outside*100))
    p("-" * 56)
    p("%-22s %12s %8.1f%% %9s" % ("스캔 밖 합계", f"{outside:,}", outside/tot*100, "100.0%"))

    if unc:
        p("")
        p("미분류의 정체 (말단 프레임 상위 12개) — 숨기지 않는다")
        for f, c in unc.most_common(12):
            p("   %8.2f%%  %s" % (c/tot*100, f[:96]))
    p("=" * 84)


main()
