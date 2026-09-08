#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""셔플 캐시 축 채점. 예측 C1~C3 은 scripts/22-shufcache.sh 헤더에 있고 여기 없다."""
import glob
import io
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

R = sys.argv[1] if len(sys.argv) > 1 else "results"
TAG = os.environ.get("SC_TAG", "d610")
EVENTS = (os.environ.get("SC_EVENTS") or "cycles instructions cache-misses").split()
QUERIES = [("scan", "q0 스캔만"), ("aggL", "q2 집계-대")]
C2_TOL = 10.0   # 명령어 허용 변화 (%)
C3_MIN = 1.3    # cache-miss 가 몇 배 이상이면 C3 적중


def cell(query, event):
    # 스크립트는 `tr -c 'a-zA-Z0-9' '_'` 로 이름을 만드는데, echo 의 개행까지 바뀌어
    # 이벤트 이름 뒤에 밑줄이 하나 더 붙는다 (`cycles__c2_r1`). 그래서 뒤에 `*` 를 둔다.
    safe = "".join(c if c.isalnum() else "_" for c in event)
    scan, tot, dv = [], [], []
    for path in sorted(glob.glob(os.path.join(
            R, "profiles", "baseline__sc%s%s_%s*_c*_r*.collapsed" % (TAG, query, safe)))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        scan.append(a["scan_samples"])
        tot.append(a["total_samples"])
        dv.append(a["dv_union_samples"])
    if not scan:
        return None
    return dict(n=len(scan), scan=st.median(scan), total=st.median(tot), dv=st.median(dv),
                rng=(min(scan), max(scan)))


p("=" * 100)
p("셔플이 붙으면 스캔 서브트리가 왜 커지는가 — 일이 는 것인가, 막히는 것인가")
p("=" * 100)

data = {}
for ev in EVENTS:
    for q, _ in QUERIES:
        data[(q, ev)] = cell(q, ev)

p("")
p("─ C1 [설계 검증]  세 이벤트가 다 잡히는가")
missing = [ev for ev in EVENTS if not data.get(("scan", ev)) or not data.get(("aggL", ev))]
for ev in EVENTS:
    a, b = data.get(("scan", ev)), data.get(("aggL", ev))
    p("   %-16s %s" % (ev, "✅ 둘 다 있음" if (a and b) else "❌ 프로파일 없음"))
if missing:
    p("   판정: ★빗나감 — %s 를 못 잡았다. 여기서 멈춘다." % ", ".join(missing))
    p("=" * 100)
    sys.exit(0)
p("   판정: **맞음**")

p("")
p("─ 스캔 서브트리에 귀속된 샘플 (중앙값)")
p("%-16s %14s %14s %10s %16s" % ("이벤트", "q0 스캔만", "q2 집계-대", "배율", "q2 범위"))
ratio = {}
for ev in EVENTS:
    a, b = data[("scan", ev)], data[("aggL", ev)]
    ratio[ev] = b["scan"] / a["scan"] if a["scan"] else 0
    p("%-16s %14.0f %14.0f %9.2f배 %7.0f ~ %-7.0f"
      % (ev, a["scan"], b["scan"], ratio[ev], b["rng"][0], b["rng"][1]))

p("")
p("─ C2 ★판정용★  명령어는 그대로인데 사이클만 느는가 (문턱 ±%.0f%%)" % C2_TOL)
if "instructions" in ratio and "cycles" in ratio:
    di = (ratio["instructions"] - 1) * 100
    dc = (ratio["cycles"] - 1) * 100
    p("   명령어 %+.1f%%   사이클 %+.1f%%" % (di, dc))
    ipc_q0 = data[("scan", "instructions")]["scan"] / max(data[("scan", "cycles")]["scan"], 1)
    ipc_q2 = data[("aggL", "instructions")]["scan"] / max(data[("aggL", "cycles")]["scan"], 1)
    p("   스캔 서브트리 IPC 대용치(명령어/사이클 샘플비): %.2f → %.2f (%+.1f%%)"
      % (ipc_q0, ipc_q2, (ipc_q2 / ipc_q0 - 1) * 100 if ipc_q0 else 0))
    if abs(di) <= C2_TOL and dc > C2_TOL:
        p("   판정: **맞음** — 리더가 하는 일은 같은데 막힌다. stall 이다.")
    elif abs(di) > C2_TOL and di > 0:
        p("   판정: ★빗나감 — 명령어도 %+.1f%% 늘었다. 리더가 실제로 더 많은 일을 한다." % di)
        p("         캐시 압박이 아니라 다른 설명이 필요하다.")
    else:
        p("   판정: 판정 불가 — 사이클도 %+.1f%% 로 문턱 안이다. 현상 자체가 이 조건에서 약하다." % dc)

p("")
p("─ C3  스캔 서브트리의 cache-miss 가 q2 에서 %.1f배 이상인가" % C3_MIN)
if "cache-misses" in ratio:
    r = ratio["cache-misses"]
    p("   cache-miss 샘플 %.0f → %.0f = **%.2f배**"
      % (data[("scan", "cache-misses")]["scan"], data[("aggL", "cache-misses")]["scan"], r))
    if r >= C3_MIN:
        p("   판정: **맞음** — 집계 해시 테이블이 리더의 캐시를 밀어낸다는 F-024 의 추론이 확인된다.")
    else:
        p("   판정: ★빗나감 — %.2f배뿐이다. stall 의 원인이 캐시가 아니다." % r)
        p("         (메모리 대역·TLB·GC 는 이 축이 안 갈랐다.)")

p("")
p("─ 참고: 분모 전체와 DV (귀속이 셔플에 오염되지 않았는지)")
p("%-16s %12s %12s %12s %12s" % ("이벤트", "q0 전체", "q2 전체", "q0 DV", "q2 DV"))
for ev in EVENTS:
    a, b = data[("scan", ev)], data[("aggL", ev)]
    p("%-16s %12.0f %12.0f %12.0f %12.0f" % (ev, a["total"], b["total"], a["dv"], b["dv"]))
p("   DV 샘플이 두 쿼리에서 비슷해야 한다 — 같은 행에 같은 일을 하기 때문 (F-024 R2).")
p("=" * 100)
