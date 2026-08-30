#!/usr/bin/env python3
"""
반복 측정 집계 — 모든 프로파일을 읽어 설정별 평균/범위를 낸다.
대시보드(docs/dashboard.html)가 소비하는 summary.json 을 만든다.

핵심 원칙: 반복 범위가 겹치면 '차이 없음'으로 보고한다.
2026-08-17 에 같은 설정 재측정이 20~41% 벌어진 것을 확인했으므로,
반복 없이 얻은 단일 값의 미세 차이는 주장하지 않는다.
"""
import csv
import glob
import json
import os
import re
import statistics as st
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402

CHUNK = 65536


def containers(results, tag):
    # 밀도 축은 02-inspect.sh 가 containers_<bp>.csv 로,
    # 클러스터링/점유율 축은 04·06 이 containers_<tag>.csv 로 쓴다.
    cands = [os.path.join(results, f"containers_{tag}.csv")]
    m = re.match(r"^d(\d+)$", tag)
    if m:
        cands.append(os.path.join(results, f"containers_{m.group(1)}.csv"))
    path = next((p for p in cands if os.path.exists(p)), None)
    if not path:
        return None
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return None
    types = Counter(r["container"] for r in rows)
    cards = [int(r["cardinality"]) for r in rows]
    runs = [int(r["runs"]) for r in rows if r["runs"]]
    dom = max(types, key=types.get)
    return {
        "n_containers": len(rows),
        "types": dict(types),
        "dominant": dom,
        "array": types.get("array", 0),
        "bitmap": types.get("bitmap", 0),
        "run": types.get("run", 0),
        "mean_cardinality": sum(cards) / len(cards),
        "mean_runs": (sum(runs) / len(runs)) if runs else None,
        "bytes": sum(int(r["bytes"]) for r in rows),
    }


def gen(results, tag):
    cands = [os.path.join(results, f"gen_{tag}.json")]
    m = re.match(r"^d(\d+)$", tag)
    if m:
        cands.append(os.path.join(results, f"gen_{m.group(1)}.json"))
    p = next((x for x in cands if os.path.exists(x)), None)
    return json.load(open(p)) if p else {}


def collect(results):
    """tag -> [측정1, 측정2, ...]"""
    by_tag = defaultdict(list)
    for path in glob.glob(os.path.join(results, "profiles", "*.collapsed")):
        name = os.path.basename(path)
        m = re.match(r"^(.*?)_c\d+(?:_r(\d+))?\.collapsed$", name)
        if not m:
            continue
        tag = m.group(1)
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        by_tag[tag].append({
            "file": name,
            "rep": int(m.group(2)) if m.group(2) else 0,
            "dv_samples": a["dv_union_samples"],
            "total_samples": a["total_samples"],
            "scan_samples": a["scan_samples"],
            "dv_pct_scan": a["dv_pct_of_scan"],
            "dv_pct_all": a["dv_pct_of_all"],
        })
    return by_tag


def summarize(reps):
    vals = [r["dv_samples"] for r in reps]
    pcts = [r["dv_pct_scan"] for r in reps]
    mean = sum(vals) / len(vals)
    # 대조군(삭제 0)은 샘플이 0이라 흩어짐이 정의되지 않는다. 0 으로 둔다.
    spread = ((max(vals) - min(vals)) / mean * 100) if mean > 0 else 0.0
    return {
        "n_reps": len(vals),
        "dv_samples_mean": mean,
        "dv_samples_min": min(vals),
        "dv_samples_max": max(vals),
        "dv_samples_sd": st.stdev(vals) if len(vals) > 1 else 0.0,
        "dv_pct_scan_mean": sum(pcts) / len(pcts),
        "dv_pct_scan_min": min(pcts),
        "dv_pct_scan_max": max(pcts),
        "spread_pct": spread,
    }


