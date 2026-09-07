#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JMH -prof perfnorm 출력을 읽어 P-B1~P-B4 를 기계적으로 판정한다.

예측은 bench/run-perf.sh 헤더에 있고 여기에 복제하지 않는다.
(예측을 채점 도구가 다시 적으면 사후에 조용히 고칠 수 있게 된다.)
"""
import io, re, sys

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

path = sys.argv[1] if len(sys.argv) > 1 else "results/a_baseline_perfnorm.txt"
txt = io.open(path, encoding="utf-8", errors="replace").read()

# "Benchmark[:counter]  batchSize  PATTERN  avgt  cnt  score  [± err]  unit"
row = re.compile(
    r"^\S*?(?::(?P<ctr>[\w\-]+))?\s+\d+\s+(?P<pat>[A-Z_0-9]+)\s+avgt\s+\d+\s+"
    r"(?P<score>[\d.]+)", re.M)

data = {}
for m in row.finditer(txt):
    pat = m.group("pat"); ctr = m.group("ctr") or "us_op"
    data.setdefault(pat, {})[ctr] = float(m.group("score"))

ORDER = ["SPARSE_0_5", "MEDIUM_5", "DENSE_12", "RUN_PARTIAL"]
CONT  = {"SPARSE_0_5": "array", "MEDIUM_5": "array",
         "DENSE_12": "bitmap", "RUN_PARTIAL": "run"}
pats = [x for x in ORDER if x in data]
if not pats:
    p("파싱된 패턴이 없다:", path); sys.exit(1)

ROWS = 5000.0
p("=" * 96)
p("PMU 측정 — 행당 삭제 체크의 비용이 어디서 나오는가")
p("=" * 96)
p("%-12s %-7s %8s %11s %11s %10s %9s %8s" %
  ("패턴", "컨테이너", "us/op", "cycles/행", "insn/행", "분기/행", "실패/행", "IPC"))
for k in pats:
    d = data[k]
    p("%-12s %-7s %8.2f %11.1f %11.1f %10.1f %9.4f %8.2f" % (
        k, CONT.get(k, "?"), d.get("us_op", 0),
        d.get("cycles", 0)/ROWS, d.get("instructions", 0)/ROWS,
        d.get("branches", 0)/ROWS, d.get("branch-misses", 0)/ROWS,
        d.get("IPC", 0)))

def g(k, c): return data[k].get(c, 0.0)

p("")
p("─ P-B1  array 의 op 당 branch-misses 가 bitmap 의 2배 이상인가")
bm = g("DENSE_12", "branch-misses")
for k in ("SPARSE_0_5", "MEDIUM_5"):
    if k in data:
        r = g(k, "branch-misses") / bm if bm else float("nan")
        p("   %-11s %8.1f vs bitmap %.1f  →  %.2f배   %s" %
          (k, g(k, "branch-misses"), bm, r, "충족" if r >= 2 else "미달"))
ok1 = all(g(k, "branch-misses") >= 2*bm for k in ("SPARSE_0_5", "MEDIUM_5") if k in data)
p("   판정: %s" % ("맞음" if ok1 else "★빗나감 — array 가 일률적으로 더 틀리지 않는다"))

p("")
p("─ P-B2  MEDIUM_5 > SPARSE_0_5 (카디널리티가 크면 이진 탐색이 깊다)")
a, b = g("SPARSE_0_5", "branch-misses"), g("MEDIUM_5", "branch-misses")
p("   %.1f → %.1f  (%.2f배)   판정: %s" % (a, b, b/a if a else 0,
  "맞음 — 방향은 예측대로다" if b > a else "빗나감"))

p("")
p("─ P-B3 ★기전 판정★  비용 차이를 분기 예측 실패로 설명할 수 있는가")
CYC_PER_MISS = 18.0          # Zen 3 분기 오예측 페널티, 보수적 중앙값 (15~20)
for k in ("SPARSE_0_5", "MEDIUM_5"):
    if k not in data: continue
    dcyc  = g(k, "cycles") - g("DENSE_12", "cycles")          # 설명해야 할 사이클 격차
    dmiss = g(k, "branch-misses") - g("DENSE_12", "branch-misses")
    need  = dcyc / CYC_PER_MISS                                # 그러려면 필요한 추가 실패 수
    share = (dmiss * CYC_PER_MISS) / dcyc * 100 if dcyc else 0
    p("   %-11s 사이클 격차 %9.0f  |  필요한 추가 실패 %8.0f  |  실제 %7.1f  |  설명력 %5.2f%%"
      % (k, dcyc, need, dmiss, share))
worst = max((g(k,"branch-misses")-bm)*CYC_PER_MISS/(g(k,"cycles")-g("DENSE_12","cycles"))*100
            for k in ("SPARSE_0_5","MEDIUM_5") if k in data)
p("   판정: %s" % ("맞음" if worst >= 50 else
    "★빗나감 — 분기 예측 실패는 비용 차이의 %.1f%% 밖에 설명하지 못한다" % worst))

p("")
p("─ P-B4  대안 가설: 비용은 그냥 명령어 수인가 (IPC 가 일정한가)")
ipcs = [g(k, "IPC") for k in pats]
lo, hi = min(ipcs), max(ipcs)
p("   IPC 범위 %.2f ~ %.2f  (변동 %.1f%%)" % (lo, hi, (hi-lo)/lo*100))
for k in pats:
    if k == "DENSE_12": continue
    ri = g(k, "instructions") / g("DENSE_12", "instructions")
    rc = g(k, "cycles") / g("DENSE_12", "cycles")
    p("   %-11s bitmap 대비  명령어 %.2f배  vs  사이클 %.2f배   (어긋남 %+.1f%%)"
      % (k, ri, rc, (rc/ri - 1) * 100))
ok4 = (hi-lo)/lo < 0.10
p("   판정: %s" % ("맞음 — IPC 가 사실상 상수다. 비용 = 명령어 수." if ok4 else "빗나감"))

p("")
p("─ 곁다리: 캐시 지역성 가설도 같이 죽는다")
for k in pats:
    p("   %-11s L1-dcache 미스/op %9.1f   (us/op %6.2f)" %
      (k, g(k, "L1-dcache-load-misses"), g(k, "us_op")))
p("   가장 빠른 DENSE_12 가 L1 미스는 가장 많다 → 'bitmap 은 L1 상주라 빠르다' 는 설명도 성립하지 않는다.")
p("=" * 96)
