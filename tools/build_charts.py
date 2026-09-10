# -*- coding: utf-8 -*-
"""블로그용 차트를 만든다.

숫자는 전부 docs/findings.md 와 docs/BLOG.md 의 표에서 그대로 옮긴 것이다.
여기서 계산하거나 보정하지 않는다 — 글의 표와 그림이 어긋나면 안 된다.

오차 막대는 3회 이상 반복의 실측 범위다. 노이즈 바닥(중앙값 9.7%)보다
작은 차이는 주장하지 않는다는 이 저장소의 규칙을 그림에서도 보이게 하려고 넣었다.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

rcParams["font.family"] = "Malgun Gothic"
rcParams["axes.unicode_minus"] = False

INK   = "#1a1a1a"
MUTED = "#8a8a8a"
GRID  = "#e4e4e4"
ACC   = "#c2410c"   # 측정값
BASE  = "#9ca3af"   # 현행
PATCH = "#0f766e"   # 패치본
ARRAY = "#fef3e2"
BITMAP= "#e8f2f1"

OUT = "docs/img"


def frame(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=GRID, lw=0.8)


def save(fig, name):
    fig.savefig("%s/%s.png" % (OUT, name), dpi=140,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  %s.png" % name)


def chart_curve():
    """§1 — 삭제율에 따른 DV 체크 비용. 이 글의 표지 그림이다."""
    labels = ["0.5%", "5%", "6.1%", "6.25%", "7%", "12%", "50%"]
    med    = [599, 746, 872, 654, 394, 422, 496]
    lo     = [576, 700, 838, 617, 381, 410, 454]
    hi     = [633, 798, 909, 702, 406, 447, 529]
    x = list(range(len(med)))
    L, R = -1.0, 6.45

    fig, ax = plt.subplots(figsize=(9, 5.2))
    frame(ax)

    # 컨테이너가 바뀌는 지점 — 4096/65536 = 6.25%
    ax.axvspan(L, 3, color=ARRAY, zorder=0)
    ax.axvspan(3, R, color=BITMAP, zorder=0)
    ax.axvline(3, color=MUTED, ls="--", lw=1, zorder=1)
    ax.text(3.08, 1035, "4096 / 65536 = 6.25%", color=MUTED, fontsize=9.5,
            va="center", ha="left")
    ax.text(0.05, 250, "정렬 배열 컨테이너\n이진 탐색", color=MUTED,
            fontsize=10.5, linespacing=1.6, ha="left")
    ax.text(6.3, 900, "비트맵 컨테이너\nO(1)", color=MUTED,
            fontsize=10.5, linespacing=1.6, ha="right")

    # 정점과 최저를 잇는 눈금 — 데이터 선을 덮지 않게 왼쪽 바깥에 세운다
    for y in (872, 394):
        ax.plot([-0.62, 2 if y == 872 else 4], [y, y], color=MUTED,
                ls=":", lw=1, zorder=1)
    ax.annotate("", xy=(-0.62, 872), xytext=(-0.62, 394),
                arrowprops=dict(arrowstyle="<->", color=INK, lw=1.3))
    ax.text(-0.72, 633, "2.22배", color=INK, fontsize=12.5,
            fontweight="bold", rotation=90, va="center", ha="center")

    err = [[m - l for m, l in zip(med, lo)], [h - m for m, h in zip(med, hi)]]
    ax.errorbar(x, med, yerr=err, color=ACC, lw=2.2, marker="o", ms=6,
                capsize=4, elinewidth=1.2, zorder=3)

    ax.annotate("정점 872", xy=(2, 915), xytext=(2, 995), ha="center",
                color=ACC, fontsize=11.5, fontweight="bold",
                arrowprops=dict(arrowstyle="-", color=ACC, lw=1))
    ax.annotate("최저 394", xy=(4, 375), xytext=(4, 290), ha="center",
                color=ACC, fontsize=11.5, fontweight="bold",
                arrowprops=dict(arrowstyle="-", color=ACC, lw=1))

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(180, 1080)
    ax.set_xlim(L, R)
    ax.set_xlabel("삭제율 d   (x축은 등간격이 아니다)", color=MUTED,
                  fontsize=10, labelpad=10)
    ax.set_ylabel("DV 체크 CPU 샘플", color=MUTED, fontsize=10)
    ax.set_title("덜 지웠는데 더 비싸다", color=INK, fontsize=16,
                 fontweight="bold", loc="left", pad=34)
    ax.text(0, 1.035, "삭제율 6.1% 가 7% 보다 2.22배 비싸다 · 오차 막대는 3회 반복의 실측 범위",
            transform=ax.transAxes, color=MUTED, fontsize=10)
    save(fig, "01-density-curve")


def chart_share():
    """§3 — 같은 절감이라도 분모가 커지면 비중은 사라진다."""
    labels = ["1 컬럼", "3 컬럼", "5 컬럼", "20 컬럼"]
    vals   = [53.2, 24.0, 16.0, 3.75]

    fig, ax = plt.subplots(figsize=(9, 4.8))
    frame(ax)
    bars = ax.bar(labels, vals, color=[ACC, ACC, MUTED, MUTED], width=0.55)
    bars[0].set_color(ACC)
    bars[1].set_color("#e0864f")
    bars[2].set_color("#bfa695")
    bars[3].set_color(BASE)

    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.6, "%.4g%%" % v,
                ha="center", color=INK, fontsize=12, fontweight="bold")

    ax.axhline(21, color=MUTED, ls="--", lw=1)
    ax.text(3.42, 22.5, "손익분기 21%", color=MUTED, fontsize=10, ha="right")

    ax.set_ylim(0, 62)
    ax.set_ylabel("DV 체크가 스캔 CPU 에서 차지하는 비중", color=MUTED, fontsize=10)
    ax.set_title("\"DV 체크가 CPU 의 절반\" 은 분모를 밝혀야 참이 된다",
                 color=INK, fontsize=16, fontweight="bold", loc="left", pad=34)
    ax.text(0, 1.035, "절감되는 절대 CPU 시간은 같다. 달라지는 건 비율뿐이다.",
            transform=ax.transAxes, color=MUTED, fontsize=9.5)
    save(fig, "02-projection-width")


def chart_patch():
    """§4 — 현행과 패치본. 같은 테이블, jar 만 교체."""
    labels = ["0.5%\narray", "6.1%\narray", "7.0%\nbitmap", "0.5% 정렬\nrun"]
    cur    = [674, 873, 420, 185]
    pat    = [79, 94, 113, 61]
    # 표에 적힌 값을 그대로 쓴다. 여기서 cur/pat 로 나누면 반올림이 달라져
    # 본문 표(9.28배)와 그림(9.29배)이 어긋난다.
    ratio  = ["8.56배", "9.28배", "3.73배", "3.05배"]
    x = list(range(len(cur)))
    w = 0.36

    fig, ax = plt.subplots(figsize=(9, 5.0))
    frame(ax)
    b1 = ax.bar([i - w / 2 for i in x], cur, w, label="현행", color=BASE)
    b2 = ax.bar([i + w / 2 for i in x], pat, w, label="패치본", color=PATCH)

    for i in x:
        ax.text(i - w / 2, cur[i] + 16, str(cur[i]), ha="center",
                color=MUTED, fontsize=10)
        ax.text(i + w / 2, pat[i] + 16, str(pat[i]), ha="center",
                color=PATCH, fontsize=10, fontweight="bold")
        ax.text(i, -118, ratio[i], ha="center",
                color=INK, fontsize=12, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylim(0, 990)
    ax.set_ylabel("DV 체크 CPU 샘플", color=MUTED, fontsize=10)
    ax.set_title("175줄을 고친 뒤 — 같은 테이블, jar 만 교체",
                 color=INK, fontsize=16, fontweight="bold", loc="left", pad=34)
    ax.text(0, 1.035, "베이스라인도 같은 소스 트리에서 패치만 빼고 다시 빌드한 것이다.",
            transform=ax.transAxes, color=MUTED, fontsize=9.5)
    leg = ax.legend(frameon=False, loc="upper right", fontsize=11)
    for t in leg.get_texts():
        t.set_color(INK)
    save(fig, "03-patched-vs-current")


if __name__ == "__main__":
    print("차트 생성:")
    chart_curve()
    chart_share()
    chart_patch()