def parse_tag(tag):
    m = re.match(r"^d(\d+)L(\d+)$", tag)
    if m:
        return {"axis": "clustering", "density_bp": int(m.group(1)),
                "run_length": int(m.group(2)), "occupancy_bp": 10000}
    m = re.match(r"^d(\d+)p(\d+)$", tag)
    if m:
        return {"axis": "occupancy", "density_bp": int(m.group(1)),
                "run_length": 1, "occupancy_bp": int(m.group(2))}
    m = re.match(r"^d(\d+)$", tag)
    if m:
        return {"axis": "density", "density_bp": int(m.group(1)),
                "run_length": 1, "occupancy_bp": 10000}
    return None


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"
    by_tag = collect(results)
    if not by_tag:
        print("프로파일이 없습니다.")
        sys.exit(1)

    out = []
    for tag, reps in by_tag.items():
        meta = parse_tag(tag)
        if not meta:
            continue
        s = summarize(reps)
        c = containers(results, tag) or {}
        g = gen(results, tag)
        out.append({
            "tag": tag, **meta, **s,
            "container": c.get("dominant"),
            "n_containers": c.get("n_containers"),
            "array": c.get("array"), "bitmap": c.get("bitmap"), "run": c.get("run"),
            "mean_cardinality": c.get("mean_cardinality"),
            "mean_runs": c.get("mean_runs"),
            "dv_bytes": c.get("bytes"),
            "deleted_rows": g.get("deleted_rows"),
            "actual_density": g.get("actual_density"),
        })
    out.sort(key=lambda r: (r["axis"], r["density_bp"], r["run_length"], -r["occupancy_bp"]))

    # ---- 재현성 리포트 ----
    # 대조군(샘플 0)은 흩어짐 통계에서 제외한다
    multi = [r for r in out if r["n_reps"] > 1 and r["dv_samples_mean"] > 0]
    print("=" * 104)
    print(" 재현성 — 같은 설정 반복 측정의 흩어짐")
    print("=" * 104)
    if multi:
        spreads = [r["spread_pct"] for r in multi]
        print(f"  반복 측정된 설정: {len(multi)}개 (각 {multi[0]['n_reps']}회)")
        print(f"  흩어짐(max-min)/mean:  중앙값 {st.median(spreads):.1f}%   "
              f"최대 {max(spreads):.1f}%")
        print(f"  -> 이 값보다 작은 차이는 주장하지 않는다.")
        noise = st.median(spreads)
    else:
        print("  반복 측정이 없습니다. 07-remeasure.sh 를 실행하세요.")
        noise = 25.0

    for axis, title in [("density", "밀도 축 d"),
                        ("clustering", "클러스터링 축 L"),
                        ("occupancy", "점유율 축 p")]:
        rows = [r for r in out if r["axis"] == axis]
        if not rows:
            continue
        print()
        print("=" * 104)
        print(f" {title}")
        print("=" * 104)
        print(f"  {'설정':>12} {'컨테이너':>9} {'개수':>5} {'평균card':>9} "
              f"{'DV 샘플(평균)':>13} {'범위':>15} {'흩어짐':>7} {'DV bytes':>10}")
        print("  " + "-" * 100)
        for r in rows:
            key = (f"d={r['density_bp']/100:.2f}%" if axis == "density" else
                   f"L={r['run_length']}" if axis == "clustering" else
                   f"p={r['occupancy_bp']/100:.0f}%")
            rng = f"{r['dv_samples_min']:,}–{r['dv_samples_max']:,}" if r["n_reps"] > 1 else "-"
            print(f"  {key:>12} {str(r['container']):>9} {str(r['n_containers'] or '-'):>5} "
                  f"{(r['mean_cardinality'] or 0):>9,.0f} {r['dv_samples_mean']:>13,.0f} "
                  f"{rng:>15} {r['spread_pct']:>6.1f}% {str(r['dv_bytes'] or '-'):>10}")

        # 노이즈를 넘는 차이만 보고
        vals = [(r, r["dv_samples_mean"]) for r in rows if r["dv_samples_mean"] > 0]
        if len(vals) > 1:
            hi = max(vals, key=lambda x: x[1])
            lo = min(vals, key=lambda x: x[1])
            ratio = hi[1] / lo[1]
            key_hi = (f"d={hi[0]['density_bp']/100:.2f}%" if axis == "density" else
                      f"L={hi[0]['run_length']}" if axis == "clustering" else
                      f"p={hi[0]['occupancy_bp']/100:.0f}%")
            key_lo = (f"d={lo[0]['density_bp']/100:.2f}%" if axis == "density" else
                      f"L={lo[0]['run_length']}" if axis == "clustering" else
                      f"p={lo[0]['occupancy_bp']/100:.0f}%")
            verdict = "유의" if (ratio - 1) * 100 > noise else "노이즈 범위 — 주장 불가"
            print(f"\n  최대 대비 최소: {key_hi} {hi[1]:,.0f}  vs  {key_lo} {lo[1]:,.0f}"
                  f"  = {ratio:.2f}배  [{verdict}]")

    with open(os.path.join(results, "summary.json"), "w") as fh:
        json.dump({"noise_pct": noise, "configs": out}, fh, indent=2)
    print(f"\n  -> {os.path.join(results, 'summary.json')}")


if __name__ == "__main__":
    main()
