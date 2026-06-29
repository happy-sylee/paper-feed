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


def load_categories():
    """keywords.txt 를 파싱해서 축별 카테고리 구조 반환.
    반환: {
      "SYSTEM": {"LIB": [kw...], "LMB": [...], ...},
      "COMPONENT": {"Cathode": [...], ...}
    }
    그리고 축별 카테고리 등장 순서도 함께 반환."""
    cats = {"SYSTEM": {}, "COMPONENT": {}}
    order = {"SYSTEM": [], "COMPONENT": []}
    cur_axis = None
    cur_cat = None
    for line in KEYWORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            inner = line[1:-1]
            if ":" in inner:
                axis, cat = inner.split(":", 1)
                axis = axis.strip().upper()
                cat = cat.strip()
                if axis in cats:
                    cur_axis, cur_cat = axis, cat
                    if cat not in cats[axis]:
                        cats[axis][cat] = []
                        order[axis].append(cat)
                else:
                    cur_axis = cur_cat = None
            continue
        # 키워드 줄
        if cur_axis and cur_cat:
            cats[cur_axis][cur_cat].append(line.lower())
    return cats, order


def all_keywords(cats):
    """필터(수집)용: 모든 카테고리의 키워드를 하나의 집합으로"""
    kws = set()
    for axis in cats.values():
        for kwlist in axis.values():
            kws.update(kwlist)
    return kws


def tag_categories(text, cats):
    """text가 속하는 카테고리들을 축별로 반환.
    반환: {"systems": [...], "components": [...]}
    아무 카테고리에도 안 걸리면 ['others']"""
    low = text.lower()
    systems = []
    for cat, kwlist in cats["SYSTEM"].items():
        if any(kw in low for kw in kwlist):
            systems.append(cat)
    components = []
    for cat, kwlist in cats["COMPONENT"].items():
        if any(kw in low for kw in kwlist):
            components.append(cat)
    if not systems:
        systems = ["others"]
    if not components:
        components = ["others"]
    return {"systems": systems, "components": components}


def clean_html(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_authors(entry, max_authors=4):
    """RSS entry에서 저자 목록을 추출. 형식이 제각각이라 방어적으로 처리.
    - entry.authors (리스트) 또는 entry.author (문자열) 시도
    - 너무 많으면 max_authors까지 + 'et al.'
    - 없으면 빈 문자열"""
    names = []
    if entry.get("authors"):
        for a in entry["authors"]:
            name = a.get("name", "").strip() if isinstance(a, dict) else str(a).strip()
            if name:
                names.append(name)
    if not names and entry.get("author"):
        raw = str(entry["author"]).strip()
        if raw:
            parts = re.split(r"\s*(?:,|;|\band\b|&)\s*", raw)
            names = [p.strip() for p in parts if p.strip()]
    names = [clean_html(n) for n in names if n]
    if not names:
        return ""
    if len(names) > max_authors:
        return ", ".join(names[:max_authors]) + " et al."
    return ", ".join(names)


def get_entry_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return None


def matches_any(text, keyword_set):
    """text 안에 키워드 집합 중 하나라도 있으면 True (수집 필터용)"""
    low = text.lower()
    return any(kw in low for kw in keyword_set)


def main():
    feeds = load_feeds()
    cats, cat_order = load_categories()
    keyword_set = all_keywords(cats)
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
                authors = get_authors(entry)

                if date and date < cutoff:
                    continue
                haystack = title + " " + abstract
                if not matches_any(haystack, keyword_set):
                    continue

                # 카테고리 태깅 (시스템 축 + 구성요소 축)
                tags = tag_categories(haystack, cats)

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
                    "authors": authors,
                    "systems": tags["systems"],
                    "components": tags["components"],
                })
                n_kept += 1
            log.append(f"  [OK]  {group} / {name}: {n_total}개 중 {n_kept}개")
        except Exception as e:
            log.append(f"  [실패] {group} / {name}: {e}")

    now_kst_str = now.astimezone(KST).strftime("%Y-%m-%d %H:%M")
    html_out = render_html(items, groups_order, cat_order, now_kst_str)
    OUTPUT_FILE.write_text(html_out, encoding="utf-8")

    print(f"총 {len(items)}개 논문 수집 (최근 {DAYS_BACK}일)")
    print("\n".join(log))


