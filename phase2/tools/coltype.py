#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
컬럼 타입을 통제한 축 — "손익분기 5컬럼" 이 컬럼 수인가 디코딩 비용인가.

06c-coltype.sh 헤더의 Q8~Q11 을 채점한다. 예측은 저기 있고 여기 없다.
여기 다시 적으면 사후에 어느 쪽을 고쳤는지 알 수 없게 된다.

덤으로 '샘플→wall-clock 보정 비율' 을 F-016 의 6점에서 전 구성으로 넓힌다.
프로파일러 샘플 비중을 그대로 "쿼리가 이만큼 빨라진다" 로 옮기면 얼마나
과장되는가 — 이 문서의 모든 수치에 걸리는 보정이다.
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

DENSITIES = [("d50", "0.5%"), ("d610", "6.1%"), ("d700", "7.0%")]
NOISE_FLOOR = 9.7    # F-010
OUTLIER_FACTOR = 2.0  # F-016 (라운드 이상치는 표시만 하고 제외하지 않는다)

# 구성. suf 는 태그 접미사, n 은 컬럼 수, kind 는 디코딩 비용의 성격.
# 표시 순서가 곧 논증이다 — 같은 컬럼 수끼리 붙여놓고 비중이 갈리는지 본다.
CONFIGS = [
    dict(key="c1",  suf="",    n=1,  kind="int", new=False, label="정수 1개  (id)"),
    dict(key="s1",  suf="s1",  n=1,  kind="str", new=True,  label="문자열 1개 (s07)"),
    dict(key="w3",  suf="w3",  n=3,  kind="int", new=False, label="정수 3개  (id,k01,k02)"),
    dict(key="s3",  suf="s3",  n=3,  kind="str", new=True,  label="문자열 3개 (s07~s09)"),
    dict(key="w5",  suf="w5",  n=5,  kind="int", new=False, label="정수 5개"),
    dict(key="i10", suf="i10", n=10, kind="int", new=True,  label="정수 10개"),
    dict(key="w10", suf="w10", n=10, kind="mix", new=False, label="혼합 10개 (문자열 3)"),
    dict(key="w20", suf="w20", n=20, kind="mix", new=False, label="혼합 20개 (문자열 5)"),
]

# 06c-coltype.sh Q8/Q9 — 두 가설이 서로 반대 순서를 예측한다.
#   컬럼 수 가설: 비중 = 870 / (870 + 933c)      (F-016 이 세운 선형 모델)
#   디코딩 비용 가설: 정수 190/컬럼, md5 3,130/컬럼, fixed 705
PRED_COUNT = {"i10": 8.5, "s1": 48.3, "s3": 23.7}
PRED_COST = {"i10": 25.0, "s1": 18.5, "s3": 9.0}
PRED_WALL = {"i10": -9.0, "s1": -7.0, "s3": -3.0}


def pad(s, w):
    """한글(전각)을 폭 2 로 세어 열을 맞춘다."""
    import unicodedata
    n = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)
    return s + " " * max(0, w - n)


def tag_for(dens, cfg):
    return dens + cfg["suf"]


def profiles(results, arm, tag, n):
    pat = os.path.join(results, "profiles", "%s__%s_c%d_r*.collapsed" % (arm, tag, n))
    out = {}
    for path in sorted(glob.glob(pat)):
        m = re.search(r"_r(\d+)\.collapsed$", path)
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        out[int(m.group(1))] = {"dv": a["dv_union_samples"],
                                "scan": a["scan_samples"],
                                "pct_scan": a["dv_pct_of_scan"]}
    return out


def walls(results, arm, tag):
    out = {}
    for path in glob.glob(os.path.join(results, "scan_%s__%s_r*.json" % (arm, tag))):
        m = re.search(r"_r(\d+)\.json$", path)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if d.get("median_s"):
            out[int(m.group(1))] = float(d["median_s"])
    return out


def outlier_reps(vals):
    if len(vals) < 3:
        return set()
    med = st.median(vals.values())
    if med <= 0:
        return set()
    return {r for r, v in vals.items()
            if v > med * OUTLIER_FACTOR or v < med / OUTLIER_FACTOR}


def sign_test_p(k, n):
    if n == 0:
        return 1.0
    k = max(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / (2.0 ** n))


