#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docs/STORY.md -> 읽기용 HTML 아티팩트.

마크다운 전체를 손으로 옮기지 않고 변환한다. 이 문서에 실제로 쓰인 문법만 다룬다:
제목, 표, 목록, 코드블록, 인용, 굵게/기울임/인라인코드, 링크, 수평선.
"""
import io
import os
import re
import sys

# 원래 STORY.md 전용이었다. BLOG.md 도 같은 문법만 쓰므로 소스를 인자로 받게 열었다.
#   build_story_html.py [출력]            -> docs/STORY.md 변환 (기존 동작 유지)
#   build_story_html.py [출력] [소스.md]
DST = sys.argv[1] if len(sys.argv) > 1 else "story.html"
SRC = sys.argv[2] if len(sys.argv) > 2 else "docs/STORY.md"

# 문서마다 머리말이 다르다. 소스 파일명으로 고른다.
HEADS = {
    "STORY.md": (
        "Apache Iceberg V3 · 읽기 경로 해부",
        "Deletion Vector Anatomy",
        "행마다 삭제 여부를 묻는 루프가 스캔 CPU 의 절반을 먹는다. 그걸 찾고, 고치고, "
        "내 노트북 밖에서도 성립하는지 확인한 기록 — 틀렸던 것까지 포함해서.",
    ),
    "BLOG.md": (
        "Apache Iceberg V3 · Deletion Vector",
        "삭제를 더 많이 할수록 스캔이 싸진다",
        "Roaring 컨테이너가 6.25% 에서 갈리고, 그 경계 바로 아래가 가장 비싸다. "
        "찾고, 고치고, 15번 빗나간 기록.",
    ),
}
KICKER, TITLE, SUB = HEADS.get(os.path.basename(SRC), HEADS["STORY.md"])

md = io.open(SRC, encoding="utf-8").read()


def esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def inline(t):
    t = esc(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", t)
    # [텍스트](링크)
    def link(m):
        txt, href = m.group(1), m.group(2)
        cls = ' class="ref"' if href.startswith("findings.md") else ""
        if href.endswith(".md") or href.startswith("../"):
            href = "https://github.com/JeonDaehong/iceberg-dv-anatomy/blob/main/docs/" + href.lstrip("./")
            href = href.replace("/docs/../", "/")
        return '<a href="%s"%s>%s</a>' % (href, cls, txt)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, t)
    return t


def slug(t):
    t = re.sub(r"[`*]", "", t).strip().lower()
    t = re.sub(r"[^\w가-힣\s-]", "", t)
    return re.sub(r"\s+", "-", t)


STATUS = [
    ("🔜 **진행 중**", "run", "진행 중"),
    ("🔜 **예정**", "todo", "예정"),
    ("🔜 **대기**", "todo", "대기"),
    ("⛔ **막힘**", "blocked", "막힘"),
    ("🟡 **미확정**", "unsure", "미확정"),
]


def chips(cell):
    for needle, cls, label in STATUS:
        if needle in cell:
            return cell.replace(needle, '<span class="chip %s">%s</span>' % (cls, label))
    return cell


out = []
lines = md.split("\n")
i = 0
in_code = False
code_buf = []
toc = []

while i < len(lines):
    L = lines[i]

    if L.startswith("```"):
        if in_code:
            out.append('<pre><code>%s</code></pre>' % esc("\n".join(code_buf)))
            code_buf, in_code = [], False
        else:
            in_code = True
        i += 1
        continue
    if in_code:
        code_buf.append(L)
        i += 1
        continue

    if L.startswith("|"):
        block = []
        while i < len(lines) and lines[i].startswith("|"):
            block.append(lines[i]); i += 1
        rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in block]
        rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
        if not rows:
            continue
        head, body = rows[0], rows[1:]
        h = "".join("<th>%s</th>" % inline(chips(c)) for c in head)
        b = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(chips(c)) for c in r)
                    for r in body)
        out.append('<div class="tw"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (h, b))
        continue

    m = re.match(r"^(#{1,4})\s+(.*)$", L)
    if m:
        lvl, txt = len(m.group(1)), m.group(2)
        sid = slug(txt)
        if lvl == 2 and not txt.startswith("목차"):
            toc.append((sid, txt))
        eyebrow = ""
        num = re.match(r"^(\d+)\.\s+(.*)$", re.sub(r"^[🔜⛔🟡]\s*", "", txt))
        if lvl == 2 and num:
            eyebrow = '<span class="num">%s</span>' % num.group(1)
        out.append('<h%d id="%s">%s%s</h%d>' % (lvl, sid, eyebrow, inline(txt), lvl))
        i += 1
        continue

    if L.startswith(">"):
        buf = []
        while i < len(lines) and lines[i].startswith(">"):
            buf.append(lines[i].lstrip(">").strip()); i += 1
        out.append("<blockquote>%s</blockquote>" % inline(" ".join(buf)))
        continue

    if re.match(r"^\s*[-*]\s+", L):
        buf = []
        while i < len(lines) and (re.match(r"^\s*[-*]\s+", lines[i]) or
                                  (lines[i].startswith("  ") and lines[i].strip() and buf)):
            if re.match(r"^\s*[-*]\s+", lines[i]):
                buf.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
            else:
                buf[-1] += " " + lines[i].strip()
            i += 1
        out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % inline(x) for x in buf))
        continue

    if re.match(r"^\d+\.\s+", L):
        buf = []
        while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
            buf.append(re.sub(r"^\d+\.\s+", "", lines[i])); i += 1
        out.append("<ol>%s</ol>" % "".join("<li>%s</li>" % inline(x) for x in buf))
        continue

    if L.strip() == "---":
        out.append('<hr>')
        i += 1
        continue

    if L.strip() == "":
        i += 1
        continue

    buf = [L]
    i += 1
    while i < len(lines) and lines[i].strip() and not re.match(
            r"^(\||#{1,4}\s|>|\s*[-*]\s|\d+\.\s|```|---$)", lines[i]):
        buf.append(lines[i]); i += 1
    out.append("<p>%s</p>" % inline(" ".join(buf)))

body_html = "\n".join(out)
toc_html = "".join('<a href="#%s">%s</a>' % (sid, re.sub(r"[`*]", "", t)) for sid, t in toc)

HTML = """<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&family=Source+Sans+3:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap">
<style>
:root{
  --ground:#f6f7f8; --panel:#ffffff; --ink:#171b21; --ink-2:#3d4650; --muted:#69737f;
  --rule:#dfe3e8; --rule-2:#eef1f4;
  --teal:#1f6b66; --teal-soft:#e4efee;
  --amber:#9a6218; --amber-soft:#f7eddc;
  --rust:#96393b; --rust-soft:#f6e5e4;
  --slate:#4a5b74; --slate-soft:#e7ecf3;
  --code-bg:#f0f2f5;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#0f1318; --panel:#151a21; --ink:#e6e9ed; --ink-2:#bcc4cd; --muted:#8b96a3;
    --rule:#28313b; --rule-2:#1e252d;
    --teal:#5fb8b0; --teal-soft:#14312f;
    --amber:#d9a css; --amber:#d9a75a; --amber-soft:#33280f;
    --rust:#d98785; --rust-soft:#331d1d;
    --slate:#9db2cf; --slate-soft:#1b2430;
    --code-bg:#1a212a;
  }
}
:root[data-theme="dark"]{
  --ground:#0f1318; --panel:#151a21; --ink:#e6e9ed; --ink-2:#bcc4cd; --muted:#8b96a3;
  --rule:#28313b; --rule-2:#1e252d;
  --teal:#5fb8b0; --teal-soft:#14312f;
  --amber:#d9a75a; --amber-soft:#33280f;
  --rust:#d98785; --rust-soft:#331d1d;
  --slate:#9db2cf; --slate-soft:#1b2430;
  --code-bg:#1a212a;
}
*{box-sizing:border-box}
body{
  background:var(--ground); color:var(--ink);
  font-family:"Source Sans 3", ui-sans-serif, system-ui, sans-serif;
  font-size:16.5px; line-height:1.68; margin:0;
  -webkit-font-smoothing:antialiased;
}
.wrap{display:grid; grid-template-columns:minmax(0,1fr); max-width:1180px; margin:0 auto; padding:0 22px 96px}
@media(min-width:1060px){ .wrap{grid-template-columns:236px minmax(0,1fr); gap:56px} }

