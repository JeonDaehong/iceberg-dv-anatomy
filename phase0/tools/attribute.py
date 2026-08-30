#!/usr/bin/env python3
"""
async-profiler collapsed stack -> DV 체크 비중 귀속.

Phase 0 게이트의 판정을 내리는 도구. 표준 라이브러리만 사용한다.

분모 두 가지를 모두 보고한다:
  ALL   : JVM 전체 샘플. JVM 기동/컴파일/GC 가 섞여 DV 비중을 '과소' 평가한다.
          게이트 판정은 보수적인 쪽이 안전하므로 이걸 1차 기준으로 쓴다.
  SCAN  : 스캔 서브트리 샘플만. 실제 리더 안에서의 비중. 2차 참고치.

주의 — 이 도구는 스택 '포함 여부'로 세는 inclusive 집계다.
       DV 프레임이 스택 어딘가에 있으면 그 샘플 전체를 DV 로 센다.
       DV 그룹들은 서로 중첩되므로(ColumnarBatchUtil -> deletes -> roaringbitmap)
       그룹별 수치의 단순 합은 전체와 일치하지 않는다. UNION 값을 쓸 것.
"""
import argparse
import json
import os
import sys
from collections import Counter

# 스택에 이 프레임이 있으면 "DV 삭제 체크" 로 귀속한다.
DV_GROUPS = {
    # 벡터화 경로의 배치 루프 (ColumnarBatchUtil.buildRowIdMapping / buildIsDeleted)
    "batch_util": ["org.apache.iceberg.spark.data.vectorized.ColumnarBatchUtil"],
    # PositionDeleteIndex / RoaringPositionBitmap
    "iceberg_deletes": ["org.apache.iceberg.deletes."],
    # Roaring 라이브러리 내부 (contains / RoaringArray.binarySearch / 컨테이너)
    # iceberg-spark-runtime 은 roaringbitmap 을 shade 하므로 두 형태를 모두 잡는다:
    #   org.roaringbitmap.*                              (unshaded)
    #   org.apache.iceberg.shaded.org.roaringbitmap.*    (fat runtime jar)
    "roaring": ["org.roaringbitmap.", "shaded.org.roaringbitmap."],
}

# 벡터화 리더가 실제로 쓰이는지 판정한다.
#
# 필터 '클래스' 이름으로는 판정할 수 없다. 2026-08-17 에 두 번 헛짚었다:
#   - org.apache.iceberg.deletes.Deletes  -> 벡터화 경로도 DV 로드에 사용
#   - org.apache.iceberg.data.DeleteFilter -> BaseBatchReader$BatchDeleteFilter 가
#     이걸 상속해서 벡터화 경로에서도 needDeletes()/hasEqDeletes() 로 나타남
# 그래서 '리더' 클래스로 판정한다. 이건 배타적이다.
BATCH_READER_FRAMES = [
    "org.apache.iceberg.spark.source.BaseBatchReader",
    "org.apache.iceberg.spark.source.BatchDataReader",
    "org.apache.iceberg.spark.data.vectorized.ColumnarBatchReader",
]
ROW_READER_FRAMES = [
    "org.apache.iceberg.spark.source.BaseRowReader",
    "org.apache.iceberg.spark.source.RowDataReader",
]

# SCAN 분모용 서브트리 루트.
SCAN_ROOTS = [
    "org.apache.iceberg.spark.source.",
    "org.apache.iceberg.spark.data.",
    "org.apache.iceberg.parquet.",
    "org.apache.parquet.",
]


def normalize(stack: str) -> str:
    # async-profiler 버전에 따라 org/apache/... 또는 org.apache.... 로 나온다.
    return stack.replace("/", ".")


def parse_collapsed(path):
    """collapsed 포맷: 'frame1;frame2;...;frameN <count>'"""
    stacks = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            sp = line.rfind(" ")
            if sp < 0:
                continue
            try:
                count = int(line[sp + 1 :])
            except ValueError:
                continue
            stacks.append((normalize(line[:sp]), count))
    return stacks


