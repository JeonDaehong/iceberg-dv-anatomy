#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""여러 마크다운 문서를 **하나의 단독 실행 HTML** 로 합친다.

왜 필요한가:
  문서가 아홉 개로 늘었고(README·STORY·findings·BLOG·LINKEDIN·threats·SETUP·
  UPSTREAM-HOWTO·이슈 초안), 서로를 링크한다. 한 파일로 들고 다니며 검토하려면
  링크가 파일 밖으로 나가면 안 된다.

무엇을 하는가:
  - 문서마다 하나의 패널로 만들고 왼쪽 사이드바에서 전환한다 (JS, 외부 의존 없음).
  - 문서 안 제목으로 각 패널의 목차를 만든다.
  - `[텍스트](findings.md#앵커)` 같은 **문서 간 링크를 번들 내부 이동으로 바꾼다.**
    바꾸지 못한 링크만 GitHub 으로 내보낸다.
  - 렌더러는 build_story_html.py 와 같은 문법만 다룬다(이 저장소가 쓰는 것 전부).

사용: python3 tools/build_bundle.py [출력.html]
"""
import io
import os
import re
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/bundle.built.html"
GH = "https://github.com/JeonDaehong/iceberg-dv-anatomy/blob/main/"

# (파일, 사이드바 이름, 한 줄 설명, 그룹)
DOCS = [
    ("README.md",                          "README",        "프로젝트 지도 · 핵심 결과 · 설계 근거", "시작"),
    ("docs/STORY.md",                      "STORY",         "서사 — 왜 했나부터 결론까지 12장",      "시작"),
    ("docs/findings.md",                   "findings",      "근거 원본 — F-001~F-044 시간순",        "시작"),
    ("docs/UPSTREAM-HOWTO.md",             "제출 절차",      "Apache Iceberg 에 내는 방법",           "업스트림"),
    ("upstream/issue-columnarbatchutil.md","이슈 초안",      "그대로 붙여넣을 본문",                  "업스트림"),
    ("upstream/README.md",                 "패치 작업기",    "빌드·검증 절차",                        "업스트림"),
    ("docs/threats.md",                    "threats",       "이 결과를 공격하는 방법과 방어 상태",   "방어"),
    ("docs/BLOG.md",                       "블로그",         "방법론 중심 초안",                      "공개"),
    ("docs/LINKEDIN.md",                   "LinkedIn",      "3종 + 게시 전 점검표",                  "공개"),
    ("docs/SETUP.md",                      "SETUP",         "환경 구축 상세",                        "재현"),
    ("INSTALL.md",                         "INSTALL",       "처음부터 설치",                         "재현"),
]

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SLUG = {f: "d%d" % i for i, (f, *_r) in enumerate(DOCS)}


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def anchor(t):
    t = re.sub(r"[`*]", "", t).strip().lower()
    t = re.sub(r"[^\w가-힣\s-]", "", t)
    return re.sub(r"\s+", "-", t)


def inline(t, docfile):
    t = esc(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", t)

    def link(m):
        txt, href = m.group(1), m.group(2)
        if href.startswith(("http://", "https://", "#")):
            return '<a href="%s"%s>%s</a>' % (
                href, ' target="_blank" rel="noopener"' if href.startswith("http") else "", txt)
        # 문서 간 링크 → 번들 내부 이동
        path, _, frag = href.partition("#")
        for cand in (path, "docs/" + path, path.replace("../", "")):
            cand = cand.lstrip("./")
            if cand in SLUG:
                return '<a href="#" data-go="%s" data-frag="%s">%s</a>' % (
                    SLUG[cand], anchor(frag) if frag else "", txt)
        # 못 바꾼 것은 GitHub 으로
        base = os.path.dirname(docfile)
        full = os.path.normpath(os.path.join(base, path)).replace("\\", "/")
        return '<a href="%s%s" target="_blank" rel="noopener">%s</a>' % (GH, full, txt)

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, t)


def render(md, docfile, sid):
    out, toc = [], []
    lines = md.split("\n")
    i, in_code, buf_code, seen_h1 = 0, False, [], False
    while i < len(lines):
        L = lines[i]
        if L.startswith("```"):
            if in_code:
                out.append("<pre><code>%s</code></pre>" % esc("\n".join(buf_code)))
                buf_code, in_code = [], False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            buf_code.append(L)
            i += 1
            continue

        if L.startswith("|"):
            blk = []
            while i < len(lines) and lines[i].startswith("|"):
                blk.append(lines[i]); i += 1
            rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in blk]
            rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
            if not rows:
                continue
            h = "".join("<th>%s</th>" % inline(c, docfile) for c in rows[0])
            b = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c, docfile) for c in r)
                        for r in rows[1:])
            out.append('<div class="tw"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (h, b))
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", L)
        if m:
            lvl, txt = len(m.group(1)), m.group(2)
            if lvl == 1 and not seen_h1:
                seen_h1 = True
                i += 1
                continue
            aid = "%s-%s" % (sid, anchor(txt))
            if lvl <= 2:
                toc.append((aid, txt, lvl))
            out.append('<h%d id="%s">%s</h%d>' % (lvl, aid, inline(txt, docfile), lvl))
            i += 1
            continue

        if L.startswith(">"):
            b = []
            while i < len(lines) and lines[i].startswith(">"):
                b.append(lines[i].lstrip(">").strip()); i += 1
            out.append("<blockquote>%s</blockquote>" % inline(" ".join(b), docfile))
            continue

        if re.match(r"^\s*[-*]\s+", L):
            b = []
            while i < len(lines) and (re.match(r"^\s*[-*]\s+", lines[i]) or
                                      (lines[i].startswith("  ") and lines[i].strip() and b)):
                if re.match(r"^\s*[-*]\s+", lines[i]):
                    b.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                else:
                    b[-1] += " " + lines[i].strip()
                i += 1
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % inline(x, docfile) for x in b))
            continue

        if re.match(r"^\d+\.\s+", L):
            b = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                b.append(re.sub(r"^\d+\.\s+", "", lines[i])); i += 1
            out.append("<ol>%s</ol>" % "".join("<li>%s</li>" % inline(x, docfile) for x in b))
            continue

        if L.strip() == "---":
            out.append("<hr>"); i += 1; continue
        if L.strip() == "":
            i += 1; continue

        b = [L]; i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(\||#{1,4}\s|>|\s*[-*]\s|\d+\.\s|```|---$)", lines[i]):
            b.append(lines[i]); i += 1
        out.append("<p>%s</p>" % inline(" ".join(b), docfile))
    return "\n".join(out), toc


panels, navs, group_now = [], [], None
for f, name, desc, group in DOCS:
    path = os.path.join(ROOT, f)
    if not os.path.exists(path):
        sys.stderr.write("없음: %s\n" % f); continue
    md = io.open(path, encoding="utf-8").read()
    sid = SLUG[f]
    body, toc = render(md, f, sid)
    n_lines = md.count("\n") + 1
    toc_html = "".join(
        '<a href="#%s" class="l%d">%s</a>' % (a, lv, re.sub(r"[`*]", "", t)) for a, t, lv in toc)
    panels.append(
        '<section class="doc" id="%s" hidden>'
        '<div class="doc-h"><p class="kicker">%s</p><h1>%s</h1><p class="sub">%s</p>'
        '<div class="doc-m"><span>%s</span><span>%s줄</span></div></div>'
        '<div class="doc-b"><nav class="dtoc">%s</nav><article>%s</article></div></section>'
        % (sid, esc(group), esc(name), esc(desc), esc(f), format(n_lines, ","), toc_html, body))
    if group != group_now:
        navs.append('<div class="ng">%s</div>' % esc(group)); group_now = group
    navs.append('<button data-go="%s"><b>%s</b><i>%s</i></button>' % (sid, esc(name), esc(desc)))

HTML = """<title>Deletion Vector Anatomy — 전체 문서</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&family=Source+Sans+3:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap">
<style>
:root{
  --ground:#f6f7f8; --panel:#fff; --ink:#171b21; --ink-2:#3d4650; --muted:#69737f;
  --rule:#dfe3e8; --rule-2:#eef1f4; --teal:#1f6b66; --teal-soft:#e4efee;
  --slate:#4a5b74; --slate-soft:#e7ecf3; --code-bg:#f0f2f5; --side:#eef1f4;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --ground:#0f1318; --panel:#151a21; --ink:#e6e9ed; --ink-2:#bcc4cd; --muted:#8b96a3;
  --rule:#28313b; --rule-2:#1e252d; --teal:#5fb8b0; --teal-soft:#14312f;
  --slate:#9db2cf; --slate-soft:#1b2430; --code-bg:#1a212a; --side:#11161c; } }
:root[data-theme="dark"]{
  --ground:#0f1318; --panel:#151a21; --ink:#e6e9ed; --ink-2:#bcc4cd; --muted:#8b96a3;
  --rule:#28313b; --rule-2:#1e252d; --teal:#5fb8b0; --teal-soft:#14312f;
  --slate:#9db2cf; --slate-soft:#1b2430; --code-bg:#1a212a; --side:#11161c; }
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);margin:0;
  font-family:"Source Sans 3",ui-sans-serif,system-ui,sans-serif;font-size:16px;line-height:1.65}