def render_html(items, groups_order, cat_order, now_str):
    # 데이터를 JS로 안전하게 전달 (</script> 깨짐 방지)
    data_json = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    groups_json = json.dumps(groups_order, ensure_ascii=False)
    systems_json = json.dumps(cat_order["SYSTEM"] + ["others"], ensure_ascii=False)
    components_json = json.dumps(cat_order["COMPONENT"] + ["others"], ensure_ascii=False)

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
    --g-Nature:#f06a35; --g-Science:#f08080; --g-Wiley:#6ea8fe;
    --g-RSC:#5fc98a; --g-Elsevier:#e6c34a; --g-ACS:#8a93e0;
    /* 시스템 카테고리 색 (다크) */
    --sys-LIB:#5ad1c8; --sys-LMB:#b48ef0; --sys-SIB:#5fc98a;
    --sys-ZIB:#e88fb8; --sys-others:#d9a441;
  }
  :root[data-theme="light"] {
    --bg:#f6f7f9; --sidebar:#ffffff; --card:#ffffff; --border:#e2e5ea;
    --text:#1a1d24; --muted:#6b7280; --accent:#2563eb; --date-bar:#eef1f6;
    /* 그룹별 색 (라이트: 진한 글자색) */
    --g-Nature:#ea5c27; --g-Science:#c43d3d; --g-Wiley:#2563eb;
    --g-RSC:#1f9254; --g-Elsevier:#a37e12; --g-ACS:#4750b0;
    /* 시스템 카테고리 색 (라이트) */
    --sys-LIB:#0e9b90; --sys-LMB:#7c3aed; --sys-SIB:#1f9254;
    --sys-ZIB:#c43d7a; --sys-others:#b8740f;
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
  .folder-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

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
  .save-btn {
    background:none; border:none; cursor:pointer; font-size:16px;
    color:var(--muted); padding:0 2px; line-height:1; transition:color .12s, transform .12s;
  }
  .save-btn:hover { color:var(--accent); transform:scale(1.2); }
  .title { font-size:16px; margin:2px 0 6px; line-height:1.4; }
  .title a { color:var(--text); text-decoration:none; }
  .title a:hover { color:var(--accent); text-decoration:underline; }
  .authors { font-size:12.5px; color:var(--muted); margin:0 0 8px; }
  .abstract { margin:0; color:var(--muted); font-size:14px; }
  .no-abstract { font-style:italic; opacity:.6; }
  /* 카드 안 카테고리 태그 */
  .cat-tags { margin-top:9px; display:flex; flex-wrap:wrap; gap:5px; }
  .cat-tag {
    font-size:11px; font-weight:500;
    padding:1px 9px; border-radius:6px; border:1px solid transparent;
  }
  .cat-tag.system {
    color:var(--sys-color, var(--muted));
    background:color-mix(in srgb, var(--sys-color, var(--muted)) 12%, transparent);
    border-color:color-mix(in srgb, var(--sys-color, var(--muted)) 30%, transparent);
  }
  .cat-tag.component {
    color:var(--muted);
    background:color-mix(in srgb, var(--text) 7%, transparent);
    border-color:var(--border);
  }

  /* 상단 필터 바 (토글로 열고 닫음) */
  .filterbar {
    background:var(--sidebar); border-bottom:1px solid var(--border);
    padding:14px 24px; display:flex; flex-direction:column; gap:10px;
  }
  .filterbar[hidden] { display:none; }
  .filter-actions { display:flex; gap:8px; margin-top:2px; }
  .filter-clear {
    font-size:12px; color:var(--muted); background:none; border:none;
    cursor:pointer; text-decoration:underline; padding:0;
  }
  .filter-clear:hover { color:var(--accent); }
  .filter-row { display:flex; align-items:center; gap:10px; }
  .filter-label {
    font-size:11px; font-weight:600; color:var(--muted);
    min-width:52px; letter-spacing:.03em;
  }
  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  .chip {
    font-size:12px; padding:3px 11px; border-radius:999px;
    border:1px solid var(--border); background:var(--card); color:var(--muted);
    cursor:pointer; user-select:none; transition:all .12s;
  }
  .chip:hover { border-color:var(--accent); color:var(--text); }
  .chip.on {
    background:var(--accent); color:#fff; border-color:var(--accent);
  }
  .empty { text-align:center; color:var(--muted); padding:60px 0; }

  /* ---- 모바일: 사이드바를 가운데 드롭다운으로 ---- */
  .dropdown-toggle { display:none; }
  @media (max-width:680px) {
    .layout { flex-direction:column; }
    .sidebar {
      width:100%; height:auto; position:sticky; top:0; z-index:20;
      border-right:none; border-bottom:1px solid var(--border);
      padding:10px 12px; display:flex; flex-direction:column; align-items:center;
    }
    .sidebar h2 { display:none; }
    /* 펼침 버튼 */
    .dropdown-toggle {
      display:flex; justify-content:space-between; align-items:center; gap:8px;
      width:100%; max-width:320px;
      background:var(--card); border:1px solid var(--border); color:var(--text);
      border-radius:10px; padding:10px 14px; cursor:pointer; font-size:14px; font-weight:600;
    }
    .dropdown-toggle .arrow { transition:transform .2s; color:var(--muted); }
    .sidebar.open .dropdown-toggle .arrow { transform:rotate(180deg); }
    /* 탭 목록: 평소 숨김, open 시 표시 */
    #tabs {
      width:100%; max-width:320px; margin-top:6px;
      display:none; flex-direction:column; gap:2px;
    }
    .sidebar.open #tabs { display:flex; }
    .tab { justify-content:space-between; }
    .date-header { top:54px; }
  }