def analyze(stacks):
    total = sum(c for _, c in stacks)
    scan = sum(c for s, c in stacks if any(r in s for r in SCAN_ROOTS))

    per_group = {}
    for name, pats in DV_GROUPS.items():
        per_group[name] = sum(c for s, c in stacks if any(p in s for p in pats))

    all_dv_pats = [p for pats in DV_GROUPS.values() for p in pats]
    dv_union = sum(c for s, c in stacks if any(p in s for p in all_dv_pats))
    batch_reader = sum(c for s, c in stacks if any(p in s for p in BATCH_READER_FRAMES))
    row_reader = sum(c for s, c in stacks if any(p in s for p in ROW_READER_FRAMES))

    return {
        "total_samples": total,
        "scan_samples": scan,
        "dv_union_samples": dv_union,
        "dv_groups": per_group,
        "batch_reader_samples": batch_reader,
        "row_reader_samples": row_reader,
        "dv_pct_of_all": 100.0 * dv_union / total if total else 0.0,
        "dv_pct_of_scan": 100.0 * dv_union / scan if scan else 0.0,
        "scan_pct_of_all": 100.0 * scan / total if total else 0.0,
    }


def hot_frames(stacks, patterns, top=12):
    """DV 관련 스택의 최말단(leaf) 프레임 분포 — 어디서 사이클이 타는지."""
    leaves = Counter()
    for s, c in stacks:
        if any(p in s for p in patterns):
            frames = s.split(";")
            # DV 패턴에 매칭되는 가장 깊은 프레임을 대표로 삼는다
            pick = None
            for f in frames:
                if any(p in f for p in patterns):
                    pick = f
            leaves[pick or frames[-1]] += c
    return leaves.most_common(top)


def verdict(dv_pct):
    if dv_pct >= 5.0:
        return "GO", "원안대로 전면 진행. 헤드라인 = '스캔 CPU의 X%를 차지하는 DV 체크'"
    if dv_pct >= 1.0:
        return "PIVOT", (
            "진행하되 무게중심 이동. 마이크로벤치는 유지하고 "
            "헤드라인은 ColumnarBatchUtil 코드 개선(README §7.1)과 레이아웃 처방으로."
        )
    return "STOP", (
        "방향 전환. 'DV 읽기 비용은 무시 가능하다'를 짧은 글로 내고, "
        "병목이 실제로 있는 곳(DV 파일 I/O, planning, Puffin 파싱)으로 주제 이동."
    )


def fmt_row(label, val, pct=None):
    if pct is None:
        return f"  {label:<28} {val:>12,}"
    return f"  {label:<28} {val:>12,}   {pct:>6.2f}%"


def report_one(path):
    stacks = parse_collapsed(path)
    if not stacks:
        print(f"!! {path}: 샘플 0개 — 프로파일러가 안 붙었을 가능성. 스킵.")
        return None
    a = analyze(stacks)
    name = os.path.basename(path)

    print(f"\n{'='*72}\n{name}\n{'='*72}")
    print(fmt_row("전체 샘플", a["total_samples"]))
    print(fmt_row("스캔 서브트리", a["scan_samples"], a["scan_pct_of_all"]))
    print(fmt_row("DV 체크 (union)", a["dv_union_samples"], a["dv_pct_of_all"]))
    print(f"       └ 스캔 서브트리 대비:                    {a['dv_pct_of_scan']:>6.2f}%")
    print("\n  [그룹별 inclusive — 서로 중첩되므로 합산 금지]")
    for g, v in a["dv_groups"].items():
        pct = 100.0 * v / a["total_samples"] if a["total_samples"] else 0
        print(fmt_row(f"  {g}", v, pct))

    br, rr = a["batch_reader_samples"], a["row_reader_samples"]
    if br + rr > 0:
        kind = "벡터화(batch)" if br >= rr else "*** 행 기반(row) ***"
        print(f"\n  리더 경로: {kind}   batch={br:,}  row={rr:,}")
        if rr > br:
            print("      경고: 행 기반 리더가 우세합니다. ColumnarBatchUtil 경로가 아니라\n"
                  "      DeleteFilter 경로를 측정 중이며, 측정 대상이 달라집니다.")

    if a["dv_union_samples"] > 0:
        print("\n  [DV 스택의 hot frame]")
        allp = [p for pats in DV_GROUPS.values() for p in pats]
        for f, c in hot_frames(stacks, allp):
            pct = 100.0 * c / a["total_samples"]
            short = f if len(f) <= 62 else "..." + f[-59:]
            print(f"    {pct:>6.2f}%  {short}")

    a["file"] = name
    return a