.app{display:grid;grid-template-columns:1fr;min-height:100vh}
@media(min-width:1040px){.app{grid-template-columns:250px 1fr}}
/* 사이드바 */
aside{background:var(--side);border-right:1px solid var(--rule);padding:20px 0 40px}
@media(min-width:1040px){aside{position:sticky;top:0;height:100vh;overflow-y:auto}}
.brand{padding:0 18px 16px;border-bottom:1px solid var(--rule);margin-bottom:12px}
.brand b{font-family:"Newsreader",Georgia,serif;font-size:19px;display:block;letter-spacing:-.01em}
.brand i{font-style:normal;color:var(--muted);font-size:12px;
  font-family:"JetBrains Mono",monospace}
.ng{padding:14px 18px 6px;font-family:"JetBrains Mono",monospace;font-size:10.5px;
  letter-spacing:.14em;text-transform:uppercase;color:var(--teal)}
aside button{display:block;width:100%;text-align:left;background:none;border:0;cursor:pointer;
  padding:7px 18px;color:var(--ink-2);font:inherit;border-left:2px solid transparent}
aside button b{display:block;font-weight:600;font-size:14.5px}
aside button i{display:block;font-style:normal;font-size:11.5px;color:var(--muted);line-height:1.35}
aside button:hover{background:var(--rule-2)}
aside button[aria-current="true"]{border-left-color:var(--teal);background:var(--panel)}
aside button[aria-current="true"] b{color:var(--teal)}
/* 문서 */
main{min-width:0;padding:0 0 80px}
.doc-h{padding:40px 30px 22px;border-bottom:1px solid var(--rule);max-width:1100px}
.kicker{font-family:"JetBrains Mono",monospace;font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--teal);margin:0 0 10px}
h1{font-family:"Newsreader",Georgia,serif;font-weight:600;font-size:clamp(28px,4vw,40px);
  margin:0 0 10px;line-height:1.12;letter-spacing:-.015em;text-wrap:balance}