</style>
</head>
<body>
<div class="layout">
  <nav class="sidebar" id="sidebar">
    <button class="dropdown-toggle" id="dropdownToggle">
      <span id="currentGroup">전체</span>
      <span class="arrow">v</span>
    </button>
    <h2>출판사 그룹</h2>
    <div id="tabs"></div>
    <h2 style="margin-top:18px; display:flex; justify-content:space-between; align-items:center;">
      <span>내 폴더</span>
      <span id="addFolder" style="cursor:pointer; color:var(--accent); font-size:16px;">+</span>
    </h2>
    <div id="folderList"></div>
  </nav>
  <div class="main">
    <header>
      <div>
        <h1>논문 피드</h1>
        <div class="meta">최근 14일 · battery 관련 · 업데이트 __NOW__ KST</div>
      </div>
      <div style="display:flex; gap:8px; align-items:center;">
        <button class="theme-btn" id="filterToggle">필터</button>
        <button class="theme-btn" id="themeBtn">라이트</button>
      </div>
    </header>
    <div class="filterbar" id="filterbar" hidden>
      <div class="filter-row">
        <span class="filter-label">시스템</span>
        <div class="chips" id="systemChips"></div>
      </div>
      <div class="filter-row">
        <span class="filter-label">구성요소</span>
        <div class="chips" id="componentChips"></div>
      </div>
      <div class="filter-actions">
        <button class="filter-clear" id="filterClear">필터 초기화</button>
      </div>
    </div>
    <div class="content" id="content"></div>
  </div>
</div>

<script>
const ITEMS = __DATA__;
const GROUPS = __GROUPS__;
const SYSTEMS = __SYSTEMS__;
const COMPONENTS = __COMPONENTS__;
let activeGroup = "전체";
let activeSystems = new Set();      // 선택된 시스템 칩 (비어있으면 전체)
let activeComponents = new Set();   // 선택된 구성요소 칩

// ---- 폴더 (localStorage 기반) ----
// 구조: { "폴더명": [ {논문 통째 복사}, ... ], ... }
const STORE_KEY = "paperFeedFolders";
let folders = loadFolders();
let viewMode = "feed";   // "feed" = 메인 피드, "folder" = 특정 폴더 보기
let activeFolder = null;
let currentList = [];    // 현재 화면에 그려진 논문 목록 (저장 버튼 참조용)

function loadFolders(){
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch(e){ return {}; }
}
function saveFolders(){
  try { localStorage.setItem(STORE_KEY, JSON.stringify(folders)); }
  catch(e){ alert("저장 공간이 부족하거나 브라우저가 저장을 막고 있어요."); }
}
function paperKey(it){
  // 중복 판단용 키 (링크 우선, 없으면 제목)
  return it.link || it.title;
}
function isSaved(folderName, it){
  return (folders[folderName]||[]).some(p => paperKey(p) === paperKey(it));
}

