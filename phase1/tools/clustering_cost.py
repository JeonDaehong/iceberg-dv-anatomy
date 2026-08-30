#!/usr/bin/env python3
"""
평균 run 길이 L -> DV 체크 비용.

밀도 축(cost_curve.py)보다 깨끗한 비교다:
  밀도가 고정이므로 삭제 행 수가 거의 같고, contains() 호출 횟수도 동일하다.
  남는 차이는 '컨테이너 타입과 그 내부 구조(nRuns)' 뿐이다.

예측: RunContainer.contains 는 nRuns 에 대한 이진 탐색 -> L 이 커질수록 싸져야 한다.
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
    density_bp = int(sys.argv[2]) if len(sys.argv) > 2 else 50

    cont = {}
    cpath = os.path.join(results, f"clustering_curve_d{density_bp}.json")
    if os.path.exists(cpath):
        for c in json.load(open(cpath)):
            cont[c["run_length"]] = c

    gens = {}
    for f in glob.glob(os.path.join(results, f"gen_d{density_bp}L*.json")):
        m = re.search(rf"gen_d{density_bp}L(\d+)\.json$", f)
        if m:
            gens[int(m.group(1))] = json.load(open(f))

    rows = []
    for path in glob.glob(os.path.join(results, "profiles", f"d{density_bp}L*_c*.collapsed")):
        m = re.search(rf"d{density_bp}L(\d+)_c\d+\.collapsed$", os.path.basename(path))
        if not m:
            continue
        L = int(m.group(1))
        stacks = parse_collapsed(path)
        if not stacks:
            continue
        a = analyze(stacks)
        c = cont.get(L, {})
        g = gens.get(L, {})
        rows.append({
            "run_length": L,
            "dv_samples": a["dv_union_samples"],
            "dv_pct_scan": a["dv_pct_of_scan"],
            "dv_pct_all": a["dv_pct_of_all"],
            "container": ("run" if c.get("run") else
                          "bitmap" if c.get("bitmap") else "array") if c else None,
            "mean_runs": c.get("mean_runs"),
            "dv_bytes": c.get("container_bytes"),
            "deleted_rows": g.get("deleted_rows"),
        })
    rows.sort(key=lambda r: r["run_length"])
    if not rows:
        print("프로파일이 없습니다. 05-profile-clustering.sh 를 먼저 실행하세요.")
        sys.exit(1)

    ref = rows[0]["dv_samples"]

    print("=" * 100)
    print(f" 클러스터링(L) -> DV 체크 비용   [고정 밀도 {density_bp/100:.2f}%]")
    print("=" * 100)
    print(f"  {'L':>6} {'컨테이너':>9} {'평균nRuns':>10} {'삭제행':>10} "
          f"{'DV 샘플':>9} {'DV/스캔':>9} {'L=1 대비':>10} {'DV bytes':>10}")
    print("  " + "-" * 96)
    for r in rows:
        mr = f"{r['mean_runs']:>10,.0f}" if r["mean_runs"] else f"{'-':>10}"
        rel = f"{r['dv_samples']/ref:>9.2f}x" if ref else f"{'-':>10}"
        dl = f"{r['deleted_rows']:>10,}" if r["deleted_rows"] else f"{'-':>10}"
        by = f"{r['dv_bytes']:>10,}" if r["dv_bytes"] else f"{'-':>10}"
        print(f"  {r['run_length']:>6} {str(r['container']):>9} {mr} {dl} "
              f"{r['dv_samples']:>9,} {r['dv_pct_scan']:>8.2f}% {rel} {by}")

    print()
    print("=" * 100)
    print(" 판정")
    print("=" * 100)
    best = min(rows, key=lambda r: r["dv_samples"])
    worst = max(rows, key=lambda r: r["dv_samples"])
    print(f"  가장 비쌈: L={worst['run_length']} ({worst['container']}) {worst['dv_samples']:,} 샘플")
    print(f"  가장 쌈  : L={best['run_length']} ({best['container']}) {best['dv_samples']:,} 샘플")
    if best["dv_samples"]:
        print(f"  -> 클러스터링만으로 {worst['dv_samples']/best['dv_samples']:.2f}배 차이")

    mono = all(rows[i]["dv_samples"] >= rows[i + 1]["dv_samples"] - 0.15 * rows[i]["dv_samples"]
               for i in range(len(rows) - 1))
    print(f"  L 증가에 따른 단조 감소 예측: {'대체로 부합' if mono else '불일치 (되짚어볼 것)'}")
    print("""
  주의:
   - ctimer 샘플 노이즈는 대략 ±√n. 인접 값 차이는 주장하지 말 것.
   - 밀도가 고정이라 삭제 행 수가 거의 같다. 즉 이 차이는 순수하게
     '컨테이너 구조' 때문이며, 밀도 축보다 인과가 깨끗하다.""")

    with open(os.path.join(results, f"clustering_cost_d{density_bp}.json"), "w") as fh:
        json.dump(rows, fh, indent=2)


if __name__ == "__main__":
    main()
