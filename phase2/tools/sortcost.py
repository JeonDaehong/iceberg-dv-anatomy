#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""정렬 쓰기 비용 채점. 예측 W1~W3 은 scripts/14-sortcost.sh 헤더에 있고 여기 복제하지 않는다."""
import io, json, os, sys, statistics as st

OUT = io.open(1, "w", encoding="utf-8", closefd=False)
def p(*a): OUT.write(" ".join(str(x) for x in a) + "\n")

R = sys.argv[1] if len(sys.argv) > 1 else "results"
# F-023 에서 측정된 정렬 테이블의 스캔 wall 절감률. 여기서 다시 재지 않고 가져다 쓴다.
SCAN_GAIN = float(os.environ.get("SCAN_GAIN", "0.205"))

arms = {"sc_uns": [], "sc_srt": []}
for fn in sorted(os.listdir(R)):
    if not fn.startswith("sortcost_") or not fn.endswith(".json"): continue
    tag = fn[len("sortcost_"):].rsplit("_r", 1)[0]
    if tag not in arms: continue
    d = json.load(open(os.path.join(R, fn), encoding="utf-8"))
    if d.get("write_secs"): arms[tag].append(d)

if not arms["sc_uns"] or not arms["sc_srt"]:
    p("결과가 부족하다: uns=%d srt=%d" % (len(arms["sc_uns"]), len(arms["sc_srt"]))); sys.exit(1)

def med(xs): return st.median(xs)
def rng(xs): return (min(xs), max(xs))

u = [d["write_secs"] for d in arms["sc_uns"]]
s = [d["write_secs"] for d in arms["sc_srt"]]
ub = [d["data_bytes"] for d in arms["sc_uns"]]
sb = [d["data_bytes"] for d in arms["sc_srt"]]
rows = arms["sc_uns"][0]["total_rows"]

p("=" * 92)
p("정렬 쓰기 비용 — 처방의 값은 얼마인가")
p("=" * 92)
p("행 %s · 파일 %d개 · d=%.2f%% · 라운드 uns %d / srt %d"
  % (f"{rows:,}", arms['sc_uns'][0]['num_files'],
     arms['sc_uns'][0]['actual_density']*100, len(u), len(s)))
p("")
p("%-10s %10s %22s %14s" % ("arm", "중앙값", "범위", "데이터 바이트"))
p("%-10s %9.2fs %10.2f ~ %-9.2f %14s" % ("무정렬", med(u), rng(u)[0], rng(u)[1], f"{med(ub):,.0f}"))
p("%-10s %9.2fs %10.2f ~ %-9.2f %14s" % ("정렬",   med(s), rng(s)[0], rng(s)[1], f"{med(sb):,.0f}"))

lo_u, hi_u = rng(u); lo_s, hi_s = rng(s)
overlap = not (hi_u < lo_s or hi_s < lo_u)
delta = med(s) - med(u)
ratio = med(s) / med(u) if med(u) else float("nan")

p("")
p("─ W1  정렬 쓰기가 더 비싼가 (+50~200% 예측)")
p("   차이 %+.2f초  (%.2f배, %+.0f%%)" % (delta, ratio, (ratio-1)*100))
p("   행당 추가 %+.3f µs   ← 소스 생성 비용이 양쪽에 같이 들어가므로 이쪽이 일반화된다"
  % (delta / rows * 1e6))
if overlap:
    p("   범위가 겹친다 (%.2f~%.2f vs %.2f~%.2f) → **차이를 주장하지 않는다**"
      % (lo_u, hi_u, lo_s, hi_s))
    verdict1 = "판정 불가 — 범위 겹침"
else:
    inband = 0.50 <= (ratio - 1) <= 2.00
    verdict1 = ("맞음" if inband else
                "빗나감 — 예측 구간(+50~200%%) 밖이다 (%.0f%%)" % ((ratio-1)*100))
    p("   범위가 안 겹친다 (%.2f~%.2f vs %.2f~%.2f)" % (lo_u, hi_u, lo_s, hi_s))
p("   판정: %s" % verdict1)

p("")
p("─ W3  정렬 테이블의 데이터 파일이 더 작은가 (Parquet 인코딩 이득)")
br = med(sb) / med(ub) if med(ub) else float("nan")
p("   %s B → %s B  (%.4f배, %+.2f%%)" % (f"{med(ub):,.0f}", f"{med(sb):,.0f}", br, (br-1)*100))
if br < 0.98:
    p("   판정: 맞음 — %.1f%% 작아졌다. 읽을 바이트가 주는 것은 DV 와 무관한 추가 이득이다." % ((1-br)*100))
elif br > 1.02:
    p("   판정: ★빗나감 — 오히려 커졌다. 같은 데이터인데 커질 이유가 없으므로 통제를 의심할 것.")
else:
    p("   판정: 빗나감 — 사실상 그대로다(±2%). 인코딩 이득은 없다.")

p("")
p("─ W2 ★판정용★  몇 번 읽어야 본전인가 (10회 미만이면 처방이 남는 장사)")
p("   F-023 의 정렬 스캔 wall 절감률 %.1f%% 를 가져다 쓴다 (여기서 다시 재지 않는다)." % (SCAN_GAIN*100))
# ⚠️ 라운드 하나만 쓰면 오염된 라운드에 끌려간다 (실제로 r1 이 다른 라운드의 1.5배였다).
#    프로젝트 규칙대로 전 라운드의 **중앙값**을 쓴다.
import glob as _g
_walls = []
for _f in sorted(_g.glob(os.path.join(R, "scan_baseline__layunsc1_r*.json"))):
    try:
        _d = json.load(open(_f, encoding="utf-8"))
        if _d.get("median_s"): _walls.append(float(_d["median_s"]))
    except Exception:
        pass
scan_wall = med(_walls) if _walls else None
if _walls:
    p("   무정렬 스캔: %d라운드 중앙값 %.4f초  (범위 %.4f~%.4f)"
      % (len(_walls), scan_wall, min(_walls), max(_walls)))
if scan_wall:
    save = scan_wall * SCAN_GAIN
    p("   → 정렬 시 스캔 1회당 %.4f초 절감" % save)
    if delta <= 0:
        p("   추가 쓰기 비용이 0 이하다 → 즉시 본전. 판정: 맞음")
    else:
        n = delta / save
        p("   본전 회수 = %.2f초 / %.4f초 = **%.0f회**" % (delta, save, n))
        if n < 10:   p("   판정: 맞음 — %.0f회면 한 번 쓰고 여러 번 읽는 테이블에서 확실히 남는다." % n)
        elif n < 100:p("   판정: 빗나감(방향은 무해) — %.0f회. 처방에 '읽기가 꽤 많을 때' 조건이 붙는다." % n)
        else:        p("   판정: ★빗나감 — %.0f회. 처방에 '읽기가 아주 많을 때만' 이라는 강한 조건이 필요하다." % n)
else:
    p("   ⚠️ %s 를 못 찾아 스캔 1회 시간을 모른다. SCAN_WALL 을 주면 계산한다." % scan_file)
    sw = os.environ.get("SCAN_WALL")
    if sw:
        scan_wall = float(sw); save = scan_wall*SCAN_GAIN
        n = delta/save if save else float("nan")
        p("   (SCAN_WALL=%.3f초 사용) 본전 회수 **%.0f회**" % (scan_wall, n))
p("=" * 92)