.sub{color:var(--ink-2);margin:0;font-size:17px}
.doc-m{display:flex;gap:18px;margin-top:14px;font-family:"JetBrains Mono",monospace;
  font-size:11.5px;color:var(--muted)}
.doc-b{display:grid;grid-template-columns:1fr;gap:0}
@media(min-width:1400px){.doc-b{grid-template-columns:1fr 230px;direction:rtl}
  .doc-b>*{direction:ltr}}
.dtoc{display:none}
@media(min-width:1400px){.dtoc{display:block;position:sticky;top:0;align-self:start;
  max-height:100vh;overflow-y:auto;padding:34px 24px 40px}
  .dtoc a{display:block;padding:3px 0 3px 10px;border-left:2px solid var(--rule-2);
    color:var(--muted);text-decoration:none;font-size:12.5px;line-height:1.35}
  .dtoc a.l1{font-weight:700;color:var(--ink-2);margin-top:8px}
  .dtoc a:hover{color:var(--teal);border-left-color:var(--teal)}}
article{padding:30px;max-width:78ch;min-width:0;overflow-wrap:break-word}
h2{font-family:"Newsreader",Georgia,serif;font-weight:600;font-size:clamp(23px,3vw,30px);
  margin:52px 0 16px;padding-top:22px;border-top:1px solid var(--rule);line-height:1.2;
  text-wrap:balance;scroll-margin-top:12px}
