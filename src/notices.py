"""정부 수출통제 공고 수집 → data/notices.csv

출처
- 🇺🇸 미국 BIS(상무부 산업안보국): Federal Register 공식 API (무료)
- 🇯🇵 일본 METI(경제산업성): 안전보장무역관리 '개정 정보' 페이지
- 🇨🇳 중국 상무부: 출구관제 정보망(exportcontrol.mofcom.gov.cn) 국내 동향
  (상무부 본 사이트 aqygzj.mofcom.gov.cn은 접속을 끊어서 쓰지 않음.
   공고 원문은 Google News 중문 수집이 보완)

각 공고의 제목·요약에 당사 품목 키워드가 있으면 related_items에 품목명을 적습니다.
"""

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from src.news import make_matcher

KST = timezone(timedelta(hours=9))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36"}
NOTICE_COLUMNS = ["date", "country", "agency", "title", "summary", "link", "related_items", "collected_at"]


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", text).strip()


# ── 미국 BIS ─────────────────────────────────────────

def fetch_bis(per_page: int = 50) -> list[dict]:
    resp = requests.get(
        "https://www.federalregister.gov/api/v1/documents.json",
        params={"conditions[agencies][]": "industry-and-security-bureau", "order": "newest",
                "per_page": per_page,
                "fields[]": ["title", "publication_date", "html_url", "abstract", "type"]},
        headers=HEADERS, timeout=30,
    )
    resp.raise_for_status()
    return [
        {"date": d["publication_date"], "country": "미국", "agency": f"BIS ({d.get('type', '')})",
         "title": d["title"], "summary": d.get("abstract") or "", "link": d["html_url"]}
        for d in resp.json().get("results", [])
    ]


# ── 일본 METI ────────────────────────────────────────

def _wareki_to_date(text: str) -> str:
    """'令和8年6月16日' → '2026-06-16' (전각 숫자·元年도 처리)"""
    t = unicodedata.normalize("NFKC", text).replace("元年", "1年")
    m = re.search(r"令和\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", t)
    if not m:
        return ""
    y, mo, d = (int(x) for x in m.groups())
    return f"{2018 + y:04d}-{mo:02d}-{d:02d}"


def fetch_meti() -> list[dict]:
    url = "https://www.meti.go.jp/policy/anpo/law09-2.html"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", resp.text, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 2:
            continue
        date = _wareki_to_date(_clean(tds[0]))
        if not date:
            continue
        href = re.search(r'href="([^"]+)"', tds[1])
        link = href.group(1) if href else url
        if link.startswith("#"):  # 같은 페이지 안의 항목 ('#id41', 오타 '##id41' 모두)
            link = url + "#" + link.lstrip("#")
        elif link.startswith("/"):
            link = "https://www.meti.go.jp" + link
        rows.append({"date": date, "country": "일본", "agency": "METI 안전보장무역관리",
                     "title": _clean(tds[1]), "summary": _clean(tds[2]) if len(tds) > 2 else "",
                     "link": link})
    return rows


# ── 중국 상무부 ──────────────────────────────────────

def fetch_mofcom(max_articles: int = 25) -> list[dict]:
    base = "http://exportcontrol.mofcom.gov.cn"
    resp = requests.get(base + "/", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    links = []
    # gndt = 국내 동향(중국 조치), gjdt = 국제 동향(각국 조치)
    for href, title in re.findall(r'href="(/article/(?:gndt|gjdt)/\d{6}/\d+\.html)"[^>]*>([^<]{4,120})</a>', resp.text):
        if href not in (h for h, _ in links):
            links.append((href, _clean(title)))
    rows = []
    for href, title in links[:max_articles]:
        try:
            page = requests.get(base + href, headers=HEADERS, timeout=30)
            page.encoding = "utf-8"
            m = re.search(r"publishTime\s*=\s*'(\d{4}-\d{2}-\d{2})", page.text)
            month = re.search(r"/(\d{4})(\d{2})/", href)
            date = m.group(1) if m else f"{month.group(1)}-{month.group(2)}-01"
            body = _clean(re.sub(r"<script.*?</script>", " ", page.text, flags=re.S))
            i = body.find(title[:10])
            summary = body[i + len(title): i + len(title) + 300] if i >= 0 else ""
        except Exception:
            month = re.search(r"/(\d{4})(\d{2})/", href)
            date, summary = f"{month.group(1)}-{month.group(2)}-01", ""
        agency = "상무부 정보망 (국내 동향)" if "/gndt/" in href else "상무부 정보망 (국제 동향)"
        rows.append({"date": date, "country": "중국", "agency": agency,
                     "title": title, "summary": summary.strip(), "link": base + href})
    return rows


# ── 전체 ──────────────────────────────────────────────

def collect_notices(items: list[dict], log=print) -> pd.DataFrame:
    collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    rows = []
    for name, fn in [("미국 BIS", fetch_bis), ("일본 METI", fetch_meti), ("중국 상무부", fetch_mofcom)]:
        try:
            got = fn()
            rows.extend(got)
            log(f"  {name}: {len(got)}건")
        except Exception as exc:
            log(f"  {name}: 실패 {exc}")
    df = pd.DataFrame(rows, columns=NOTICE_COLUMNS[:-2])
    matchers = {it["item"]: make_matcher(it) for it in items}
    df["related_items"] = [
        "; ".join(name for name, m in matchers.items() if m(f"{t} {s}"))
        for t, s in zip(df["title"], df["summary"])
    ]
    df["collected_at"] = collected_at
    return df[NOTICE_COLUMNS]


def merge_and_save(new: pd.DataFrame, items: list[dict], path: str | Path = "data/notices.csv") -> tuple[int, int]:
    """(전체 건수, 당사 품목 관련 건수)"""
    path = Path(path)
    old = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig") \
        if path.exists() else pd.DataFrame(columns=NOTICE_COLUMNS)
    df = pd.concat([old, new], ignore_index=True)
    df = df.drop_duplicates(subset=["link", "title"], keep="last")  # 최신 수집 내용으로
    # 품목·키워드가 바뀌어도 바로 반영되도록 관련 품목은 매번 다시 계산
    matchers = {it["item"]: make_matcher(it) for it in items}
    df["related_items"] = [
        "; ".join(name for name, m in matchers.items() if m(f"{t} {s}"))
        for t, s in zip(df["title"], df["summary"])
    ]
    df = df.sort_values("date", ascending=False)
    df[NOTICE_COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
    return len(df), int((df["related_items"] != "").sum())
