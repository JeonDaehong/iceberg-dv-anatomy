#!/usr/bin/env python3
"""
summary.json 의 반복 측정 결과를 docs/dashboard.html 에 주입한다.

대시보드는 자기완결형 HTML 이어야 하므로(외부 fetch 불가) 데이터를 인라인한다.
템플릿의 __COST__ / __CLU_COST__ 자리를 실제 값으로 바꾼 뒤
docs/dashboard.built.html 로 쓴다.
"""
import json
import os
import re
import sys


def label_density(bp):
    d = bp / 100.0
    return f"{d:.2f}%".rstrip("0").rstrip(".") + ("%" if not f"{d:.2f}".endswith("%") else "")


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    summary_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        root, "phase1", "results", "summary.json")
    tpl_path = os.path.join(root, "docs", "dashboard.html")
    out_path = os.path.join(root, "docs", "dashboard.built.html")

    data = json.load(open(summary_path))
    cfgs = data["configs"]

    cost = []
    for c in sorted([c for c in cfgs if c["axis"] == "density"],
                    key=lambda c: c["density_bp"]):
        if c["density_bp"] == 0:
            continue          # 대조군은 0 이라 막대 차트에 안 넣는다
        d = c["density_bp"] / 100.0
        cost.append({
            "label": (f"{d:g}%"),
            "density": d,
            "container": c.get("container"),
            "mean": round(c["dv_samples_mean"], 1),
            "min": c["dv_samples_min"],
            "max": c["dv_samples_max"],
            "pct": round(c["dv_pct_scan_mean"], 2),
            "reps": c["n_reps"],
        })

    clu = []
    for c in sorted([c for c in cfgs if c["axis"] == "clustering"],
                    key=lambda c: c["run_length"]):
        clu.append({
            "L": c["run_length"],
            "container": c.get("container"),
            "mean": round(c["dv_samples_mean"], 1),
            "min": c["dv_samples_min"],
            "max": c["dv_samples_max"],
            "pct": round(c["dv_pct_scan_mean"], 2),
            "reps": c["n_reps"],
        })

    # phase2 의 패치 전후 비교. 없으면 빈 배열 (섹션이 스스로 렌더를 건너뛴다).
    patch = []
    extra = []
    cmp_path = os.path.join(root, "phase2", "results", "compare.json")
    if os.path.exists(cmp_path):
        # 메인 차트는 buildRowIdMapping 경로 다섯 개만 (동일 조건 비교).
        LABEL = {"d50": "삭제율 0.5%", "d610": "삭제율 6.1%", "d700": "삭제율 7.0%",
                 "d50L1": "0.5% · 무정렬 L=1", "d50L4096": "0.5% · 정렬 L=4096"}
        # 나머지 경로는 별도 표로 (F-012). 워크로드가 달라 같은 축에 못 올린다.
        EXTRA = {
            "d50isdel":  ("_deleted 투영 · 0.5%", "buildIsDeleted 경로"),
            "d610isdel": ("_deleted 투영 · 6.1%", "buildIsDeleted 경로"),
            "eq50":      ("DV + equality delete", "게이트가 빠른 경로를 거부"),
            "eq0":       ("equality delete 만", "두 arm이 같은 코드 — 음성 대조군"),
        }
        for c in json.load(open(cmp_path))["configs"]:
            b, q = c.get("baseline"), c.get("patched")
            if not (b and q):
                continue
            if c["tag"] in EXTRA:
                name, note = EXTRA[c["tag"]]
                extra.append({
                    "label": name, "note": note,
                    "base": round(b["mean"], 1), "baseMin": b["min"], "baseMax": b["max"],
                    "patch": round(q["mean"], 1), "patchMin": q["min"], "patchMax": q["max"],
                    "x": round(c["speedup"], 3),
                    "overlap": bool(c.get("overlap")),
                })
                continue
            if c["tag"] not in LABEL:
                continue
            patch.append({
                "tag": c["tag"],
                "label": LABEL.get(c["tag"], c["tag"]),
                "container": ("array" if c["tag"] in ("d50", "d610", "d50L1")
                              else "run" if c["tag"] == "d50L4096" else "bitmap"),
                "base": round(b["mean"], 1), "baseMin": b["min"], "baseMax": b["max"],
                "patch": round(q["mean"], 1), "patchMin": q["min"], "patchMax": q["max"],
                "x": round(c["speedup"], 3),
                "pctBase": round(b["pct_scan"], 2), "pctPatch": round(q["pct_scan"], 2),
                "scanCut": round(c.get("scan_cut_pct", 0), 1),
                "wallBase": round(c.get("wall_baseline_s") or 0, 4),
                "wallPatch": round(c.get("wall_patched_s") or 0, 4),
            })
        # 개선이 큰 순
        patch.sort(key=lambda p: -p["x"])
        order = list(EXTRA)
        extra.sort(key=lambda e: order.index(
            next(k for k, v in EXTRA.items() if v[0] == e["label"])))

    html = open(tpl_path, encoding="utf-8").read()

    # 발견 수 · 측정 축 수 · 빗나간 예측 수는 **문서에서 센다.**
    # 손으로 적었더니 대시보드 17 / 블로그 15 로 어긋났다 (2026-09-09).
    docs = os.path.join(root, "docs")
    nf = len(re.findall(r"^## F-0", open(os.path.join(docs, "findings.md"),
                                         encoding="utf-8").read(), re.M))
    nax = len([f for f in os.listdir(os.path.join(root, "phase2", "scripts"))
               if re.match(r"^\d\d.*\.sh$", f)])
    st = open(os.path.join(docs, "STORY.md"), encoding="utf-8").read()
    blk = st[st.index("### 10.2"):st.index("### 10.3")]
    nmiss = len([l for l in blk.splitlines()
                 if l.startswith("|") and "---" not in l and "예측 | 결과" not in l])
    html = (html.replace("__NF__", str(nf))
                .replace("__NAX__", str(nax))
                .replace("__NMISS__", str(nmiss)))
    html = html.replace("__COST__", json.dumps(cost, ensure_ascii=False))
    html = html.replace("__CLU_COST__", json.dumps(clu, ensure_ascii=False))
    html = html.replace("__PATCH__", json.dumps(patch, ensure_ascii=False))
    html = html.replace("__PATCH_EXTRA__", json.dumps(extra, ensure_ascii=False))

    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)

    print(f"밀도 축 {len(cost)}점, 클러스터링 축 {len(clu)}점, 패치 비교 {len(patch)}점(+보조 {len(extra)}점) 주입")
    print(f"노이즈 바닥(반복 흩어짐 중앙값): {data['noise_pct']:.1f}%")
    print(f"-> {out_path}")

    # 주장 가능성 점검
    if cost:
        hi = max(cost, key=lambda c: c["mean"]); lo = min(cost, key=lambda c: c["mean"])
        r = hi["mean"] / lo["mean"]
        print(f"   밀도 축 최대/최소 = {r:.2f}배 "
              f"[{'유의' if (r-1)*100 > data['noise_pct'] else '노이즈 범위'}]")
    if clu:
        r = clu[0]["mean"] / clu[-1]["mean"]
        print(f"   클러스터링 L=1/L={clu[-1]['L']} = {r:.2f}배 "
              f"[{'유의' if (r-1)*100 > data['noise_pct'] else '노이즈 범위'}]")


if __name__ == "__main__":
    main()