/* ── 헤더 ── */
header.top{border-bottom:1px solid var(--rule); margin-bottom:44px; padding:56px 0 30px}
.kicker{
  font-family:"JetBrains Mono", ui-monospace, monospace; font-size:11.5px;
  letter-spacing:.16em; text-transform:uppercase; color:var(--teal); margin:0 0 14px
}
h1{
  font-family:"Newsreader", Georgia, serif; font-weight:600; font-size:clamp(34px,5.2vw,52px);
  line-height:1.1; letter-spacing:-.015em; margin:0 0 16px; text-wrap:balance;
}
.sub{color:var(--ink-2); font-size:18px; max-width:64ch; margin:0}
.meta{
  display:flex; flex-wrap:wrap; gap:8px 20px; margin-top:22px;
  font-family:"JetBrains Mono", monospace; font-size:12px; color:var(--muted);
}
.meta b{color:var(--ink-2); font-weight:700}

/* ── 목차 ── */
nav.toc{display:none}
@media(min-width:1060px){
  nav.toc{
    display:block; position:sticky; top:0; align-self:start;
    max-height:100vh; overflow-y:auto; padding:56px 0 40px;
  }
  nav.toc a{
    display:block; padding:5px 0 5px 12px; border-left:2px solid var(--rule-2);
    color:var(--muted); text-decoration:none; font-size:13.5px; line-height:1.4;
  }
  nav.toc a:hover{color:var(--teal); border-left-color:var(--teal)}
}
main{min-width:0}

