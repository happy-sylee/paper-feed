#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
논문 피드 빌더 (v2: 그룹 사이드바 + 날짜 그룹핑 + 라이트모드)
- feeds.txt (그룹|저널|URL) 의 RSS 를 모두 읽어서
- 최근 N일 + 키워드 매칭 논문만 골라
- 데이터를 JSON 으로 index.html 에 심는다.
- 사이드바 필터/정렬은 브라우저(JS)가 처리 (정적 호스팅에서도 동작)
"""

import html
import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser

# ------------------- 설정 -------------------
DAYS_BACK = 14
HERE = Path(__file__).parent
FEEDS_FILE = HERE / "feeds.txt"
KEYWORDS_FILE = HERE / "keywords.txt"
OUTPUT_FILE = HERE / "index.html"
KST = timezone(timedelta(hours=9))
# --------------------------------------------


def load_feeds():
    """feeds.txt -> [(group, name, url), ...]"""
    feeds = []
    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3:
            continue
        group, name, url = parts
        feeds.append((group, name, url))
    return feeds


def load_keywords():
    kws = []
    for line in KEYWORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kws.append(line.lower())
    return kws


def clean_html(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_entry_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return None


def matches_keywords(text, keywords):
    low = text.lower()
    return any(kw in low for kw in keywords)


def found_keywords(text, keywords, limit=6):
    """text 안에서 실제로 발견된 키워드 목록을 반환 (표시용).
    - 긴 키워드부터 검사해 부분 중복을 줄임
    - 원래 keywords.txt에 적힌 형태로 표시
    - 최대 limit개까지"""
    low = text.lower()
    hits = []
    for kw in sorted(keywords, key=len, reverse=True):
        if kw in low and kw not in hits:
            hits.append(kw)
    # keywords.txt 순서대로 다시 정렬해서 일관성 유지
    ordered = [kw for kw in keywords if kw in hits]
    return ordered[:limit]


def main():
    feeds = load_feeds()
    keywords = load_keywords()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DAYS_BACK)

    items = []
    groups_order = []  # 그룹 등장 순서 보존
    log = []

    for group, name, url in feeds:
        if group not in groups_order:
            groups_order.append(group)
        try:
            parsed = feedparser.parse(url)
            n_total = len(parsed.entries)
            n_kept = 0
            for entry in parsed.entries:
                title = clean_html(entry.get("title", ""))
                abstract = clean_html(entry.get("summary", ""))
                if not abstract and entry.get("content"):
                    try:
                        abstract = clean_html(entry["content"][0].get("value", ""))
                    except Exception:
                        abstract = ""
                link = entry.get("link", "")
                date = get_entry_date(entry)

                if date and date < cutoff:
                    continue
                haystack = title + " " + abstract
                if not matches_keywords(haystack, keywords):
                    continue

                # 표시용 키워드 추출
                kws_found = found_keywords(haystack, keywords)

                # 날짜를 KST 기준 yyyy-mm-dd 문자열로 (없으면 빈 문자열)
                date_kst = date.astimezone(KST).strftime("%Y-%m-%d") if date else ""
                # 정렬용 타임스탬프 (없으면 0)
                ts = date.timestamp() if date else 0

                items.append({
                    "group": group,
                    "journal": name,
                    "title": title or "(제목 없음)",
                    "abstract": abstract,
                    "link": link,
                    "date": date_kst,
                    "ts": ts,
                    "keywords": kws_found,
                })
                n_kept += 1
            log.append(f"  [OK]  {group} / {name}: {n_total}개 중 {n_kept}개")
        except Exception as e:
            log.append(f"  [실패] {group} / {name}: {e}")

    now_kst_str = now.astimezone(KST).strftime("%Y-%m-%d %H:%M")
    html_out = render_html(items, groups_order, now_kst_str)
    OUTPUT_FILE.write_text(html_out, encoding="utf-8")

    print(f"총 {len(items)}개 논문 수집 (최근 {DAYS_BACK}일)")
    print("\n".join(log))


def render_html(items, groups_order, now_str):
    # 데이터를 JS로 안전하게 전달 (</script> 깨짐 방지)
    data_json = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    groups_json = json.dumps(groups_order, ensure_ascii=False)

    return """<!DOCTYPE html>