// ---- 사이드바 탭 만들기 ----
function buildTabs(){
  const tabs = document.getElementById("tabs");
  // 시스템/구성요소 필터를 반영한 카운트 (그룹 필터는 제외하고 셈)
  function countFor(group){
    return ITEMS.filter(it => {
      if(group !== "전체" && it.group !== group) return false;
      if(activeSystems.size>0 && !(it.systems||[]).some(s=>activeSystems.has(s))) return false;
      if(activeComponents.size>0 && !(it.components||[]).some(c=>activeComponents.has(c))) return false;
      return true;
    }).length;
  }
  const list = ["전체", ...GROUPS];
  tabs.innerHTML = "";
  list.forEach(g => {
    const div = document.createElement("div");
    div.className = "tab" + (g===activeGroup ? " active":"");
    div.innerHTML = `<span>${g}</span><span class="count">${countFor(g)}</span>`;
    div.onclick = () => {
      activeGroup = g;
      backToFeed();         // 폴더 보기 - 메인 피드로
      render();
      buildTabs();
      buildFolders();
      // 모바일 드롭다운: 선택 시 라벨 갱신하고 접기
      document.getElementById("currentGroup").textContent = g;
      document.getElementById("sidebar").classList.remove("open");
    };
    tabs.appendChild(div);
  });
}

// ---- 폴더 목록 사이드바 ----
function buildFolders(){
  const box = document.getElementById("folderList");
  box.innerHTML = "";
  const names = Object.keys(folders);
  if(names.length === 0){
    const hint = document.createElement("div");
    hint.style.cssText = "font-size:12px; color:var(--muted); padding:6px 10px;";
    hint.textContent = "+ 로 폴더를 만들어보세요";
    box.appendChild(hint);
    return;
  }
  names.forEach(name => {
    const div = document.createElement("div");
    const isActive = (viewMode==="folder" && activeFolder===name);
    div.className = "tab" + (isActive ? " active":"");
    div.innerHTML = `<span class="folder-name">${escAttr(name)}</span><span class="count">${folders[name].length}</span>`;
    // 폴더 선택
    div.querySelector(".folder-name").onclick = () => {
      viewMode = "folder"; activeFolder = name; activeGroup = "전체";
      render(); buildTabs(); buildFolders();
      document.getElementById("sidebar").classList.remove("open");
    };
    // 우클릭/길게 누르면 관리 메뉴 대신, 옆에 작은 메뉴 버튼
    const menu = document.createElement("span");
    menu.textContent = "...";
    menu.style.cssText = "cursor:pointer; color:var(--muted); padding:0 4px; margin-left:4px;";
    menu.onclick = (e) => { e.stopPropagation(); folderMenu(name); };
    div.appendChild(menu);
    box.appendChild(div);
  });
}

function folderMenu(name){
  const action = prompt(
    `폴더 "${name}"\n\n무엇을 할까요?\n  1 = 이름 수정\n  2 = 삭제\n\n번호를 입력하세요 (취소는 빈칸):`
  );
  if(action === "1"){
    const newName = prompt("새 폴더 이름:", name);
    if(newName && newName.trim() && newName !== name){
      if(folders[newName]){ alert("같은 이름의 폴더가 이미 있어요."); return; }
      folders[newName] = folders[name];
      delete folders[name];
      if(activeFolder===name) activeFolder=newName;
      saveFolders(); buildFolders(); render();
    }
  } else if(action === "2"){
    if(confirm(`폴더 "${name}"을(를) 삭제할까요? (담긴 논문 정보도 사라져요)`)){
      delete folders[name];
      if(activeFolder===name){ viewMode="feed"; activeFolder=null; }
      saveFolders(); buildFolders(); buildTabs(); render();
    }
  }
}

// + 폴더 추가
document.getElementById("addFolder").onclick = () => {
  const name = prompt("새 폴더 이름:");
  if(name && name.trim()){
    if(folders[name]){ alert("같은 이름의 폴더가 이미 있어요."); return; }
    folders[name] = [];
    saveFolders(); buildFolders();
  }
};

