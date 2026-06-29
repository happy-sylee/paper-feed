#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
논문 피드 빌더
- feeds.txt 의 저널 RSS 를 모두 읽어서
- 최근 N일 이내 + 키워드 매칭되는 논문만 골라
- index.html (보기 좋은 피드 페이지) 를 생성한다.
GitHub Actions 가 매일 자동으로 이 스크립트를 실행한다.
"""

import html
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser  # RSS 파서

# ------------------- 설정 -------------------
DAYS_BACK = 14          # 최근 며칠 치를 보여줄지
HERE = Path(__file__).parent
FEEDS_FILE = HERE / "feeds.txt"
KEYWORDS_FILE = HERE / "keywords.txt"
OUTPUT_FILE = HERE / "index.html"
# --------------------------------------------


def load_feeds():
    """feeds.txt 를 읽어 (저널이름, URL) 목록 반환"""
    feeds = []
    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            continue
        name, url = line.split("|", 1)
        feeds.append((name.strip(), url.strip()))
    return feeds


def load_keywords():
    """keywords.txt 를 읽어 소문자 키워드 목록 반환"""
    kws = []
    for line in KEYWORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kws.append(line.lower())
    return kws


def clean_html(raw):
    """초록에 섞인 HTML 태그 제거 + 공백 정리"""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)      # 태그 제거
    text = html.unescape(text)               # &amp; 같은 거 복원
    text = re.sub(r"\s+", " ", text).strip()  # 공백 정리
    return text


def get_entry_date(entry):
    """논문 발행일을 datetime(UTC)으로 반환. 없으면 None."""
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return None


def matches_keywords(text, keywords):
    """text 안에 키워드가 하나라도 있으면 True"""
    low = text.lower()
    return any(kw in low for kw in keywords)


def main():
    feeds = load_feeds()
    keywords = load_keywords()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DAYS_BACK)

    all_items = []
    log = []  # 저널별 처리 결과 기록 (디버깅용)

    for name, url in feeds:
        try:
            parsed = feedparser.parse(url)
            n_total = len(parsed.entries)
            n_kept = 0

            for entry in parsed.entries:
                title = clean_html(entry.get("title", ""))
                # 초록 후보: summary, description, content 순으로 탐색
                abstract = clean_html(entry.get("summary", ""))
                if not abstract and entry.get("content"):
                    try:
                        abstract = clean_html(entry["content"][0].get("value", ""))
                    except Exception:
                        abstract = ""

                link = entry.get("link", "")
                date = get_entry_date(entry)

                # 날짜 필터 (날짜 정보 없으면 일단 포함)
                if date and date < cutoff:
                    continue

                # 키워드 필터 (제목+초록 기준)
                haystack = title + " " + abstract
                if not matches_keywords(haystack, keywords):
                    continue

                all_items.append({
                    "journal": name,
                    "title": title or "(제목 없음)",
                    "abstract": abstract,
                    "link": link,
                    "date": date,
                })
                n_kept += 1

            log.append(f"  [OK]  {name}: {n_total}개 중 {n_kept}개 매칭")
        except Exception as e:
            log.append(f"  [실패] {name}: {e}")

    # 최신순 정렬 (날짜 없는 건 맨 뒤로)
    all_items.sort(
        key=lambda x: x["date"] or datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )

    html_out = render_html(all_items, now)
    OUTPUT_FILE.write_text(html_out, encoding="utf-8")

    # GitHub Actions 로그에 결과 출력
    print(f"총 {len(all_items)}개 논문 수집 (최근 {DAYS_BACK}일)")
    print("\n".join(log))


def render_html(items, now):
    """수집한 논문 목록을 HTML 문자열로 변환"""
    now_str = now.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")  # KST

    cards = []
    for it in items:
        date_str = ""
        if it["date"]:
            date_str = it["date"].astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

        abstract_html = ""
        if it["abstract"]:
            abstract_html = f'<p class="abstract">{html.escape(it["abstract"])}</p>'
        else:
            abstract_html = '<p class="abstract no-abstract">초록 없음 — 제목을 눌러 원문에서 확인</p>'

        cards.append(f"""
        <article class="card">
          <div class="card-head">
            <span class="journal">{html.escape(it['journal'])}</span>
            <span class="date">{date_str}</span>
          </div>
          <h2 class="title"><a href="{html.escape(it['link'])}" target="_blank" rel="noopener">{html.escape(it['title'])}</a></h2>
          {abstract_html}
        </article>""")

    cards_html = "\n".join(cards) if cards else '<p class="empty">최근 14일간 조건에 맞는 논문이 없어요.</p>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>논문 피드</title>
<style>
  :root {{
    --bg: #0f1115;
    --card: #1a1d24;
    --border: #2a2e38;
    --text: #e8eaed;
    --muted: #9aa0a6;
    --accent: #6ea8fe;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
    line-height: 1.6;
  }}
  header {{
    position: sticky; top: 0; z-index: 10;
    background: rgba(15,17,21,0.92); backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--border);
    padding: 18px 20px;
  }}
  header h1 {{ margin: 0; font-size: 20px; }}
  header .meta {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  .wrap {{ max-width: 820px; margin: 0 auto; padding: 20px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 14px; padding: 18px 20px; margin-bottom: 16px;
    transition: border-color .15s;
  }}
  .card:hover {{ border-color: var(--accent); }}
  .card-head {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 8px; }}
  .journal {{
    font-size: 12px; font-weight: 600; color: var(--accent);
    background: rgba(110,168,254,0.12); padding: 3px 10px; border-radius: 999px;
  }}
  .date {{ font-size: 12px; color: var(--muted); white-space: nowrap; }}
  .title {{ font-size: 17px; margin: 4px 0 10px; line-height: 1.4; }}
  .title a {{ color: var(--text); text-decoration: none; }}
  .title a:hover {{ color: var(--accent); text-decoration: underline; }}
  .abstract {{ margin: 0; color: var(--muted); font-size: 14.5px; }}
  .no-abstract {{ font-style: italic; opacity: .7; }}
  .empty {{ text-align: center; color: var(--muted); padding: 60px 0; }}
  footer {{ text-align: center; color: var(--muted); font-size: 12px; padding: 30px 20px 50px; }}
</style>
</head>
<body>
  <header>
    <h1>📚 논문 피드</h1>
    <div class="meta">최근 14일 · battery 관련 · 마지막 업데이트 {now_str} KST · 총 {len(items)}편</div>
  </header>
  <div class="wrap">
    {cards_html}
  </div>
  <footer>매일 자동 업데이트 · GitHub Actions + Pages</footer>
</body>
</html>"""


if __name__ == "__main__":
    main()
