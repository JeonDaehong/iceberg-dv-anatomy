# -*- coding: utf-8 -*-
"""마크다운 이미지를 HTML 로 바꾼다.

두 변환기(build_story_html.py, build_bundle.py)가 같이 쓴다.

이미지는 base64 로 박아 넣는다. 이 HTML 들은 **파일 하나만 옮겨도 열려야 하는**
산출물이라, 상대 경로로 두면 다른 노트북에서 그림이 전부 깨진다.
"""
import base64
import io
import os
import re

_CACHE = {}

# 마크다운 이미지 문법보다 링크 문법이 먼저 돌면 앞의 ! 가 남는다.
# 그래서 변환기에서는 반드시 링크 처리보다 먼저 이 함수를 호출한다.
PAT = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def uri(src):
    if src in _CACHE:
        return _CACHE[src]
    for cand in (src, os.path.join("docs", src)):
        if os.path.exists(cand):
            raw = io.open(cand, "rb").read()
            got = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
            _CACHE[src] = got
            return got
    raise SystemExit("이미지를 찾을 수 없다: " + src)


def images(t):
    def one(m):
        alt, src = m.group(1), m.group(2)
        return ('<span class="fig"><img src="%s" alt="%s">'
                '<span class="cap">%s</span></span>' % (uri(src), alt, alt))
    return PAT.sub(one, t)


CSS = """
.fig{display:block;margin:22px 0}
.fig img{display:block;width:100%;height:auto;
  border:1px solid var(--line,#e4e4e4);border-radius:6px}
.fig .cap{display:block;margin-top:9px;font-size:12.8px;
  line-height:1.6;color:var(--muted,#8a8a8a)}
"""