h3{font-weight:700;font-size:18.5px;margin:32px 0 10px;text-wrap:balance;scroll-margin-top:12px}
h4{font-size:15.5px;font-weight:700;margin:22px 0 7px;color:var(--ink-2)}
p{margin:0 0 14px}
ul,ol{margin:0 0 15px;padding-left:22px}
li{margin:0 0 6px}
a{color:var(--teal);text-underline-offset:2px}
strong{font-weight:700;color:var(--ink)}
code{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.86em;background:var(--code-bg);
  padding:1.5px 5px;border-radius:3px;color:var(--ink-2)}
pre{background:var(--code-bg);border:1px solid var(--rule);border-radius:6px;padding:15px 17px;
  overflow-x:auto;margin:0 0 19px;line-height:1.5}
pre code{background:none;padding:0;font-size:12.6px}
blockquote{margin:0 0 19px;padding:13px 17px;border-left:3px solid var(--slate);
  background:var(--slate-soft);border-radius:0 5px 5px 0;color:var(--ink-2);font-size:15.2px}
blockquote strong{color:var(--ink)}
hr{border:0;border-top:1px solid var(--rule-2);margin:30px 0}
h2+hr,hr+h2{display:none}
.tw{overflow-x:auto;margin:0 0 22px;border:1px solid var(--rule);border-radius:7px;
  background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13.8px;font-variant-numeric:tabular-nums}
th{text-align:left;font-weight:700;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);padding:10px 13px;border-bottom:1px solid var(--rule);white-space:nowrap;
  background:var(--ground)}
td{padding:9px 13px;border-bottom:1px solid var(--rule-2);vertical-align:top;color:var(--ink-2)}
tbody tr:last-child td{border-bottom:0}
td strong{color:var(--ink)}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
:focus-visible{outline:2px solid var(--teal);outline-offset:2px;border-radius:2px}
</style>

<div class="app">
<aside>
  <div class="brand"><b>Deletion Vector Anatomy</b><i>__NDOC__개 문서 · 발견 __NF__개</i></div>
  __NAV__
</aside>
<main>__PANELS__</main>
</div>

<script>
(function(){
  const btns = Array.from(document.querySelectorAll("aside button[data-go]"));
  function show(id, frag){
    document.querySelectorAll("section.doc").forEach(s => s.hidden = (s.id !== id));
    btns.forEach(b => b.setAttribute("aria-current", String(b.dataset.go === id)));
    try { location.hash = id + (frag ? "/" + frag : ""); } catch(e){}
    const t = frag && document.getElementById(id + "-" + frag);
    if (t) t.scrollIntoView({block:"start"}); else window.scrollTo(0,0);
  }
  btns.forEach(b => b.addEventListener("click", () => show(b.dataset.go, "")));
  // 문서 간 링크
  document.addEventListener("click", e => {
    const a = e.target.closest("a[data-go]");
    if (!a) return;
    e.preventDefault();
    show(a.dataset.go, a.dataset.frag || "");
  });
  const h = (location.hash || "").replace(/^#/, "").split("/");
  show(document.getElementById(h[0]) ? h[0] : "d0", h[1] || "");
})();
</script>
"""

nf = len(re.findall(r"^## F-0", io.open(os.path.join(ROOT, "docs/findings.md"),
                                       encoding="utf-8").read(), re.M))
html = (HTML.replace("__NAV__", "\n".join(navs))
            .replace("__PANELS__", "\n".join(panels))
            .replace("__NDOC__", str(len(panels)))
            .replace("__NF__", str(nf)))
io.open(os.path.join(ROOT, OUT), "w", encoding="utf-8", newline="\n").write(html)
print("생성: %s (%d KB, 문서 %d개, 발견 %d개)" % (OUT, len(html.encode()) / 1024, len(panels), nf))
