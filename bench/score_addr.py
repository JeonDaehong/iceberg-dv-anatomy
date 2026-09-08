#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DvAddrBench 의 2×2 요인 설계를 채점한다. 예측 S1~S4 는 소스 헤더에 있고 여기 없다.

포크별 평균도 뽑는다 — 이 축에서 실제로 중요한 것이 그것이었다(JIT 이봉).
사용: python3 bench/score_addr.py [results/addr-perfnorm.txt] [원본 JMH 로그]
"""
import io
import re
import sys

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

path = sys.argv[1] if len(sys.argv) > 1 else "results/addr-perfnorm.txt"
raw = sys.argv[2] if len(sys.argv) > 2 else None
txt = io.open(path, encoding="utf-8", errors="replace").read()

row = re.compile(
    r"^DvAddrBench\.(?P<b>\w+)(?::(?P<c>[\w\-]+))?\s+\d+\s+(?P<p>[A-Z_0-9]+)\s+avgt\s+\d+\s+"
    r"(?P<s>[\d.]+)\s*(?:±\s*(?P<e>[\d.]+))?", re.M)
D = {}
for m in row.finditer(txt):
    D.setdefault(m.group("b"), {}).setdefault(m.group("p"), {})[m.group("c") or "us"] = (
        float(m.group("s")), float(m.group("e") or 0))

LO, HI = "DENSE_8", "DENSE_50"
CELL = [("a_brLive", "분기 O · 주소 live  (현행)"), ("b_brRow", "분기 O · 주소 rowId"),
        ("c_noBrLive", "분기 X · 주소 live"), ("d_noBrRow", "분기 X · 주소 rowId")]
if not all(b in D for b, _ in CELL):
    p("네 칸이 다 없다: %s" % sorted(D)); sys.exit(1)

v = lambda b, pat, c="us": D[b][pat].get(c, (float("nan"), 0.0))
d = {b: v(b, HI)[0] - v(b, LO)[0] for b, _ in CELL}

p("=" * 96)
p("저장 주소 의존 — 2×2 요인 설계 (분기 O/X × 주소 live/rowId)")
p("=" * 96)
p("")
p("%-12s %-30s %16s %16s %12s" % ("칸", "무엇인가", "d=8%", "d=50%", "증가폭"))
for b, lab in CELL:
    (s1, e1), (s2, e2) = v(b, LO), v(b, HI)
    p("%-12s %-30s %8.3f ±%-6.3f %8.3f ±%-6.3f %+11.3f" % (b, lab, s1, e1, s2, e2, d[b]))

p("")
p("─ S1 [설계 검증]  분기 실패가 두 그룹으로 갈리는가 (분기 O ≥ 500 / 분기 X < 50)")
bm = {b: v(b, HI, "branch-misses") for b, _ in CELL}
for b, lab in CELL:
    p("   %-12s %10.1f ±%-10.1f" % (b, bm[b][0], bm[b][1]))
okO = bm["a_brLive"][0] >= 500 and bm["b_brRow"][0] >= 500
okX = bm["c_noBrLive"][0] < 50 and bm["d_noBrRow"][0] < 50
gap = min(bm["a_brLive"][0], bm["b_brRow"][0]) / max(bm["c_noBrLive"][0], bm["d_noBrRow"][0], 1e-9)
p("   갈라짐 %.0f배.  판정: %s" % (gap,
  "**맞음**" if (okO and okX) else
  "★빗나감 — 문턱을 못 넘은 칸이 있다 (갈라짐 자체는 크다. 문턱을 사후에 옮기지 않는다)"))

p("")
p("─ S2 ★판정용★  분기가 없으면 주소는 밀도를 안 타는가 (차 ≤ 1.0 us/op)")
noBr = abs(d["c_noBrLive"] - d["d_noBrRow"])
p("   Δc %+.3f  vs  Δd %+.3f   →  차 **%.3f**" % (d["c_noBrLive"], d["d_noBrRow"], noBr))
p("   판정: %s" % ("**맞음** — 분기 없이는 주소가 밀도 반응을 안 만든다." if noBr <= 1.0
                  else "★빗나감 — %.3f us/op 다." % noBr))

p("")
p("─ S3 ★판정용★  분기가 있을 때 주소 차가 2배 이상 벌어지는가 (상호작용)")
br = abs(d["a_brLive"] - d["b_brRow"])
p("   Δa %+.3f  vs  Δb %+.3f   →  차 **%.3f**" % (d["a_brLive"], d["b_brRow"], br))
p("   상호작용 비 = %.3f / %.3f = **%.2f배**" % (br, noBr, br / noBr if noBr else 0))
p("   판정: %s" % ("**맞음** — 주소는 분기와 결합할 때만 값을 낸다. F-034 가설이 산다."
                  if noBr and br / noBr >= 2.0 else
                  "★빗나감 — 상호작용이 없다. **F-034 의 주소 의존 가설은 죽는다.**"))

p("")
p("─ 주소의 고정비 (밀도를 타지 않는 몫)")
for pat in (LO, HI):
    p("   %-9s  a−b %+.3f   c−d %+.3f"
      % (pat, v("a_brLive", pat)[0] - v("b_brRow", pat)[0],
         v("c_noBrLive", pat)[0] - v("d_noBrRow", pat)[0]))
p("   → 항상 양수이고 밀도에 거의 안 움직이면, 주소 의존은 실재하되 반등의 원인이 아니다.")

p("")
p("─ S4  d=50% 에서 가장 느린 칸이 a(현행)인가")
slow = max(CELL, key=lambda x: v(x[0], HI)[0])[0]
p("   가장 느림: %s   판정: %s" % (slow, "**맞음**" if slow == "a_brLive" else "★빗나감"))

if raw:
    p("")
    p("─ 포크별 평균 (JIT 이 포크마다 다르게 컴파일하는지)")
    s = io.open(raw, encoding="utf-8", errors="replace").read()
    for blk in re.split(r"# Benchmark: ", s)[1:]:
        name = blk.split("\n")[0].split(".")[-1]
        pat = re.search(r"pattern = (\w+)", blk)
        if not pat or pat.group(1) != HI:
            continue
        means = []
        for f in re.split(r"# Fork: ", blk)[1:]:
            it = [float(x) for x in re.findall(r"^Iteration\s+\d+:\s+([\d.]+) us/op", f, re.M)]
            if it:
                means.append(sum(it) / len(it))
        if means:
            spread = (max(means) - min(means)) / (sum(means) / len(means)) * 100
            p("   %-12s %s   흩어짐 %.0f%%" % (name, " ".join("%.1f" % m for m in means), spread))
    p("   → 분기 있는 칸만 이봉이면, 마이크로벤치의 반등 '크기' 는 JIT 추첨에 달렸다는 뜻이다.")
p("=" * 96)
