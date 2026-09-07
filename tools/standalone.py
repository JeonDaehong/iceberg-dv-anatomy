#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""아티팩트 조각(fragment) HTML 을 **단독 실행 가능한** HTML 파일로 감싼다.

왜 필요한가:
  아티팩트로 게시할 때는 호스트가 `<!doctype html><head>…</head><body>` 를 자동으로
  붙여준다. 그래서 소스 파일에는 `<title>` 과 `<style>` 만 있다.
  그런데 그 파일을 **메일에 첨부하거나 브라우저로 직접 열면** charset 선언이 없어
  한글이 깨진다. 저장소에 두는 `docs/*.built.html` 은 그런 용도이므로 감싸서 둔다.

사용: python3 tools/standalone.py <입력.html> <출력.html> [제목]
"""
import io
import re
import sys

SKELETON = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
%(head)s</head>
<body>
%(body)s</body>
</html>
"""

# 아티팩트 호스트가 넣어주는 최소 리셋. 단독 파일에서도 같게 보이도록 복제한다.
RESET = """<style>
  html{color-scheme:light dark}
  body{margin:0; font:14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif}
  img{max-width:100%}
  [hidden]{display:none !important}
</style>
"""


def main():
    src, dst = sys.argv[1], sys.argv[2]
    s = io.open(src, encoding="utf-8").read()

    if re.search(r"<!doctype", s, re.I):
        io.open(dst, "w", encoding="utf-8", newline="\n").write(s)
        print("이미 단독 파일이다 — 그대로 복사: %s" % dst)
        return

    # <title> / <link rel=stylesheet> / <style> 는 head 로, 나머지는 body 로 보낸다.
    head_parts = []

    def take(pattern):
        nonlocal s
        out = []
        for m in re.finditer(pattern, s, re.I | re.S):
            out.append(m.group(0))
        for frag in out:
            s = s.replace(frag, "", 1)
        return out

    head_parts += take(r"<title>.*?</title>")
    head_parts += take(r"<link\b[^>]*>")
    # RESET 을 페이지 자체 <style> 보다 **먼저** 넣어야 페이지 규칙이 이긴다.
    head = "".join(x + "\n" for x in head_parts) + RESET
    head += "".join(x + "\n" for x in take(r"<style\b.*?</style>"))

    if len(sys.argv) > 3 and "<title>" not in head:
        head = "<title>%s</title>\n" % sys.argv[3] + head

    out = SKELETON % {"head": head, "body": s.strip() + "\n"}
    io.open(dst, "w", encoding="utf-8", newline="\n").write(out)
    print("단독 파일 생성: %s (%s bytes)" % (dst, format(len(out), ",")))


main()
