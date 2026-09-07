#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
정렬 키 카디널리티 축 — 13-sortkey.sh 헤더의 S_C1~S_C3 을 채점한다.

⚠️ 이 축은 술어를 각 정렬 키에 걸므로 **삭제되는 행이 축마다 다르다.**
   DV 체크는 삭제 여부와 무관하게 행마다 1회 프로브하므로 총 행 수(800만)가 같으면
   DV 샘플 비교 자체는 성립한다. 그러나 삭제 '개수' 가 크게 다르면 청크 카디널리티가
   달라져 컨테이너 타입이 독립적으로 바뀐다(F-008 의 톱니). 그래서 삭제 행 수를
   먼저 찍고, 5% 넘게 어긋난 축은 표시한다.
"""
import glob
import json
import math
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

# 이름, 표시, 정렬 키의 대략적 고유값
AXES = [("uns", "무정렬", None),
        ("c1k", "k04 정렬", "~1,000"),
        ("c1m", "k15 정렬", "~100만"),
        ("c8m", "k01 정렬", "~10억")]
NOISE_FLOOR = 9.7


def cell(results, arm, name):
    tag = "sk%s" % name
    pr = {}
    for path in sorted(glob.glob(os.path.join(
            results, "profiles", "%s__%s_c1_r*.collapsed" % (arm, tag)))):
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        pr[int(re.search(r"_r(\d+)\.collapsed$", path).group(1))] = a
    if not pr:
        return None
    w = {}
    for path in glob.glob(os.path.join(results, "scan_%s__%s_r*.json" % (arm, tag))):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if d.get("median_s"):
            w[int(re.search(r"_r(\d+)\.json$", path).group(1))] = float(d["median_s"])
    return dict(
        n=len(pr),
        dv=st.mean(a["dv_union_samples"] for a in pr.values()),
        scan=st.mean(a["scan_samples"] for a in pr.values()),
        share=st.mean(a["dv_pct_of_scan"] for a in pr.values()),
        rng=(min(a["dv_union_samples"] for a in pr.values()),
             max(a["dv_union_samples"] for a in pr.values())),
        wall=w,
    )


def gen(results, name):
    p = os.path.join(results, "gen_sk_%s.json" % name)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def sign_p(k, n):
    if n == 0:
        return 1.0
    k = max(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / (2.0 ** n))


def h(t):
    print("\n" + "=" * 96)
    print(" " + t)
    print("=" * 96)


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    B = {n: cell(results, "baseline", n) for n, _, _ in AXES}
    P = {n: cell(results, "patched", n) for n, _, _ in AXES}
    G = {n: gen(results, n) for n, _, _ in AXES}
    B = {k: v for k, v in B.items() if v}
    if not B:
        print("결과가 없습니다: %s" % results)
        return

    # ---------- 통제 확인 먼저 ----------
    h("통제 확인 — 축마다 삭제되는 행이 다르다 (술어를 각 정렬 키에 걸기 때문)")
    print("  DV 체크는 삭제 여부와 무관하게 행마다 1회 프로브하므로 총 행 수가 같으면")
    print("  DV 샘플 비교는 성립한다. 다만 삭제 개수가 크게 다르면 청크 카디널리티가")
    print("  달라져 컨테이너 타입이 독립적으로 바뀐다(F-008 톱니).\n")
    print("  %-10s %14s %11s %14s %14s" % ("축", "삭제 행", "실제 밀도", "DV 바이트", "삭제행당 bit"))
    print("  " + "-" * 92)
    base_del = None
    warn = []
    for name, lab, card in AXES:
        g = G.get(name)
        if not g:
            continue
        if base_del is None:
            base_del = g["deleted_rows"]
        rel = abs(g["deleted_rows"] - base_del) / max(base_del, 1) * 100
        if rel > 5.0:
            warn.append(lab)
        print("  %-10s %14s %10.3f%% %14s %13.4f%s"
              % (lab, f"{g['deleted_rows']:,}", g["actual_density"] * 100,
                 f"{g['dv_bytes']:,}", g["dv_bytes"] * 8 / max(g["deleted_rows"], 1),
                 "  ⚠️" if rel > 5.0 else ""))
    if warn:
        print("\n  ⚠️ 삭제 행 수가 무정렬과 5%% 넘게 다른 축: %s" % ", ".join(warn))
        print("     이 축들은 컨테이너 카디널리티 효과가 섞여 있다. 해석에 단서를 달 것.")
    else:
        print("\n  ✅ 삭제 행 수가 모든 축에서 5%% 안. 클러스터링 효과만 남는다.")

    # ---------- 표 ----------
    h("정렬 키 카디널리티 — 고유값이 많을수록 정렬이 덜 뭉친다")
    print("  %-10s %10s %3s %10s %11s %10s %10s"
          % ("축", "고유값", "n", "DV 샘플", "스캔 샘플", "DV 비중", "wall"))
    print("  " + "-" * 92)
    for name, lab, card in AXES:
        r = B.get(name)
        if not r:
            continue
        wm = st.median(r["wall"].values()) if r["wall"] else float("nan")
        print("  %-10s %10s %3d %10.0f %11.0f %9.2f%% %9.3fs"
              % (lab, card or "—", r["n"], r["dv"], r["scan"], r["share"], wm))

    # ---------- S_C1 ----------
    h("S_C1 채점 [판정용] — 이득이 고유값 수에 따라 사라지는가")
    print("  예측: k04(~1천) ≈ 2.8배(F-023 재현), k15(~100만) < 1.5배, k01(~10억) < 1.1배\n")
    u = B.get("uns")
    if u:
        print("  %-10s %10s %10s %12s  %s" % ("축", "DV 샘플", "이득", "예측", "판정"))
        print("  " + "-" * 92)
        exp = {"c1k": (2.3, 3.3), "c1m": (0.0, 1.5), "c8m": (0.0, 1.1)}
        oks = []
        for name, lab, card in AXES:
            if name == "uns" or name not in B:
                continue
            g_ = u["dv"] / B[name]["dv"] if B[name]["dv"] else float("inf")
            lo, hi = exp.get(name, (0, 99))
            ok = lo <= g_ <= hi
            oks.append(ok)
            sep = B[name]["rng"][1] < u["rng"][0]
            print("  %-10s %10.0f %9.2f배 %11s  %s%s"
                  % (lab, B[name]["dv"], g_, "%.1f~%.1f배" % (lo, hi),
                     "안에 듦 ✅" if ok else "벗어남 ❌",
                     "" if sep else "  (범위 겹침)"))
        if oks:
            print("\n  -> %d/%d 통과. %s" % (sum(oks), len(oks),
                  "S_C1 적중 — 처방에는 조건이 붙는다." if all(oks)
                  else "S_C1 일부 빗나감 — 아래 값을 보고 처방 문장을 다시 쓸 것."))

    # ---------- S_C2 ----------
    h("S_C2 채점 — DV 파일 크기가 같은 순서로 커지는가")
    seq = [(lab, G[n]["dv_bytes"]) for n, lab, c in AXES if G.get(n)]
    for lab, v in seq:
        print("  %-10s %14s B" % (lab, f"{v:,}"))
    if len(seq) >= 3:
        srt = [v for lab, v in seq[1:]]
        mono = all(srt[i] <= srt[i + 1] for i in range(len(srt) - 1))
        print("\n  -> 정렬 축 3개가 %s" % ("단조 증가 ✅" if mono else "단조 아님"))

    # ---------- wall ----------
    h("wall-clock — 정렬 효과(baseline) 와 패치 효과")
    if u and u["wall"]:
        for name, lab, card in AXES:
            if name == "uns" or name not in B or not B[name]["wall"]:
                continue
            reps = sorted(set(u["wall"]) & set(B[name]["wall"]))
            if not reps:
                continue
            d = [(B[name]["wall"][r] / u["wall"][r] - 1) * 100 for r in reps]
            p = sign_p(sum(1 for x in d if x > 0), len(d))
            med = st.median(d)
            print("  정렬 효과 %-10s %-32s 중앙 %+6.1f%%  p=%.2f  %s"
                  % (lab, " ".join("%+.1f" % x for x in d)[:32], med, p,
                     "주장 가능" if abs(med) > NOISE_FLOOR and p <= 0.05 else "주장 불가"))
    print()
    for name, lab, card in AXES:
        if name not in B or name not in P or not P[name]:
            continue
        wb, wq = B[name]["wall"], P[name]["wall"]
        reps = sorted(set(wb) & set(wq))
        if not reps:
            continue
        d = [(wq[r] / wb[r] - 1) * 100 for r in reps]
        p = sign_p(sum(1 for x in d if x > 0), len(d))
        med = st.median(d)
        print("  패치 효과 %-10s %-32s 중앙 %+6.1f%%  p=%.2f  %s"
              % (lab, " ".join("%+.1f" % x for x in d)[:32], med, p,
                 "주장 가능" if abs(med) > NOISE_FLOOR and p <= 0.05 else "주장 불가"))

    out = os.path.join(results, "sortkey.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"baseline": {k: {kk: vv for kk, vv in v.items() if kk != "wall"}
                                for k, v in B.items()}}, fh, indent=2, ensure_ascii=False)
    print("\n  -> %s" % out)


if __name__ == "__main__":
    main()