def collect(results):
    rows = {}
    for dens, dlabel in DENSITIES:
        for cfg in CONFIGS:
            tag = tag_for(dens, cfg)
            b = profiles(results, "baseline", tag, cfg["n"])
            q = profiles(results, "patched", tag, cfg["n"])
            if not b or not q:
                continue
            bd = [v["dv"] for v in b.values()]
            qd = [v["dv"] for v in q.values()]
            bs = [v["scan"] for v in b.values()]
            qs = [v["scan"] for v in q.values()]

            wb, wq = walls(results, "baseline", tag), walls(results, "patched", tag)
            reps = sorted(set(wb) & set(wq))
            deltas = [(wq[r] / wb[r] - 1) * 100 for r in reps]
            bad = outlier_reps(wb) | outlier_reps(wq)
            clean = [(wq[r] / wb[r] - 1) * 100 for r in reps if r not in bad]

            rows[(dens, cfg["key"])] = dict(
                dens=dens, dlabel=dlabel, cfg=cfg,
                dv_base=st.mean(bd), dv_patch=st.mean(qd),
                scan_base=st.mean(bs), scan_patch=st.mean(qs),
                share=st.mean(v["pct_scan"] for v in b.values()),
                speedup=st.mean(bd) / st.mean(qd) if st.mean(qd) else float("inf"),
                dv_overlap=not (max(qd) < min(bd) or max(bd) < min(qd)),
                scan_cut=(st.mean(bs) - st.mean(qs)) / st.mean(bs) * 100,
                n=len(bd), deltas=deltas, deltas_clean=clean,
                outliers=sorted(bad),
                wall_med=st.median(deltas) if deltas else None,
                wall_med_clean=st.median(clean) if clean else None,
                slower=sum(1 for x in deltas if x > 0),
            )
    return rows


