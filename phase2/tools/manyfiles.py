#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""파일 수 축 채점. 예측 M1~M4 는 scripts/20-manyfiles.sh 헤더에 있고 여기 없다."""
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
BP = os.environ.get("MF_DENSITY_BP", "610")
LAYOUTS = (os.environ.get("MF_TAGS") or "f4 f488").split()
STORES = (os.environ.get("MF_STORES") or "ebs").split()
M1_TOL = 0.10   # 컨테이너 개수·바이트 허용 오차
M2_TOL = 5.0    # DV/스캔 비중 허용 오차 (%p)


def containers(tag):
    path = os.path.join(R, "containers_mf%s%s.csv" % (BP, tag))
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
    return dict(n=len(rows), kinds=kinds, bytes=sum(f(r, "bytes") for r in rows),
                card=st.median(cards) if cards else 0)


def gen(tag):
    path = os.path.join(R, "gen_mf%s%s.json" % (BP, tag))
    if not os.path.exists(path):
        return None
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return None


def cell(arm, tag, store):
    t = "mf%s%s%s" % (BP, tag, store)
    dv, sc, tot = [], [], []
    for path in sorted(glob.glob(os.path.join(
            R, "profiles", "%s__%s_c*_r*.collapsed" % (arm, t)))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        dv.append(a["dv_union_samples"])
        sc.append(a["scan_samples"])
        tot.append(a["total_samples"])
    walls = []
    for path in sorted(glob.glob(os.path.join(R, "scan_%s__%s_r*.json" % (arm, t)))):
        try:
            d = json.load(io.open(path, encoding="utf-8"))
            if d.get("median_s"):
                walls.append(float(d["median_s"]))
        except Exception:
            pass
    if not dv:
        return None
    return dict(n=len(dv), dv=st.median(dv), dv_rng=(min(dv), max(dv)),
                scan=st.median(sc), total=st.median(tot),
                wall=st.median(walls) if walls else None)


p("=" * 104)
p("파일 수 축 — 파일이 122배 많아지면 DV 비중이 흔들리는가 (d=%s bp)" % BP)
p("=" * 104)

p("")
p("─ M1 [설계 검증]  두 테이블의 컨테이너가 같은가 (다르면 파일 수 축이 아니다)")
p("%-8s %10s %10s %8s %-26s %14s" % ("레이아웃", "파일", "행", "컨테이너", "타입", "합계 바이트"))
cinfo, ginfo = {}, {}
for tag in LAYOUTS:
    c, g = containers(tag), gen(tag)
    cinfo[tag], ginfo[tag] = c, g
    if not c or not g:
        p("%-8s (%s 없음)" % (tag, "컨테이너 CSV" if not c else "gen json")); continue
    p("%-8s %10d %10s %8d %-26s %14s"
      % (tag, g["num_files"], f"{g['total_rows']:,}", c["n"],
         ", ".join("%s:%d" % kv for kv in sorted(c["kinds"].items())),
         f"{int(c['bytes']):,}"))

m1 = False
if all(cinfo.get(t) for t in LAYOUTS) and len(LAYOUTS) == 2:
    a, b = cinfo[LAYOUTS[0]], cinfo[LAYOUTS[1]]
    dn = abs(a["n"] - b["n"]) / max(a["n"], 1)
    db = abs(a["bytes"] - b["bytes"]) / max(a["bytes"], 1)
    dom = lambda c: max(c["kinds"].items(), key=lambda kv: kv[1])[0]
    same_type = dom(a) == dom(b)
    m1 = dn <= M1_TOL and db <= M1_TOL and same_type
    p("   개수 차 %.1f%%  바이트 차 %.1f%%  우세 타입 %s / %s"
      % (dn * 100, db * 100, dom(a), dom(b)))
    ga, gb = ginfo.get(LAYOUTS[0]), ginfo.get(LAYOUTS[1])
    if ga and gb:
        p("   삭제 행 %s vs %s (%.2f%% 차)"
          % (f"{ga['deleted_rows']:,}", f"{gb['deleted_rows']:,}",
             abs(ga["deleted_rows"] / gb["deleted_rows"] - 1) * 100))
    p("   판정: %s" % ("**맞음** — 컨테이너가 같다. 파일 수만 다르다."
                      if m1 else
                      "★빗나감 — 컨테이너가 다르다. 이건 파일 수 축이 아니다. 여기서 멈춘다."))
if not m1:
    p("=" * 104)
    sys.exit(0)

for store in STORES:
    p("")
    p("─ M2 ★판정용★ / M3 / M4   스토리지=%s" % store)
    p("%-10s %-9s %3s %9s %16s %9s %9s %10s"
      % ("레이아웃", "arm", "n", "DV", "범위", "DV/스캔", "DV/전체", "wall(s)"))
    got = {}
    for tag in LAYOUTS:
        for arm in ("baseline", "patched"):
            c = cell(arm, tag, store)
            got[(tag, arm)] = c
            if not c:
                p("%-10s %-9s (프로파일 없음)" % (tag, arm)); continue
            p("%-10s %-9s %3d %9.0f %7.0f ~ %-7.0f %8.1f%% %8.1f%% %10s"
              % (tag, arm, c["n"], c["dv"], c["dv_rng"][0], c["dv_rng"][1],
                 c["dv"] / c["scan"] * 100 if c["scan"] else 0,
                 c["dv"] / c["total"] * 100 if c["total"] else 0,
                 ("%.4f" % c["wall"]) if c["wall"] else "-"))

    a = got.get((LAYOUTS[0], "baseline"))
    b = got.get((LAYOUTS[1], "baseline"))
    if a and b:
        sa = a["dv"] / a["scan"] * 100
        sb = b["dv"] / b["scan"] * 100
        p("")
        p("   M2  DV/스캔 %.1f%% → %.1f%%  (차 %+.1f%%p, 문턱 ±%.0f%%p)"
          % (sa, sb, sb - sa, M2_TOL))
        p("       판정: %s" % ("**맞음** — 파일 수는 스캔 내 비중을 안 바꾼다. "
                              "F-021 의 논리(ctimer 는 CPU 시간, 요청 지연은 대기)가 파일 수에도 산다."
                              if abs(sb - sa) <= M2_TOL else
                              "★빗나감 — 비중이 %+.1f%%p 움직였다. 파일 핸들링 CPU 가 분모를 바꾼다."
                              % (sb - sa)))
        if a["wall"] and b["wall"]:
            p("   M3  wall %.4f → %.4f = **%.2f배**" % (a["wall"], b["wall"], b["wall"] / a["wall"]))
            p("       판정: %s" % ("맞음 — 파일이 많으면 느리다"
                                  if b["wall"] > a["wall"] else
                                  "빗나감 — 오히려 빠르거나 같다"))
        ta = a["dv"] / a["total"] * 100
        tb = b["dv"] / b["total"] * 100
        p("   M4  DV/전체 %.1f%% → %.1f%% (%+.1f%%p)" % (ta, tb, tb - ta))
        p("       판정: %s" % ("맞음 — 스캔 밖(계획·스케줄링)이 커져 전체 대비 비중은 떨어진다"
                              if tb < ta else
                              "빗나감 — 전체 대비 비중이 안 떨어졌다"))

    for tag in LAYOUTS:
        x, y = got.get((tag, "baseline")), got.get((tag, "patched"))
        if x and y and y["dv"]:
            p("   덤  %-6s baseline %6.0f → patched %6.0f = %5.2f배" % (tag, x["dv"], y["dv"], x["dv"] / y["dv"]))

p("=" * 104)