// 메인 피드로 돌아가기 (출판사 그룹 클릭 시 자동)
function backToFeed(){
  viewMode = "feed"; activeFolder = null;
}
function buildChips(){
  const sysBox = document.getElementById("systemChips");
  const compBox = document.getElementById("componentChips");
  sysBox.innerHTML = "";
  compBox.innerHTML = "";
  SYSTEMS.forEach(s => {
    const c = document.createElement("span");
    c.className = "chip" + (activeSystems.has(s) ? " on":"");
    c.textContent = s;
    c.onclick = () => {
      activeSystems.has(s) ? activeSystems.delete(s) : activeSystems.add(s);
      render(); buildChips(); buildTabs();
    };
    sysBox.appendChild(c);
  });
  COMPONENTS.forEach(s => {
    const c = document.createElement("span");
    c.className = "chip" + (activeComponents.has(s) ? " on":"");
    c.textContent = s;
    c.onclick = () => {
      activeComponents.has(s) ? activeComponents.delete(s) : activeComponents.add(s);
      render(); buildChips(); buildTabs();
    };
    compBox.appendChild(c);
  });
}

// ---- 필터 적용: 같은 축 OR, 다른 축 AND ----
function passFilter(it){
  // 출판사 그룹
  if(activeGroup !== "전체" && it.group !== activeGroup) return false;
  // 시스템 축 (선택된 게 있으면, 논문 시스템 중 하나라도 선택셋에 있어야 함 = OR)
  if(activeSystems.size > 0){
    if(!(it.systems||[]).some(s => activeSystems.has(s))) return false;
  }
  // 구성요소 축 (OR)
  if(activeComponents.size > 0){
    if(!(it.components||[]).some(c => activeComponents.has(c))) return false;
  }
  // 축들 사이는 AND (위 조건 전부 통과해야 도달)
  return true;
}

// ---- 모바일 드롭다운 펼침/접힘 ----
document.getElementById("dropdownToggle").onclick = () => {
  document.getElementById("sidebar").classList.toggle("open");
};

// ---- 필터 바 열고 닫기 (웹+모바일 공통) ----
document.getElementById("filterToggle").onclick = () => {
  const fb = document.getElementById("filterbar");
  fb.hidden = !fb.hidden;
};

// ---- 필터 초기화 ----
document.getElementById("filterClear").onclick = () => {
  activeSystems.clear();
  activeComponents.clear();
  render(); buildChips(); buildTabs();
};