/* ── 타이포 ── */
h2{
  font-family:"Newsreader", Georgia, serif; font-weight:600;
  font-size:clamp(25px,3.2vw,33px); line-height:1.22; letter-spacing:-.01em;
  margin:64px 0 18px; padding-top:26px; border-top:1px solid var(--rule);
  text-wrap:balance; position:relative;
}
h2 .num{
  font-family:"JetBrains Mono", monospace; font-size:12px; color:var(--teal);
  display:block; margin-bottom:8px; letter-spacing:.1em;
}
h3{
  font-family:"Source Sans 3", sans-serif; font-weight:700; font-size:19.5px;
  margin:38px 0 12px; letter-spacing:-.005em; text-wrap:balance; color:var(--ink);
}
h4{font-size:16px; font-weight:700; margin:26px 0 8px; color:var(--ink-2)}
p{margin:0 0 15px; max-width:70ch}
ul,ol{margin:0 0 16px; padding-left:22px; max-width:70ch}
li{margin:0 0 7px}
a{color:var(--teal); text-underline-offset:2px; text-decoration-thickness:1px}
a.ref{
  font-family:"JetBrains Mono", monospace; font-size:.86em;
  background:var(--teal-soft); color:var(--teal); padding:1px 5px; border-radius:3px;
  text-decoration:none; white-space:nowrap;
}
strong{font-weight:700; color:var(--ink)}
code{
  font-family:"JetBrains Mono", ui-monospace, monospace; font-size:.86em;
  background:var(--code-bg); padding:1.5px 5px; border-radius:3px; color:var(--ink-2);
}
pre{
  background:var(--code-bg); border:1px solid var(--rule); border-radius:6px;
  padding:16px 18px; overflow-x:auto; margin:0 0 20px; line-height:1.55;
}
pre code{background:none; padding:0; font-size:12.8px; color:var(--ink-2)}
blockquote{
  margin:0 0 20px; padding:14px 18px; border-left:3px solid var(--slate);
  background:var(--slate-soft); border-radius:0 5px 5px 0; color:var(--ink-2);
  font-size:15.5px; max-width:72ch;
}
blockquote strong{color:var(--ink)}
hr{border:0; border-top:1px solid var(--rule-2); margin:34px 0}
h2 + hr, hr + h2{display:none}

/* ── 표 ── */
.tw{overflow-x:auto; margin:0 0 24px; border:1px solid var(--rule); border-radius:7px; background:var(--panel)}
table{border-collapse:collapse; width:100%; font-size:14.2px; font-variant-numeric:tabular-nums}
th{
  text-align:left; font-weight:700; font-size:11.5px; letter-spacing:.07em;
  text-transform:uppercase; color:var(--muted); padding:11px 14px;
  border-bottom:1px solid var(--rule); white-space:nowrap; background:var(--ground);
}
td{padding:10px 14px; border-bottom:1px solid var(--rule-2); vertical-align:top; color:var(--ink-2)}
tbody tr:last-child td{border-bottom:0}
td strong{color:var(--ink)}
td code{font-size:12.4px}

/* ── 상태 칩 ── */
.chip{
  display:inline-block; font-family:"JetBrains Mono", monospace; font-size:10.5px;
  font-weight:700; letter-spacing:.06em; padding:2.5px 8px; border-radius:100px;
  white-space:nowrap; border:1px solid transparent;
}
.chip.todo{background:var(--amber-soft); color:var(--amber); border-color:var(--amber)}
.chip.run{background:var(--teal-soft); color:var(--teal); border-color:var(--teal)}
.chip.blocked{background:var(--rust-soft); color:var(--rust); border-color:var(--rust)}
.chip.unsure{background:var(--slate-soft); color:var(--slate); border-color:var(--slate)}

@media (prefers-reduced-motion: reduce){*{animation:none!important; transition:none!important}}
:focus-visible{outline:2px solid var(--teal); outline-offset:2px; border-radius:2px}
</style>

<div class="wrap">
<nav class="toc">__TOC__</nav>
<main>
<header class="top">
  <p class="kicker">__KICKER__</p>
  <h1>__TITLE__</h1>
  <p class="sub">__SUB__</p>
  <div class="meta">
    <span>발견 <b>__NF__</b>개</span>
    <span>측정 축 <b>__NAX__</b>개</span>
    <span>빗나간 예측 <b>15</b>개</span>
    <span>Spark <b>4.0.4</b> · Iceberg <b>1.11.0</b></span>
    <span>최종 <b>2026-09-08</b></span>
  </div>
</header>
__BODY__
</main>
</div>
"""

nf = len(re.findall(r"^## F-0", io.open("docs/findings.md", encoding="utf-8").read(), re.M))
nax = len([f for f in os.listdir("phase2/scripts") if re.match(r"^\d\d.*\.sh$", f)])

HTML = (HTML.replace("__TOC__", toc_html)
            .replace("__BODY__", body_html)
            .replace("__KICKER__", KICKER)
            .replace("__TITLE__", TITLE)
            .replace("__SUB__", SUB)
            .replace("__NF__", str(nf))
            .replace("__NAX__", str(nax)))
# 오타로 들어간 잔여 토큰 정리
HTML = HTML.replace("--amber:#d9a css; ", "")

io.open(DST, "w", encoding="utf-8", newline="\n").write(HTML)
print("생성: %s (%d bytes, 발견 %d개)" % (DST, len(HTML), nf))
