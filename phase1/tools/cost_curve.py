#!/usr/bin/env python3
"""
밀도 -> DV 체크 비용 곡선.

이 비교가 성립하는 근거:
  buildRowIdMapping 은 배치마다 batchSize 번 무조건 프로브한다.
  밀도가 0.5% 든 50% 든 contains() 호출 횟수는 동일하다.
  총 스캔 행 수가 모든 테이블에서 800만으로 고정이므로,
  DV 샘플 수를 그대로 비교하면 그게 곧 '호출당 비용'이다.

컨테이너 곡선(container_curve.json)과 조인해서
"array 비율이 떨어지는 지점"과 "비용이 떨어지는 지점"이 일치하는지 본다.
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "phase0", "tools"))
from attribute import parse_collapsed, analyze  # noqa: E402


def main():
    results = sys.argv[1] if len(sys.argv) > 1 else "results"

    containers = {}
    cpath = os.path.join(results, "container_curve.json")
    if os.path.exists(cpath):
        for c in json.load(open(cpath)):
            containers[c["density_bp"]] = c

    scans = {}
    for f in glob.glob(os.path.join(results, "scan_d*_c*.json")):
        m = re.search(r"scan_d(\d+)_c\d+\.json$", f)
        if m:
            scans[int(m.group(1))] = json.load(open(f))

    rows = []
    for path in sorted(glob.glob(os.path.join(results, "profiles", "d*_c*.collapsed"))):
        m = re.search(r"d(\d+)_c(\d+)\.collapsed$", os.path.basename(path))
        if not m:
            continue
        bp = int(m.group(1))
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        c = containers.get(bp, {})
        s = scans.get(bp, {})
        rows.append({
            "density_bp": bp,
            "density": bp / 10000.0,
            "dv_samples": a["dv_union_samples"],
            "total_samples": a["total_samples"],
            "scan_samples": a["scan_samples"],
            "dv_pct_all": a["dv_pct_of_all"],
            "dv_pct_scan": a["dv_pct_of_scan"],
            "array_share": c.get("full_array_share"),
            "median_s": s.get("median_s"),
        })
    rows.sort(key=lambda r: r["density_bp"])
    if not rows:
        print("프로파일이 없습니다. 03-profile.sh 를 먼저 실행하세요.")
        sys.exit(1)

    base = next((r for r in rows if r["density_bp"] == 0), None)

    print("=" * 104)
    print(" 밀도 -> DV 체크 비용   (스캔 행 수 800만 고정, contains() 호출 횟수 동일)")
    print("=" * 104)
    print(f"  {'d':>7} {'온전청크 array%':>15} {'DV 샘플':>9} {'DV/스캔':>9} {'DV/전체':>9} "
          f"{'d=0 대비 비용':>13} {'median(s)':>10}")
    print("  " + "-" * 100)

    ref = None  # 가장 낮은 non-zero 밀도를 비용 기준으로 삼는다
    for r in rows:
        if r["density_bp"] > 0 and ref is None:
            ref = r["dv_samples"]

    for r in rows:
        arr = f"{r['array_share']:>14.1f}%" if r["array_share"] is not None else f"{'-':>15}"
        rel = f"{r['dv_samples']/ref:>12.2f}x" if (ref and r["density_bp"] > 0) else f"{'-':>13}"
        med = f"{r['median_s']:>10.3f}" if r["median_s"] else f"{'-':>10}"
        print(f"  {r['density']*100:>6.2f}% {arr} {r['dv_samples']:>9,} "
              f"{r['dv_pct_scan']:>8.2f}% {r['dv_pct_all']:>8.2f}% {rel} {med}")

    print()
    print("=" * 104)
    print(" 판정")
    print("=" * 104)

    if base:
        print(f"  대조군 d=0%: DV 샘플 {base['dv_samples']:,} "
              f"({'오탐 없음' if base['dv_samples'] == 0 else '*** 오탐 발생 ***'})")

    below = [r for r in rows if 0 < r["density_bp"] <= 610]
    above = [r for r in rows if r["density_bp"] >= 640]
    if below and above:
        b = sum(r["dv_samples"] for r in below) / len(below)
        a = sum(r["dv_samples"] for r in above) / len(above)
        print(f"  경계 아래 (d<=6.10%) 평균 DV 샘플: {b:,.0f}")
        print(f"  경계 위   (d>=6.40%) 평균 DV 샘플: {a:,.0f}")
        if a > 0:
            print(f"  -> 경계를 넘으면 DV 체크 비용이 {b/a:.2f}배 싸진다")

    at625 = next((r for r in rows if r["density_bp"] == 625), None)
    if at625 and below and above:
        print(f"  d=6.25% (array {at625['array_share']:.0f}% / bitmap 혼합): "
              f"DV 샘플 {at625['dv_samples']:,}  "
              f"— 경계 아래({b:,.0f})와 위({a:,.0f}) 사이에 위치하는가? "
              f"{'예' if a <= at625['dv_samples'] <= b else '아니오'}")

    with open(os.path.join(results, "cost_curve.json"), "w") as fh:
        json.dump(rows, fh, indent=2)
    print(f"\n  -> {os.path.join(results, 'cost_curve.json')}")
    print("""
  해석 주의:
   - DV 샘플 수는 ctimer 샘플링이라 절대량에 노이즈가 있다. 경향을 본다.
   - median(s) 은 밀도가 높을수록 내보낼 행이 줄어드는 효과가 섞여 있어
     DV 비용만의 지표가 아니다. 인과는 DV 샘플 쪽으로 본다.""")


if __name__ == "__main__":
    main()