// ---- 본문 렌더링 (날짜로 그룹핑) ----
function render(){
  const content = document.getElementById("content");

  // 필터 버튼에 활성 표시
  const nFilters = activeSystems.size + activeComponents.size;
  document.getElementById("filterToggle").textContent =
    nFilters > 0 ? `필터 (${nFilters})` : "필터";

  // 데이터 소스: 폴더 보기면 폴더 내용, 아니면 메인 피드
  let list;
  if(viewMode === "folder" && activeFolder !== null){
    list = (folders[activeFolder] || []).filter(it => {
      if(activeSystems.size>0 && !(it.systems||[]).some(s=>activeSystems.has(s))) return false;
      if(activeComponents.size>0 && !(it.components||[]).some(c=>activeComponents.has(c))) return false;
      return true;
    });
  } else {
    list = ITEMS.filter(passFilter);
  }

  // 정렬: 최신 날짜 우선, 같은 날짜면 저널 이름 순
  list.sort((a,b) => (b.ts - a.ts) || a.journal.localeCompare(b.journal));

  if(list.length===0){
    const msg = (viewMode==="folder")
      ? '이 폴더가 비어있어요. 메인 피드에서 * 를 눌러 논문을 담아보세요.'
      : '조건에 맞는 논문이 없어요.';
    content.innerHTML = `<p class="empty">${msg}</p>`;
    return;
  }

  let htmlStr = "";
  let lastDate = null;
  currentList = list;   // 저장 버튼 클릭 시 참조용
  for(let idx=0; idx<list.length; idx++){
    const it = list[idx];
    const d = it.date || "날짜 미상";
    if(d !== lastDate){
      htmlStr += `<div class="date-header">${d}</div>`;
      lastDate = d;
    }
    const abs = it.abstract
      ? `<p class="abstract">${esc(it.abstract)}</p>`
      : `<p class="abstract no-abstract">초록 없음 — 제목을 눌러 원문에서 확인</p>`;
    const sysTags = (it.systems||[]).map(s =>
      `<span class="cat-tag system" style="--sys-color:var(--sys-${s.replace(/[^a-zA-Z]/g,'')}, var(--muted))">${esc(s)}</span>`
    ).join("");
    const compTags = (it.components||[]).map(c =>
      `<span class="cat-tag component">${esc(c)}</span>`
    ).join("");
    const tagHtml = (sysTags || compTags)
      ? `<div class="cat-tags">${sysTags}${compTags}</div>` : "";
    // 그룹 색: CSS 변수 --g-<group> 을 카드의 --jcolor 로 연결
    const safeGroup = it.group.replace(/[^a-zA-Z]/g, "");
    // 저장 버튼: 폴더 보기면 빼기(x), 피드면 담기(*)
    const btnIcon = (viewMode==="folder") ? "x" : "+";
    const btnTitle = (viewMode==="folder") ? "이 폴더에서 빼기" : "폴더에 담기";
    htmlStr += `
      <article class="card" style="--jcolor:var(--g-${safeGroup}, var(--accent))">
        <div class="card-head">
          <span class="journal">${esc(it.journal)}</span>
          <div style="display:flex; align-items:center; gap:8px;">
            <span class="grp">${esc(it.group)}</span>
            <button class="save-btn" data-idx="${idx}" title="${btnTitle}">${btnIcon}</button>
          </div>
        </div>
        <h2 class="title"><a href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a></h2>
        ${it.authors ? `<p class="authors">${esc(it.authors)}</p>` : ""}
        ${abs}
        ${tagHtml}
      </article>`;
  }
  content.innerHTML = htmlStr;

  // 저장 버튼 이벤트 (위임)
  content.querySelectorAll(".save-btn").forEach(btn => {
    btn.onclick = () => {
      const it = currentList[parseInt(btn.dataset.idx, 10)];
      if(!it) return;
      if(viewMode === "folder"){
        // 폴더에서 빼기
        folders[activeFolder] = (folders[activeFolder]||[]).filter(p => paperKey(p)!==paperKey(it));
        saveFolders(); render(); buildFolders();
      } else {
        // 폴더에 담기 (폴더 선택)
        savePaperToFolder(it);
      }
    };
  });
}

// 논문을 폴더에 담기
function savePaperToFolder(it){
  const names = Object.keys(folders);
  if(names.length === 0){
    if(confirm("폴더가 없어요. 새로 만들까요?")){
      const name = prompt("새 폴더 이름:");
      if(name && name.trim()){
        folders[name] = [];
      } else return;
    } else return;
  }
  const list = Object.keys(folders);
  let target;
  if(list.length === 1){
    target = list[0];
  } else {
    const choice = prompt(
      "어느 폴더에 담을까요?\\n\\n" +
      list.map((n,i)=>`  ${i+1} = ${n}`).join("\\n") +
      "\\n\\n번호 입력:"
    );
    const i = parseInt(choice,10)-1;
    if(isNaN(i) || i<0 || i>=list.length) return;
    target = list[i];
  }
  if(isSaved(target, it)){
    alert(`"${target}" 폴더에 이미 있어요.`);
    return;
  }
  folders[target] = folders[target] || [];
  folders[target].push(it);   // 논문 통째로 복사 저장
  saveFolders(); buildFolders();
  alert(`"${target}" 폴더에 담았어요. `);
}

function esc(s){
  return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;")
                .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function escAttr(s){ return esc(s); }

// ---- 라이트/다크 토글 ----
const themeBtn = document.getElementById("themeBtn");
themeBtn.onclick = () => {
  const root = document.documentElement;
  const next = root.getAttribute("data-theme")==="dark" ? "light":"dark";
  root.setAttribute("data-theme", next);
  themeBtn.textContent = next==="dark" ? "라이트" : "다크";
};

buildTabs();
buildChips();
buildFolders();
render();
</script>
</body>
</html>""".replace("__DATA__", data_json).replace("__GROUPS__", groups_json).replace("__SYSTEMS__", systems_json).replace("__COMPONENTS__", components_json).replace("__NOW__", now_str)


if __name__ == "__main__":
    main()
