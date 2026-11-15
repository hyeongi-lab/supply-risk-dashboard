"""Google News RSS로 품목별 '위험' 뉴스를 모아 data/news.csv에 저장합니다.

검색 방식 (품목 키워드 + 위험 키워드 조합)
- 품목 키워드마다 한 번씩:  품목 키워드 (위험 키워드1 OR 위험 키워드2 ...)
  예) "tungsten powder" ("export control" OR tariff OR shortage ...)
- 품목 키워드는 구글 시트에서, 위험 키워드는 아래 RISK_KEYWORDS에서 가져옵니다.
- 언어별 Google News 판: 영문=미국판, 중문=중국판, 한글=한국판

수집 후 제목으로 거르기
- 제목에 품목 키워드가 하나도 없으면 제외 (예: 'APT'가 아파트 기사에 걸린 경우)
- 시장 전망 보고서·광고성 기사(Market Forecast, Market Size, CAGR 등) 제외
- 같은 품목 안에서 같은 링크 또는 같은 제목의 기사는 한 번만 남깁니다.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import feedparser
import pandas as pd
import requests

KST = timezone(timedelta(hours=9))

# 언어별 Google News 판(edition) 설정: (hl, gl, ceid)
EDITIONS = {
    "en": ("en-US", "US", "US:en"),
    "zh": ("zh-CN", "CN", "CN:zh-Hans"),
    "ko": ("ko", "KR", "KR:ko"),
}

# 위험 키워드: 품목 키워드와 '함께' 나오는 기사만 검색합니다.
RISK_KEYWORDS = {
    "en": ["export control", "export ban", "export restriction", "tariff", "sanction",
           "embargo", "shortage", "supply disruption", "supply chain",
           "lead time", "discontinued", "price increase"],  # 마지막 3개: 부품 납기·단종·가격 인상
    "zh": ["出口管制", "出口禁令", "出口限制", "关税", "制裁", "短缺", "断供", "供应中断", "涨价"],
    "ko": ["수출통제", "수출 규제", "수출금지", "관세", "제재", "수급", "공급 차질", "품귀", "납기", "단종"],
}

# 뜻이 여러 개라 단독으로 쓰면 엉뚱한 기사가 걸리는 키워드 → 대신 쓸 키워드
AMBIGUOUS_KEYWORDS = {
    "apt": ["tungsten APT", "ammonium paratungstate"],
}

# 제목에 이런 말이 있으면 제외 (시장 전망 보고서, 광고성 기사)
EXCLUDE_TITLE = re.compile(
    r"market (forecast|size|share|report|research|outlook|analysis|growth|trends?|insights?)"
    r"|\bmarket\b.{0,25}\b(explosive|booming|growth|forecast|outlook|size|share)\b"  # 'Market Explosive Growth' 등
    # 일반 PC·전자제품 커넥터 (군용 Micro-D와 무관)
    r"|\b(USB|HDMI|Type-?C|Lightning|RJ-?45|SATA|PCIe|DisplayPort|Thunderbolt)\b"
    r"|\b(laptop|smartphone|gaming|motherboard|graphics card|GPU)\b"
    r"|\bCAGR\b|forecast (to|till|through) 20\d\d|industry (analysis|report)|research report"
    r"|sponsored|press release"
    r"|市场规模|市场预测|市场分析|市场前景|行业报告|研究报告|行业分析"
    r"|시장 ?전망|시장 ?규모|시장 ?보고서|시장 ?분석|\[광고\]|\[AD\]",
    re.IGNORECASE,
)

NEWS_COLUMNS = [
    "published",     # 기사 시각 (한국 시간, YYYY-MM-DD HH:MM)
    "item",          # 품목명
    "lang",          # en / zh / ko
    "keyword",       # 제목에서 찾은 품목 키워드
    "title",         # 기사 제목
    "source",        # 언론사
    "link",          # 기사 링크
    "collected_at",  # 수집 시각 (한국 시간)
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; supply-risk-monitor/1.0)"}


# ── 키워드 준비 ───────────────────────────────────────

_ALL_RISK = {r.lower().replace(" ", "") for rs in RISK_KEYWORDS.values() for r in rs}


def item_terms(item: dict) -> dict[str, list[str]]:
    """품목의 언어별 검색 키워드. (중복 제거)
    - 뜻이 여러 개인 키워드(APT 등)는 바꿔 씁니다.
    - 위험 키워드와 똑같은 키워드(예: '出口管制')는 품목을 가리키지 않으므로 뺍니다.
    """
    out = {}
    for lang, kws in item["keywords"].items():
        terms: list[str] = []
        for kw in kws:
            for t in AMBIGUOUS_KEYWORDS.get(kw.strip().lower(), [kw.strip()]):
                if not t or t.lower().replace(" ", "") in _ALL_RISK:
                    continue
                if t.lower() not in (x.lower() for x in terms):
                    terms.append(t)
        out[lang] = terms
    return out


def _q(term: str) -> str:
    return f'"{term}"' if " " in term else term


def build_query(term: str, risks: list[str]) -> str:
    """품목 키워드 1개 + 위험 키워드 묶음.
    (품목 키워드 여러 개를 한 검색어에 OR로 넣으면 구글 결과가 크게 줄어서 1개씩 검색)"""
    return f"{_q(term)} ({' OR '.join(map(_q, risks))})"


def is_standalone(item: dict, term: str) -> bool:
    """부품은 제조사명·품번처럼 한 단어 키워드(예: Cristek, Qnnect, MIL-DTL-83513)를 위험 키워드 없이 단독 검색.
    단일 공급사 부품은 공급사 인수합병·이전 같은 '공급사 변화'가 위험 신호라서 (2026-11-15 사용자 결정)"""
    return item.get("category", "").strip() == "부품" and " " not in term.strip()


def query_for(item: dict, term: str, lang: str) -> str:
    return _q(term) if is_standalone(item, term) else build_query(term, RISK_KEYWORDS[lang])


def build_url(query: str, lang: str) -> str:
    hl, gl, ceid = EDITIONS[lang]
    return f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={quote(ceid)}"


# ── 제목으로 거르기 ───────────────────────────────────

def _term_pattern(term: str) -> re.Pattern:
    """영문은 단어 단위로(예: 'APT'가 'apartment'에 걸리지 않게), 한·중문은 글자 그대로 찾습니다."""
    words = [re.escape(w) for w in term.split()]
    body = r"[\s\-]*".join(words)  # 'micro-D connector', 'micro D connector' 모두 허용
    if re.fullmatch(r"[A-Za-z0-9 .\-]+", term):
        return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(body, re.IGNORECASE)


def make_matcher(item: dict):
    """제목 → 찾은 품목 키워드(없으면 '')"""
    terms = [t for ts in item_terms(item).values() for t in ts]
    # 품목명 자체도 키워드로 인정 (예: '텅스텐 분말')
    terms.append(item["item"])
    pats = [(t, _term_pattern(t)) for t in sorted(set(terms), key=len, reverse=True)]

    def match(title: str) -> str:
        for t, p in pats:
            if p.search(title or ""):
                return t
        return ""

    return match


def is_excluded(title: str) -> bool:
    return bool(EXCLUDE_TITLE.search(title or ""))


# ── 수집 ──────────────────────────────────────────────

def _to_kst(entry) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return ""
    dt = datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(KST)
    return dt.strftime("%Y-%m-%d %H:%M")


def _clean_title(title: str, source: str) -> str:
    """Google News 제목 끝의 ' - 언론사'를 떼어냅니다."""
    title = (title or "").strip()
    if source and title.endswith(f" - {source}"):
        title = title[: -len(f" - {source}")].strip()
    return title


def fetch(query: str, lang: str) -> list[dict]:
    resp = requests.get(build_url(query, lang), headers=HEADERS, timeout=20)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    rows = []
    for e in feed.entries:
        source = (e.get("source") or {}).get("title", "")
        rows.append(
            {
                "published": _to_kst(e),
                "title": _clean_title(e.get("title", ""), source),
                "source": source,
                "link": e.get("link", ""),
            }
        )
    return rows


def collect(items: list[dict], pause: float = 1.0, log=print) -> pd.DataFrame:
    """품목 키워드마다 '품목 키워드 + 위험 키워드' 조합으로 검색합니다."""
    collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    rows: list[dict] = []
    for it in items:
        for lang, terms in item_terms(it).items():
            for term in terms:
                try:
                    found = fetch(query_for(it, term, lang), lang)
                    for r in found:
                        rows.append({**r, "item": it["item"], "lang": lang, "keyword": "",
                                     "collected_at": collected_at})
                    tag = " (단독 검색)" if is_standalone(it, term) else ""
                    log(f"  [{it['item']}] ({lang}) {term}{tag}: {len(found)}건")
                except Exception as exc:  # 한 검색이 실패해도 나머지는 계속
                    log(f"  [{it['item']}] ({lang}) {term}: 실패 {exc}")
                time.sleep(pause)  # 구글에 부담을 주지 않도록 잠깐 쉼
    return pd.DataFrame(rows, columns=NEWS_COLUMNS)


def filter_news(df: pd.DataFrame, items: list[dict]) -> tuple[pd.DataFrame, dict]:
    """제목 기준으로 관련 없는 기사를 걸러냅니다. (남은 표, 제외 사유별 건수)"""
    matchers = {it["item"]: make_matcher(it) for it in items}
    keep, reasons = [], {"품목 키워드 없음": 0, "시장 보고서·광고": 0}
    for _, r in df.iterrows():
        m = matchers.get(r["item"])
        if m is None:  # 지금 시트에 없는 품목의 과거 기록은 그대로 둠
            keep.append(r)
            continue
        if is_excluded(r["title"]):
            reasons["시장 보고서·광고"] += 1
            continue
        term = m(r["title"])
        if not term:
            reasons["품목 키워드 없음"] += 1
            continue
        r = r.copy()
        r["keyword"] = term
        keep.append(r)
    out = pd.DataFrame(keep, columns=NEWS_COLUMNS) if keep else pd.DataFrame(columns=NEWS_COLUMNS)
    return out, reasons


# ── 저장 ──────────────────────────────────────────────

def _title_key(title: str) -> str:
    """제목 비교용: 공백·기호를 지우고 소문자로"""
    return re.sub(r"[\W_]+", "", str(title)).lower()


def merge_and_save(new: pd.DataFrame, items: list[dict], path: str | Path = "data/news.csv") -> dict:
    """기존 파일과 합치고, 제목으로 거르고, 중복을 지워 저장합니다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    else:
        old = pd.DataFrame(columns=NEWS_COLUMNS)

    old_links = set(zip(old["item"], old["link"]))
    df = pd.concat([old, new], ignore_index=True)  # 기존 기사를 앞에 두어 먼저 수집된 기록을 남김
    df = df[df["title"].astype(str).str.strip() != ""]
    # 기존 기록도 매번 같은 기준으로 다시 거름 (키워드·기준이 바뀌면 바로 반영)
    df, reasons = filter_news(df, items)
    df = df.drop_duplicates(subset=["item", "link"], keep="first")
    df["_tkey"] = df["title"].map(_title_key)
    df = df.drop_duplicates(subset=["item", "_tkey"], keep="first").drop(columns="_tkey")
    df = df.sort_values("published", ascending=False)

    # utf-8-sig: 엑셀에서 열어도 한글·중국어가 깨지지 않음
    df[NEWS_COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
    added = sum(1 for k in zip(df["item"], df["link"]) if k not in old_links)
    return {"added": added, "total": len(df), "removed": reasons, "before": len(old)}