def h(title):
    print("\n" + "=" * 100)
    print(" " + title)
    print("=" * 100)


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    rows = collect(results)
    if not rows:
        print("측정 결과가 없습니다: %s" % results)
        return

    # ---------------- 1. 구성별 표 ----------------
    h("컬럼 수 vs 디코딩 비용 — 같은 컬럼 수끼리 붙여놓았다 (★ = 이번 신규)")
    for dens, dlabel in DENSITIES:
        got = [c for c in CONFIGS if (dens, c["key"]) in rows]
        if not got:
            continue
        print("\n### d=%s" % dlabel)
        print("  %-2s %-22s %3s %9s %9s %8s %9s %10s %10s"
              % ("", "구성", "n", "DV base", "DV patch", "배율", "DV비중", "스캔감소", "wall중앙"))
        print("  " + "-" * 96)
        prev_n = None
        for cfg in got:
            r = rows[(dens, cfg["key"])]
            if prev_n is not None and cfg["n"] != prev_n:
                print("  " + "·" * 96)
            prev_n = cfg["n"]
            w = ("%+.1f%%" % r["wall_med"]) if r["wall_med"] is not None else "—"
            print("  %-2s %s %3d %9.0f %9.0f %7.2f배 %8.2f%% %9.1f%% %10s"
                  % ("★" if cfg["new"] else "", pad(cfg["label"], 22), cfg["n"],
                     r["dv_base"], r["dv_patch"], r["speedup"],
                     r["share"], r["scan_cut"], w))

    # ---------------- 2. Q8 채점 (판정) ----------------
    h("Q8 채점 — 부호 하나로 갈린다: 비중(정수10) > 비중(문자열3) 인가")
    print("  컬럼 수 가설:     정수10=8.5% < 문자열3=23.7%   (넓을수록 비중이 작다)")
    print("  디코딩 비용 가설: 정수10=25.0% > 문자열3=9.0%  (비쌀수록 비중이 작다)\n")
    print("  %8s %14s %14s %10s  %s" % ("d", "정수10 비중", "문자열3 비중", "차이", "지지 가설"))
    print("  " + "-" * 96)
    verdicts = []
    for dens, dlabel in DENSITIES:
        a = rows.get((dens, "i10"))
        b = rows.get((dens, "s3"))
        if not a or not b:
            continue
        diff = a["share"] - b["share"]
        who = "디코딩 비용" if diff > 0 else "컬럼 수"
        verdicts.append(who)
        print("  %8s %13.2f%% %13.2f%% %+9.1fp  %s" % (dlabel, a["share"], b["share"], diff, who))
    if verdicts:
        print("\n  -> %d/%d 밀도에서 '%s' 가설을 지지."
              % (verdicts.count(verdicts[0]), len(verdicts), verdicts[0]))
        if len(set(verdicts)) > 1:
            print("     ⚠️  밀도마다 결론이 다르다. 판정 불가.")

    # ---------------- 3. Q9 채점 (점추정) ----------------
    h("Q9 채점 — 단가 모델(정수 190 / md5 3,130 / fixed 705)의 점추정 (d=6.1%)")
    print("  %-22s %12s %12s %10s | %12s %10s"
          % ("구성", "비용가설 예측", "실측", "차이", "수 가설 예측", "차이"))
    print("  " + "-" * 96)
    for key in ("i10", "s1", "s3"):
        r = rows.get(("d610", key))
        if not r:
            continue
        print("  %s %11.1f%% %11.2f%% %+9.1fp | %11.1f%% %+9.1fp"
              % (pad(r["cfg"]["label"], 22), PRED_COST[key], r["share"],
                 r["share"] - PRED_COST[key],
                 PRED_COUNT[key], r["share"] - PRED_COUNT[key]))

    # ---------------- 4. 컬럼 단가 재추정 ----------------
    h("컬럼 단가 — 이번 데이터만으로 다시 푼다 (앞의 단가는 F-016 의 사후 추정이었다)")
    print("  scan = DV + fixed + Σ(컬럼 단가).  같은 밀도의 네 구성에서 연립으로 푼다.")
    print("  정수 단가 = (scan[정수10] - scan[정수1]) / 9")
    print("  문자열 단가 = (scan[문자열3] - scan[문자열1]) / 2   (s08 32자, s09 16자)")
    print("  fixed = scan[정수1] - DV - 정수단가\n")
    print("  %8s %14s %14s %12s %12s" % ("d", "정수 단가", "md5 단가", "배수", "fixed"))
    print("  " + "-" * 96)
    unit = {}
    for dens, dlabel in DENSITIES:
        need = [rows.get((dens, k)) for k in ("c1", "i10", "s1", "s3")]
        if not all(need):
            continue
        c1, i10, s1, s3 = need
        per_int = (i10["scan_base"] - c1["scan_base"]) / 9.0
        per_str = (s3["scan_base"] - s1["scan_base"]) / 2.0
        fixed = c1["scan_base"] - c1["dv_base"] - per_int
        unit[dens] = (per_int, per_str, fixed)
        print("  %8s %13.0f %13.0f %10.1f배 %11.0f"
              % (dlabel, per_int, per_str, per_str / per_int if per_int else float("nan"), fixed))
    if unit:
        ratios = [v[1] / v[0] for v in unit.values() if v[0]]
        print("\n  -> md5 문자열 컬럼 하나가 정수 컬럼 %.0f~%.0f 개어치다."
              % (min(ratios), max(ratios)))
        print("     '컬럼 수' 는 단위가 아니다.")

    # ---------------- 5. Q10 / Q11 ----------------
    h("Q10 · Q11 — wall-clock 과 DV 개선 배율")
    print("  %8s %-22s %-30s %8s %6s %8s  %s"
          % ("d", "구성", "라운드별 (patched/baseline-1)", "중앙값", "부호", "예측", "판정"))
    print("  " + "-" * 96)
    for dens, dlabel in DENSITIES:
        for cfg in CONFIGS:
            r = rows.get((dens, cfg["key"]))
            if not r or not r["deltas"]:
                continue
            if not cfg["new"]:
                continue
            med, n = r["wall_med"], len(r["deltas"])
            p = sign_test_p(r["slower"], n)
            if abs(med) < NOISE_FLOOR:
                v = "노이즈 안 — 주장 불가"
            else:
                v = ("단축 %.1f%%" % -med) if med < 0 else ("느려짐 %.1f%%" % med)
            pred = PRED_WALL.get(cfg["key"])
            print("  %8s %s %-30s %+7.1f%% %3d/%-2d %+7.1f%%  %s (p=%.2f)"
                  % (dlabel, pad(cfg["label"], 22),
                     " ".join("%+.1f" % x for x in r["deltas"])[:30],
                     med, r["slower"], n, pred if pred is not None else 0, v, p))
            if r["outliers"]:
                print("  %8s %-22s ⚠️  라운드 %s 이상치 — 제외 시 %+.1f%% (n=%d)"
                      % ("", "", ",".join("r%d" % x for x in r["outliers"]),
                         r["wall_med_clean"], len(r["deltas_clean"])))

    print("\n  Q11 — DV 개선 배율이 투영과 무관한가 (겹침이면 주장 불가):")
    for dens, dlabel in DENSITIES:
        got = [(c["label"], rows[(dens, c["key"])]) for c in CONFIGS
               if (dens, c["key"]) in rows]
        if not got:
            continue
        sp = [r["speedup"] for _, r in got]
        ov = [lab for lab, r in got if r["dv_overlap"]]
        print("    d=%-6s 배율 %.2f~%.2f배 (%d개 구성)%s"
              % (dlabel, min(sp), max(sp), len(sp),
                 "  ⚠️ 겹침: " + ", ".join(ov) if ov else ""))

    # ---------------- 6. 샘플 -> wall-clock 보정 ----------------
    h("샘플 → wall-clock 보정 — 프로파일러 비중을 지연시간으로 읽으면 얼마나 과장되나")
    print("  스캔 샘플 감소(프로파일러) 대 wall-clock 단축(실측)의 비.")
    print("  1.0 이면 CPU 절감이 그대로 지연시간이 된다. 크면 그만큼 과장이다.\n")
    print("  %8s %-22s %12s %12s %9s  %s"
          % ("d", "구성", "샘플 감소", "wall 단축", "비율", "비고"))
    print("  " + "-" * 96)
    pts, fit_x, fit_y = [], [], []
    for dens, dlabel in DENSITIES:
        for cfg in CONFIGS:
            r = rows.get((dens, cfg["key"]))
            if not r or r["wall_med_clean"] is None:
                continue
            sc, wc = r["scan_cut"], -r["wall_med_clean"]
            fit_x.append(sc)
            fit_y.append(wc)
            # 비율은 wall 단축이 노이즈 위일 때만 신뢰한다. 작은 값으로 나누면
            # 비율이 폭주하므로, 아래 회귀가 진짜 요약이고 이 열은 참고다.
            solid = wc > NOISE_FLOOR and r["slower"] <= 1
            ratio = sc / wc if wc > 0 else float("nan")
            if solid:
                pts.append(ratio)
            note = "✔ 채택" if solid else ("wall 이 노이즈 안" if wc <= NOISE_FLOOR
                                          else "부호 불일치")
            print("  %8s %s %11.1f%% %11.1f%% %8s  %s"
                  % (dlabel, pad(cfg["label"], 22), sc, wc,
                     ("%.2f배" % ratio) if wc > 0 else "—", note))
    if pts:
        print("\n  채택한 %d 점: 중앙값 %.2f배, 범위 %.2f~%.2f배"
              % (len(pts), st.median(pts), min(pts), max(pts)))
    if len(fit_x) >= 3:
        # 원점을 지나는 최소제곱. 작은 점을 버리지 않으므로 선택 편향이 없다.
        # (비율만 쓰면 wall 이 큰 점만 남아 보정이 작게 잡힌다.)
        k = sum(x * y for x, y in zip(fit_x, fit_y)) / sum(x * x for x in fit_x)
        ss = sum((y - k * x) ** 2 for x, y in zip(fit_x, fit_y))
        tot = sum((y - st.mean(fit_y)) ** 2 for y in fit_y)
        print("  전 %d 점 원점 회귀: wall 단축 = %.2f × 샘플 감소  (보정 %.2f배, R²=%.2f)"
              % (len(fit_x), k, 1 / k if k else float("nan"),
                 1 - ss / tot if tot else float("nan")))
        print("  ⚠️ 두 값의 차이가 곧 선택 편향의 크기다. 회귀 쪽을 믿는다.")

    out = os.path.join(results, "coltype.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"rows": [{k: v for k, v in r.items() if k != "cfg"} |
                            {"key": r["cfg"]["key"], "label": r["cfg"]["label"],
                             "n": r["cfg"]["n"], "kind": r["cfg"]["kind"]}
                            for r in rows.values()],
                   "noise_floor": NOISE_FLOOR,
                   "unit_cost": {k: {"per_int": v[0], "per_str": v[1], "fixed": v[2]}
                                 for k, v in unit.items()}},
                  fh, indent=2, ensure_ascii=False)
    print("\n  -> %s" % out)


if __name__ == "__main__":
    main()
