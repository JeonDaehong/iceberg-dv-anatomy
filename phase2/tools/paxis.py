#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`p` 축 채점 — F-009 의 3.53배가 run '구조' 덕인가 컨테이너 '개수' 덕인가.

예측 P1~P3 은 scripts/19-paxis.sh 헤더에 있고 여기 없다 (선기록 규칙).

P1 이 깨지면 나머지는 채점하지 않는다. F-010 이 죽은 이유가 정확히 그것이고,
같은 실패를 '그래도 표는 뽑혔으니' 로 넘기면 두 번째로 날아간다.
"""
import csv
import glob
import io
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

R = sys.argv[1] if len(sys.argv) > 1 else "results"
BP = os.environ.get("PX_DENSITY_BP", "50")
OCCS = (os.environ.get("PX_OCCUPANCIES") or "10000 5000 2500 1000").split()

# F-009 가 같은 밀도(d=0.5%)에서 L 을 키워 본 값. 여기서 다시 재지 않고 비교 대상으로만 쓴다.
F009_COST_GAIN = 3.53      # L=1 -> L=4096
F009_CONTAINERS = (124, 12)  # L=1 -> L=4096 컨테이너 개수
P2_THRESHOLD = 1.5         # 이 아래면 "개수는 주범이 아니다"


def tag(occ):
    return "px%sp%s" % (BP, occ)


def gen(occ):
    path = os.path.join(R, "gen_%s.json" % tag(occ))
    if not os.path.exists(path):
        return None
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return None


def containers(occ):
    path = os.path.join(R, "containers_%s.csv" % tag(occ))
    if not os.path.exists(path):
        return None
    rows = list(csv.DictReader(io.open(path, encoding="utf-8")))
    if not rows:
        return None
    kinds = {}
    for r in rows:
        k = r.get("container", "?")
        kinds[k] = kinds.get(k, 0) + 1

    def f(r, n):
        try:
            return float(r.get(n) or 0)
        except ValueError:
            return 0.0

    cards = [f(r, "cardinality") for r in rows]
    return dict(n=len(rows), kinds=kinds,
                card=st.median(cards) if cards else 0,
                bytes=sum(f(r, "bytes") for r in rows))


def cell(arm, occ):
    """반복 라운드에 걸친 DV 샘플 중앙값과 범위. 규칙: 범위가 겹치면 차이를 주장하지 않는다."""
    dv, sc = [], []
    for path in sorted(glob.glob(os.path.join(
            R, "profiles", "%s__%s_c*_r*.collapsed" % (arm, tag(occ))))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        dv.append(a["dv_union_samples"])
        sc.append(a["scan_samples"])
    walls = []
    for path in sorted(glob.glob(os.path.join(R, "scan_%s__%s_r*.json" % (arm, tag(occ))))):
        try:
            d = json.load(io.open(path, encoding="utf-8"))
            if d.get("median_s"):
                walls.append(float(d["median_s"]))
        except Exception:
            pass
    if not dv:
        return None
    return dict(n=len(dv), dv=st.median(dv), dv_rng=(min(dv), max(dv)),
                scan=st.median(sc) if sc else 0,
                wall=st.median(walls) if walls else None)


p("=" * 104)
p("`p` 축 — 컨테이너 '개수' 만 줄이면 비용이 주는가 (d=%s bp 고정, 타입은 array 유지)" % BP)
p("=" * 104)

# ── P1. 설계 검증 ───────────────────────────────────────────────────────────
p("")
p("─ P1 [설계 검증]  p 를 바꾸면 진짜로 서로 다른 테이블이 나오는가")
p("   (F-010 은 여기서 죽었다: p=5% 와 p=10% 가 완전히 같은 테이블이었다)")
p("")
p("%-8s %14s %12s %10s %14s %12s" %
  ("p", "삭제 행", "인접 대비", "컨테이너", "합계 바이트", "청크/파일"))

gens, cinfo = {}, {}
prev_del = None
p1_distinct, p1_monotone = True, True
prev_n = None
for occ in OCCS:
    g, c = gen(occ), containers(occ)
    gens[occ], cinfo[occ] = g, c
    if not g:
        p("%-8s (gen json 없음)" % ("p" + occ)); p1_distinct = False; continue
    d = g["deleted_rows"]
    delta = "-" if prev_del is None else "%+.1f%%" % ((d / prev_del - 1) * 100)
    if prev_del is not None and abs(d / prev_del - 1) < 0.05:
        p1_distinct = False
    n = c["n"] if c else 0
    if prev_n is not None and n >= prev_n:
        p1_monotone = False
    p("%-8s %14s %12s %10s %14s %12.1f" %
      ("p" + occ, f"{d:,}", delta, n if c else "?",
       f"{int(c['bytes']):,}" if c else "?", g.get("chunks_per_file", 0)))
    prev_del, prev_n = d, n

p("")
p("   인접 p 끼리 삭제 행 5%% 이상 차이: %s" % ("예" if p1_distinct else "★아니오"))
p("   컨테이너 개수 단조 감소: %s" % ("예" if p1_monotone else "★아니오"))
if p1_distinct and p1_monotone:
    p("   판정: **맞음** — 설계가 산다. 아래를 채점한다.")
else:
    p("   판정: ★빗나감 — F-010 의 해상도 문제가 재발했다. 여기서 멈춘다.")
    p("=" * 104)
    sys.exit(0)

# ── P3. 구조 확인 (P2 를 읽기 전에 전제를 확인한다) ─────────────────────────
p("")
p("─ P3 [전제 확인]  컨테이너 타입이 array 로 유지되는가 (안 그러면 P2 는 개수 축이 아니다)")
p("%-8s %-30s %12s" % ("p", "컨테이너", "중앙값 card"))
all_array = True
for occ in OCCS:
    c = cinfo.get(occ)
    if not c:
        p("%-8s (CSV 없음)" % ("p" + occ)); all_array = False; continue
    if any(not k.startswith("array") for k in c["kinds"]):
        all_array = False
    p("%-8s %-30s %12.0f" %
      ("p" + occ, ", ".join("%s:%d" % kv for kv in sorted(c["kinds"].items())), c["card"]))
p("   판정: %s" % ("맞음 — 전부 array. 바뀐 건 개수뿐이다."
                  if all_array else
                  "★빗나감 — 타입이 섞였다. P2 는 순수한 '개수' 축이 아니다."))

# ── P2. 판정용 ──────────────────────────────────────────────────────────────
p("")
p("─ P2 ★판정용★  개수만 줄이면 비용이 %.1f배 미만으로만 싸지는가" % P2_THRESHOLD)
p("%-8s %3s %10s %18s %10s %10s" % ("p", "n", "DV 샘플", "범위", "DV/스캔", "wall(s)"))
base = {}
for occ in OCCS:
    c = cell("baseline", occ)
    base[occ] = c
    if not c:
        p("%-8s (프로파일 없음)" % ("p" + occ)); continue
    p("%-8s %3d %10.0f %8.0f ~ %-8.0f %9.1f%% %10s"
      % ("p" + occ, c["n"], c["dv"], c["dv_rng"][0], c["dv_rng"][1],
         c["dv"] / c["scan"] * 100 if c["scan"] else 0,
         ("%.4f" % c["wall"]) if c["wall"] else "-"))

lo, hi = OCCS[0], OCCS[-1]   # lo = p 가 큰 쪽(컨테이너 많음), hi = p 가 작은 쪽
p("")
if base.get(lo) and base.get(hi):
    a, b = base[lo], base[hi]
    gain = a["dv"] / b["dv"] if b["dv"] else 0
    ov = not (a["dv_rng"][0] > b["dv_rng"][1] or b["dv_rng"][0] > a["dv_rng"][1])
    nlo = cinfo[lo]["n"] if cinfo.get(lo) else "?"
    nhi = cinfo[hi]["n"] if cinfo.get(hi) else "?"
    p("   p%s → p%s : 컨테이너 %s개 → %s개,  DV 샘플 %.0f → %.0f = **%.2f배**"
      % (lo, hi, nlo, nhi, a["dv"], b["dv"], gain))
    p("   범위 겹침: %s" % ("예" if ov else "아니오"))
    if ov:
        p("   판정: 판정 불가 — 범위가 겹친다. 규칙대로 차이를 주장하지 않는다.")
        p("         (그래도 '개수 효과가 크지 않다' 쪽 증거이긴 하다 — 크면 겹칠 리가 없다)")
    elif gain < P2_THRESHOLD:
        p("   판정: **맞음** — 개수를 10배 줄여도 %.2f배뿐이다." % gain)
        p("         F-009 의 %.2f배는 대부분 **run 구조** 덕이다. 처방의 근거가 산다."
          % F009_COST_GAIN)
    else:
        p("   판정: ★빗나감 — %.2f배다. 개수 효과가 작지 않다." % gain)
        p("         F-009 의 %.2f배를 'run 구조 덕' 으로 읽던 해석을 다시 써야 한다."
          % F009_COST_GAIN)

    # F-009 와의 분해. 두 축의 컨테이너 감소 배율이 다르면 그대로 비교하면 안 된다.
    if cinfo.get(lo) and cinfo.get(hi) and gain:
        px_ratio = cinfo[lo]["n"] / max(cinfo[hi]["n"], 1)
        f9_ratio = F009_CONTAINERS[0] / F009_CONTAINERS[1]
        p("")
        p("   ─ F-009 와 나란히 (같은 d=0.5%)")
        p("     %-28s %14s %12s" % ("", "컨테이너 감소", "비용 이득"))
        p("     %-28s %13.1f배 %11.2f배" % ("F-009  L=1 → L=4096 (구조+개수)", f9_ratio, F009_COST_GAIN))
        p("     %-28s %13.1f배 %11.2f배" % ("이 축   p 만 (개수만)", px_ratio, gain))
        if gain > 0:
            p("     개수 효과를 뺀 나머지(구조 몫) ≈ %.2f배" % (F009_COST_GAIN / gain))
        if abs(px_ratio - f9_ratio) / f9_ratio > 0.25:
            p("     ⚠️ 두 축의 컨테이너 감소 배율이 %.1f배 vs %.1f배로 다르다. "
              "나눗셈은 어림값이지 정확한 분해가 아니다." % (f9_ratio, px_ratio))

p("=" * 104)
