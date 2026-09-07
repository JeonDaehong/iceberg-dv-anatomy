#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dv_inspect.py 가 뽑은 컨테이너 CSV 한 개를 한 줄로 요약한다.

컬럼 이름은 dv_inspect 버전에 따라 다를 수 있으므로 후보를 여러 개 본다.
사용: python3 container_summary.py <csv> <라벨>
"""
import csv
import io
import sys
from collections import Counter

OUT = io.open(1, "w", encoding="utf-8", closefd=False)


def num(row, *names):
    for n in names:
        v = row.get(n)
        if v not in ("", None):
            try:
                return float(v)
            except ValueError:
                pass
    return 0.0


def main():
    path, label = sys.argv[1], sys.argv[2]
    rows = list(csv.DictReader(io.open(path, encoding="utf-8")))
    if not rows:
        OUT.write("    %-6s 컨테이너 0개\n" % label)
        return

    # dv_inspect 의 실제 컬럼: source,blob,key,container,cardinality,runs,bytes,chunk,hi_key
    kinds = Counter(r.get("container") or r.get("type") or r.get("container_type") or "?"
                    for r in rows)
    card = sum(num(r, "cardinality", "card", "n") for r in rows) / len(rows)
    runs = [num(r, "n_runs", "nRuns", "runs", "num_runs") for r in rows]
    runs = [x for x in runs if x > 0]
    total_bytes = sum(num(r, "bytes", "size", "serialized_bytes", "size_bytes") for r in rows)

    OUT.write("    %-6s 컨테이너 %3d개  %-28s 평균card %6.0f  평균nRuns %8s  합계 %12s B\n"
              % (label, len(rows),
                 ", ".join("%s:%d" % (k, v) for k, v in sorted(kinds.items())),
                 card,
                 ("%.1f" % (sum(runs) / len(runs))) if runs else "-",
                 format(int(total_bytes), ",")))


main()