<html lang="ko" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>논문 피드</title>
<style>
  :root[data-theme="dark"] {
    --bg:#0f1115; --sidebar:#14171d; --card:#1a1d24; --border:#2a2e38;
    --text:#e8eaed; --muted:#9aa0a6; --accent:#6ea8fe; --date-bar:#222732;
    /* 그룹별 색 (다크: 밝은 글자색) */
    --g-Nature:#f0a868; --g-Science:#f08080; --g-Wiley:#6ea8fe;
    --g-RSC:#5fc98a; --g-Elsevier:#e6c34a; --g-ACS:#8a93e0;
  }
  :root[data-theme="light"] {
    --bg:#f6f7f9; --sidebar:#ffffff; --card:#ffffff; --border:#e2e5ea;
    --text:#1a1d24; --muted:#6b7280; --accent:#2563eb; --date-bar:#eef1f6;
    /* 그룹별 색 (라이트: 진한 글자색) */
    --g-Nature:#c2691a; --g-Science:#c43d3d; --g-Wiley:#2563eb;
    --g-RSC:#1f9254; --g-Elsevier:#a37e12; --g-ACS:#4750b0;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--bg); color:var(--text);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Noto Sans KR",sans-serif;
    line-height:1.6;
  }
  .layout { display:flex; min-height:100vh; }

  /* ---- 사이드바 ---- */
  .sidebar {
    width:180px; flex-shrink:0; background:var(--sidebar);
    border-right:1px solid var(--border); padding:16px 10px;
    position:sticky; top:0; height:100vh; overflow-y:auto;
  }
  .sidebar h2 { font-size:13px; color:var(--muted); margin:6px 8px 10px; font-weight:600; letter-spacing:.04em; }
  .tab {
    display:flex; justify-content:space-between; align-items:center;
    padding:8px 10px; margin-bottom:2px; border-radius:8px;
    cursor:pointer; font-size:14px; color:var(--text); user-select:none;
  }
  .tab:hover { background:var(--border); }
  .tab.active { background:var(--accent); color:#fff; }
  .tab .count { font-size:11px; opacity:.7; }
  .tab.active .count { opacity:.9; }

  /* ---- 메인 ---- */
  .main { flex:1; min-width:0; }
  header {
    position:sticky; top:0; z-index:10; background:var(--bg);
    border-bottom:1px solid var(--border); padding:14px 24px;
    display:flex; justify-content:space-between; align-items:center; gap:12px;
  }
  header h1 { margin:0; font-size:18px; }
  header .meta { color:var(--muted); font-size:12px; margin-top:2px; }
  .theme-btn {
    background:var(--card); border:1px solid var(--border); color:var(--text);
    border-radius:8px; padding:7px 12px; cursor:pointer; font-size:13px; white-space:nowrap;
  }
  .theme-btn:hover { border-color:var(--accent); }

  .content { max-width:860px; margin:0 auto; padding:20px 24px 60px; }

  /* ---- 날짜 헤더 ---- */
  .date-header {
    position:sticky; top:56px; z-index:5;
    background:var(--date-bar); border:1px solid var(--border);
    border-radius:8px; padding:6px 14px; margin:22px 0 12px;
    font-size:13px; font-weight:600; color:var(--accent);
  }
  .date-header:first-child { margin-top:0; }

  /* ---- 카드 ---- */
  .card {
    background:var(--card); border:1px solid var(--border);
    border-radius:12px; padding:15px 18px; margin-bottom:12px;
  }
  .card:hover { border-color:var(--accent); }
  .card-head { display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:6px; }
  .journal { font-size:11.5px; font-weight:600;
    color:var(--jcolor, var(--accent));
    background:color-mix(in srgb, var(--jcolor, var(--accent)) 14%, transparent);
    padding:2px 9px; border-radius:999px; }
  .grp { font-size:11px; color:var(--muted); }
  .title { font-size:16px; margin:2px 0 8px; line-height:1.4; }
  .title a { color:var(--text); text-decoration:none; }
  .title a:hover { color:var(--accent); text-decoration:underline; }
  .abstract { margin:0; color:var(--muted); font-size:14px; }
  .no-abstract { font-style:italic; opacity:.6; }
  /* 키워드 칩 */
  .keywords { margin-top:9px; display:flex; flex-wrap:wrap; gap:5px; }
  .kw {
    font-size:11px; color:var(--muted);
    background:color-mix(in srgb, var(--text) 8%, transparent);
    border:1px solid var(--border);
    padding:1px 8px; border-radius:6px;
  }
  .empty { text-align:center; color:var(--muted); padding:60px 0; }

  /* ---- 모바일 ---- */
  @media (max-width:680px) {
    .layout { flex-direction:column; }
    .sidebar { width:100%; height:auto; position:static; border-right:none;
      border-bottom:1px solid var(--border); display:flex; flex-wrap:wrap; gap:4px; }
    .sidebar h2 { width:100%; }
    .tab { flex:0 0 auto; margin-bottom:0; }
    .date-header { top:0; }
  }
</style>
</head>
<body>
<div class="layout">
  <nav class="sidebar">
    <h2>출판사 그룹</h2>
    <div id="tabs"></div>
  </nav>
  <div class="main">
    <header>
      <div>
        <h1>📚 논문 피드</h1>
        <div class="meta">최근 14일 · battery 관련 · 업데이트 __NOW__ KST</div>
      </div>
      <button class="theme-btn" id="themeBtn">☀️ 라이트</button>
    </header>
    <div class="content" id="content"></div>
  </div>
</div>

<script>
const ITEMS = __DATA__;
const GROUPS = __GROUPS__;
let activeGroup = "전체";

// ---- 사이드바 탭 만들기 ----
function buildTabs(){
  const tabs = document.getElementById("tabs");
  const counts = {"전체": ITEMS.length};
  GROUPS.forEach(g => counts[g] = ITEMS.filter(it => it.group===g).length);
  const list = ["전체", ...GROUPS];
  tabs.innerHTML = "";
  list.forEach(g => {
    const div = document.createElement("div");
    div.className = "tab" + (g===activeGroup ? " active":"");
    div.innerHTML = `<span>${g}</span><span class="count">${counts[g]||0}</span>`;
    div.onclick = () => { activeGroup = g; render(); buildTabs(); };
    tabs.appendChild(div);
  });
}

// ---- 본문 렌더링 (날짜로 그룹핑) ----
function render(){
  const content = document.getElementById("content");
  let list = (activeGroup==="전체") ? ITEMS.slice() : ITEMS.filter(it => it.group===activeGroup);

  // 정렬: 최신 날짜 우선, 같은 날짜면 저널 이름 순
  list.sort((a,b) => (b.ts - a.ts) || a.journal.localeCompare(b.journal));

  if(list.length===0){
    content.innerHTML = '<p class="empty">조건에 맞는 논문이 없어요.</p>';
    return;
  }

  let htmlStr = "";
  let lastDate = null;
  for(const it of list){
    const d = it.date || "날짜 미상";
    if(d !== lastDate){
      htmlStr += `<div class="date-header">${d}</div>`;
      lastDate = d;
    }
    const abs = it.abstract
      ? `<p class="abstract">${esc(it.abstract)}</p>`
      : `<p class="abstract no-abstract">초록 없음 — 제목을 눌러 원문에서 확인</p>`;
    const kwHtml = (it.keywords && it.keywords.length)
      ? `<div class="keywords">${it.keywords.map(k => `<span class="kw">${esc(k)}</span>`).join("")}</div>`
      : "";
    // 그룹 색: CSS 변수 --g-<group> 을 카드의 --jcolor 로 연결
    const safeGroup = it.group.replace(/[^a-zA-Z]/g, "");
    htmlStr += `
      <article class="card" style="--jcolor:var(--g-${safeGroup}, var(--accent))">
        <div class="card-head">
          <span class="journal">${esc(it.journal)}</span>
          <span class="grp">${esc(it.group)}</span>
        </div>
        <h2 class="title"><a href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a></h2>
        ${abs}
        ${kwHtml}
      </article>`;
  }
  content.innerHTML = htmlStr;
}

function esc(s){
  return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;")
                .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ---- 라이트/다크 토글 ----
const themeBtn = document.getElementById("themeBtn");
themeBtn.onclick = () => {
  const root = document.documentElement;
  const next = root.getAttribute("data-theme")==="dark" ? "light":"dark";
  root.setAttribute("data-theme", next);
  themeBtn.textContent = next==="dark" ? "☀️ 라이트" : "🌙 다크";
};

buildTabs();
render();
</script>
</body>
</html>""".replace("__DATA__", data_json).replace("__GROUPS__", groups_json).replace("__NOW__", now_str)


if __name__ == "__main__":
    main()
