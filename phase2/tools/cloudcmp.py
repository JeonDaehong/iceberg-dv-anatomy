#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
클라우드 vs 로컬 — cloud/bootstrap.sh 헤더의 P1~P4 를 채점한다.

예측은 저기 있고 여기 없다. 여기 다시 적으면 사후에 어느 쪽을 고쳤는지 알 수 없다.

태그 주의:
  로컬은 c=1 을 태그 접미사 없이 쓴다 (d610). 06-widescan 은 폭을 항상 붙이므로
  클라우드는 같은 조건이 d610w1 이다. 이걸 안 맞추면 c=1 이 조용히 빠진 채
  "c=20 만 비교했다" 가 된다 — 0초 완료를 성공으로 읽는 것과 같은 종류의 사고다.

사용:
  python3 tools/cloudcmp.py <로컬 results> <클라우드 results> [클라우드이름]
"""
import glob
import json
import math
import os
import re
import statistics as st
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

DENS = [("d50", "0.5%"), ("d610", "6.1%"), ("d700", "7.0%")]
WIDTHS = [1, 20]
NOISE_FLOOR = 9.7   # F-010 (로컬)
K_WALL = 0.46       # F-018
P1_BAND = 10.0      # cloud/bootstrap.sh P1 — DV 비중 허용 차이 (%p)
P4_BAND = 5.0       # P4 — 0.46 관계식 허용 오차 (%p)


def pad(s, w):
    n = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)
    return s + " " * max(0, w - n)


def tag_local(dens, c):
    return dens if c == 1 else "%sw%d" % (dens, c)


def tag_cloud(dens, c):
    return "%sw%d" % (dens, c)


def profiles(results, arm, tag, c):
    out = {}
    pat = os.path.join(results, "profiles", "%s__%s_c%d_r*.collapsed" % (arm, tag, c))
    for path in sorted(glob.glob(pat)):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        r = int(re.search(r"_r(\d+)\.collapsed$", path).group(1))
        out[r] = {"dv": a["dv_union_samples"], "scan": a["scan_samples"],
                  "pct": a["dv_pct_of_scan"]}
    return out


def walls(results, arm, tag):
    out = {}
    for path in glob.glob(os.path.join(results, "scan_%s__%s_r*.json" % (arm, tag))):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if d.get("median_s"):
            out[int(re.search(r"_r(\d+)\.json$", path).group(1))] = float(d["median_s"])
    return out


def sign_p(k, n):
    if n == 0:
        return 1.0
    k = max(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / (2.0 ** n))


def cell(results, tagfn, dens, c):
    tag = tagfn(dens, c)
    b = profiles(results, "baseline", tag, c)
    q = profiles(results, "patched", tag, c)
    if not b or not q:
        return None
    wb, wq = walls(results, "baseline", tag), walls(results, "patched", tag)
    reps = sorted(set(wb) & set(wq))
    deltas = [(wq[r] / wb[r] - 1) * 100 for r in reps]
    bd = [v["dv"] for v in b.values()]
    qd = [v["dv"] for v in q.values()]
    bs = [v["scan"] for v in b.values()]
    qs = [v["scan"] for v in q.values()]
    bw = [wb[r] for r in reps]
    return dict(
        tag=tag, n=len(bd),
        dv_b=st.mean(bd), dv_q=st.mean(qd),
        share=st.mean(v["pct"] for v in b.values()),
        speedup=st.mean(bd) / st.mean(qd) if st.mean(qd) else float("inf"),
        scan_cut=(st.mean(bs) - st.mean(qs)) / st.mean(bs) * 100,
        deltas=deltas,
        wall_med=st.median(deltas) if deltas else None,
        slower=sum(1 for x in deltas if x > 0),
        # 같은 arm 을 반복했을 때의 흩어짐 = 그 환경의 노이즈 대리 지표
        spread=(max(bw) - min(bw)) / st.median(bw) * 100 if len(bw) >= 3 else None,
    )


def h(t):
    print("\n" + "=" * 100)
    print(" " + t)
    print("=" * 100)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    LOC, CLD = sys.argv[1], sys.argv[2]
    NAME = sys.argv[3] if len(sys.argv) > 3 else "cloud"

    rows = []
    for dens, dl in DENS:
        for c in WIDTHS:
            l = cell(LOC, tag_local, dens, c)
            k = cell(CLD, tag_cloud, dens, c)
            if l or k:
                rows.append((dens, dl, c, l, k))

    if not any(k for _, _, _, _, k in rows):
        print("클라우드 결과가 없습니다: %s" % CLD)
        return

    # ---------- 1. 나란히 ----------
    h("로컬(Zen 3 / WSL2) vs %s — 같은 jar, 같은 테이블, 같은 설정" % NAME)
    print("  %-7s %3s %-9s %7s %9s %9s %9s %8s %8s"
          % ("d", "c", "환경", "n", "DV base", "DV patch", "배율", "DV비중", "wall"))
    print("  " + "-" * 96)
    for dens, dl, c, l, k in rows:
        for envname, r in (("로컬", l), (NAME, k)):
            if not r:
                continue
            w = ("%+.1f%%" % r["wall_med"]) if r["wall_med"] is not None else "—"
            print("  %-7s %3d %s %7d %9.0f %9.0f %8.2f배 %7.2f%% %8s"
                  % (dl, c, pad(envname, 9), r["n"], r["dv_b"], r["dv_q"],
                     r["speedup"], r["share"], w))
        print("  " + "·" * 96)

    # ---------- 2. P1 ----------
    h("P1 채점 — DV 비중이 로컬과 ±%.0f%%p 안에 드는가 (판정용)" % P1_BAND)
    print("  이게 벗어나면 '스캔 CPU 의 43~53%' 는 환경 독립 수치가 아니다.\n")
    print("  %-7s %3s %11s %11s %10s  %s" % ("d", "c", "로컬 비중", "%s 비중" % NAME, "차이", "판정"))
    print("  " + "-" * 96)
    p1 = []
    for dens, dl, c, l, k in rows:
        if not (l and k):
            continue
        d = k["share"] - l["share"]
        ok = abs(d) <= P1_BAND
        p1.append(ok)
        print("  %-7s %3d %10.2f%% %10.2f%% %+9.1fp  %s"
              % (dl, c, l["share"], k["share"], d, "안에 듦 ✅" if ok else "벗어남 ❌"))
    if p1:
        print("\n  -> %d/%d 셀 통과. %s" % (sum(p1), len(p1),
              "비중은 CPU 가 바뀌어도 유지된다." if all(p1)
              else "일부가 벗어났다 — CPU 조건을 명시해야 한다."))

    # ---------- 3. P2 ----------
    h("P2 채점 — 패치의 DV 개선 배율이 6~9배를 유지하는가 (array 설정)")
    print("  %-7s %3s %11s %11s  %s" % ("d", "c", "로컬 배율", "%s 배율" % NAME, "판정"))
    print("  " + "-" * 96)
    for dens, dl, c, l, k in rows:
        if not (l and k):
            continue
        note = ""
        if dens in ("d50", "d610"):
            note = "6~9배 유지 ✅" if 6.0 <= k["speedup"] <= 9.5 else "범위 밖 ⚠️"
        else:
            note = "bitmap 설정 — 로컬도 3배대"
        print("  %-7s %3d %10.2f배 %10.2f배  %s" % (dl, c, l["speedup"], k["speedup"], note))

    # ---------- 4. P3 ----------
    h("P3 채점 — %s 의 노이즈가 로컬(9.7%%)보다 나쁜가" % NAME)
    print("  baseline arm 을 반복했을 때의 (최대-최소)/중앙값. 그 환경의 흔들림 대리 지표다.\n")
    print("  %-7s %3s %13s %13s  %s" % ("d", "c", "로컬 흩어짐", "%s 흩어짐" % NAME, "판정"))
    print("  " + "-" * 96)
    lsp, ksp = [], []
    for dens, dl, c, l, k in rows:
        if not (l and k and l["spread"] and k["spread"]):
            continue
        lsp.append(l["spread"]); ksp.append(k["spread"])
        print("  %-7s %3d %12.1f%% %12.1f%%  %s"
              % (dl, c, l["spread"], k["spread"],
                 "클라우드가 더 흔들림" if k["spread"] > l["spread"] else "클라우드가 더 조용함"))
    if lsp:
        print("\n  중앙값: 로컬 %.1f%% vs %s %.1f%%" % (st.median(lsp), NAME, st.median(ksp)))
        print("  -> %s" % ("P3 적중 — 공유 하드웨어가 더 시끄럽다." if st.median(ksp) > st.median(lsp)
                          else "P3 빗나감 — 전용 인스턴스가 WSL2 보다 조용하다. "
                               "로컬 노이즈의 범인이 클라우드가 아니라 WSL2 였다는 뜻이다."))

    # ---------- 5. P4 ----------
    h("P4 채점 — F-018 의 'wall 단축 = %.2f × DV 비중' 이 %s 에서도 성립하는가" % (K_WALL, NAME))
    print("  F-019 의 콜드는 비중이 안 움직여 약한 시험이었다. 여기서는 CPU 가 바뀌어")
    print("  비중과 wall 이 함께 움직인다 — 첫 제대로 된 out-of-sample 시험이다.\n")
    print("  %-7s %3s %10s %12s %12s %9s %6s  %s"
          % ("d", "c", "DV비중", "예상 wall", "실측 wall", "차이", "부호", "판정"))
    print("  " + "-" * 96)
    hits = []
    for dens, dl, c, l, k in rows:
        if not k or k["wall_med"] is None:
            continue
        pred = -K_WALL * k["share"]
        med = k["wall_med"]
        ok = abs(med - pred) <= P4_BAND
        hits.append(ok)
        print("  %-7s %3d %9.2f%% %11.1f%% %11.1f%% %+8.1fp %3d/%-2d  %s"
              % (dl, c, k["share"], pred, med, med - pred,
                 k["slower"], len(k["deltas"]), "안에 듦 ✅" if ok else "벗어남 ❌"))
    if hits:
        print("\n  -> %d/%d 셀 통과. %s" % (sum(hits), len(hits),
              "0.46 이 CPU 를 바꿔도 산다." if all(hits)
              else "일부가 벗어났다 — 0.46 은 그 머신의 상수일 수 있다."))

    print("\n  라운드별 wall-clock (%s):" % NAME)
    for dens, dl, c, l, k in rows:
        if not k or not k["deltas"]:
            continue
        p = sign_p(k["slower"], len(k["deltas"]))
        claim = abs(k["wall_med"]) > NOISE_FLOOR and p <= 0.05
        print("    %-6s c=%-3d %-38s 중앙 %+6.1f%%  p=%.2f  %s"
              % (dl, c, " ".join("%+.1f" % x for x in k["deltas"])[:38],
                 k["wall_med"], p, "주장 가능" if claim else "주장 불가"))

    out = os.path.join(CLD, "cloudcmp.json")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"name": NAME, "rows": [
                {"dens": d, "c": c, "local": l, "cloud": k} for d, _, c, l, k in rows]},
                fh, indent=2, ensure_ascii=False)
        print("\n  -> %s" % out)
    except Exception as e:
        print("\n  (json 저장 실패: %s)" % e)


if __name__ == "__main__":
    main()