def main():
    p = argparse.ArgumentParser()
    p.add_argument("collapsed", nargs="+", help="async-profiler collapsed 출력 파일들")
    p.add_argument("--out-json", default=None)
    p.add_argument(
        "--gate-on",
        default=None,
        help="게이트 판정에 쓸 파일명 부분문자열 (기본: DV 비중이 가장 높은 파일)",
    )
    args = p.parse_args()

    results = [r for r in (report_one(f) for f in args.collapsed) if r]
    if not results:
        sys.exit(1)

    print(f"\n{'='*72}\n요약\n{'='*72}")
    print(f"  {'프로파일':<38} {'DV/전체':>9} {'DV/스캔':>9}")
    for r in results:
        print(
            f"  {r['file'][:38]:<38} {r['dv_pct_of_all']:>8.2f}% {r['dv_pct_of_scan']:>8.2f}%"
        )

    # 대조군(none) 검증: DV 없는 테이블에서 DV 샘플이 잡히면 분류기 오탐이다.
    ctrl = [r for r in results if "none" in r["file"]]
    for c in ctrl:
        if c["dv_pct_of_all"] > 0.2:
            print(
                f"\n  ⚠️  대조군 {c['file']} 에서 DV 샘플 {c['dv_pct_of_all']:.2f}% 검출.\n"
                f"      DV 가 없는데 잡혔다면 분류기 오탐이거나 잔여 delete 파일이 있습니다.\n"
                f"      이 값을 다른 결과에서 빼고 해석하세요(baseline noise)."
            )

    # 게이트 판정: 가장 보수적이지 않은(=DV 비중 최대) 조건으로 판단.
    # 좁은 스캔이 DV 비중 상한을 준다. 상한조차 낮으면 주제가 성립하지 않는다.
    cand = [r for r in results if "none" not in r["file"]]
    if args.gate_on:
        cand = [r for r in cand if args.gate_on in r["file"]] or cand
    if not cand:
        print("\n게이트 판정 불가: 삭제 있는 프로파일이 없습니다.")
        sys.exit(1)

    best = max(cand, key=lambda r: r["dv_pct_of_all"])
    v, action = verdict(best["dv_pct_of_all"])

    print(f"\n{'='*72}")
    print(f"  게이트 판정: [{v}]   (기준: {best['file']}, DV/전체 = {best['dv_pct_of_all']:.2f}%)")
    print(f"  {action}")
    print(f"{'='*72}\n")
    print(
        "  해석 주의:\n"
        "   - 분모 ALL 에는 JVM 기동/JIT/GC 가 섞여 있어 DV 비중을 과소평가한다.\n"
        "     게이트에서는 이 보수적 방향이 의도된 것이다.\n"
        "   - 이 수치는 '스캔 CPU 안에서의 비중'이지 '쿼리 시간 단축 가능폭'이 아니다.\n"
        "     DV 체크를 0으로 만들어도 절감폭은 이 비중을 넘지 못한다(암달의 법칙).\n"
    )

    if args.out_json:
        with open(args.out_json, "w") as fh:
            json.dump(
                {"results": results, "gate": {"verdict": v, "basis": best["file"],
                                              "dv_pct_of_all": best["dv_pct_of_all"]}},
                fh, indent=2,
            )


if __name__ == "__main__":
    main()
