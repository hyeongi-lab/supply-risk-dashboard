"""품목별 시장 가격 수집 → data/prices.csv

출처: 한국자원정보서비스 KOMIS (https://www.komis.or.kr) 광물자원가격 화면의 일간 가격 데이터
- 텅스텐 APT: Tungsten APT 88.5%min EXW China (원 출처 AsianMetal), USD/mt
- 알루미늄:   LME CASH, USD/ton

주의: KOMIS 공식 API가 아니라 가격 화면이 내부적으로 쓰는 데이터 주소입니다.
하루 1번, 품목당 1번만 요청합니다. KOMIS 화면 구조가 바뀌면 오류로 알려줍니다.

어떤 품목에 어떤 가격을 붙일지는 구글 시트의 '가격 지표' 열로 정합니다.
  'APT' 가 들어 있으면 → 텅스텐 APT
  'LME' 와 '알루미늄/aluminum' 이 들어 있으면 → 알루미늄 LME
"""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

KOMIS_URL = "https://www.komis.or.kr/Komis/RsrcPrice/ajax/getChartData"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36",
           "X-Requested-With": "XMLHttpRequest"}

# KOMIS 화면에서 확인한 코드 (광종, 가격기준 번호, 규격, 화면 구분)
PRICE_SOURCES = {
    "tungsten_apt": {
        "label": "텅스텐 APT",
        "match": lambda s: bool(re.search(r"\bAPT\b|파라텅스텐|ammonium paratungstate", s, re.I)),
        "form": {"mnrkndUnqRadioCd": "MNRL0018", "srchMnrkndUnqCd": "MNRL0018", "srchPrcCrtr": "845",
                 "spcfct": "88.5%", "HP000": "HP002"},
        "source": "KOMIS (AsianMetal, APT 88.5% EXW China)",
    },
    "aluminium_lme": {
        "label": "알루미늄 LME",
        "match": lambda s: bool(re.search(r"LME", s, re.I) and re.search(r"알루미늄|alumin", s, re.I)),
        "form": {"mnrkndUnqRadioCd": "MNRL0009", "srchMnrkndUnqCd": "MNRL0009", "srchPrcCrtr": "495",
                 "HP000": "HP001"},
        "source": "KOMIS (LME CASH)",
    },
}

PRICE_COLUMNS = ["date", "item", "series", "price", "unit", "source"]


def source_for(indicator: str) -> str | None:
    """시트 '가격 지표' 글 → 가격 출처 키 (없으면 None)"""
    for key, src in PRICE_SOURCES.items():
        if src["match"](indicator or ""):
            return key
    return None


def fetch_series(key: str, start_year: int) -> pd.DataFrame:
    """KOMIS에서 일간 가격을 받아옵니다. (date, price, unit)"""
    src = PRICE_SOURCES[key]
    form = {**src["form"], "srchAvgOpt": "DAY", "srchField": "year",
            "srchStartDate": str(start_year), "srchEndDate": str(datetime.now().year),
            "srchCompareMnrkndUnqCd": "", "srchComparePrcCrtr": "", "lmeInvt": "N"}
    resp = requests.post(KOMIS_URL, data=form, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    dates, series = data.get("xaxis") or [], data.get("series") or []
    if not dates or not series or not series[0].get("data"):
        raise ValueError(f"KOMIS에서 {src['label']} 가격을 받지 못했습니다. (화면 구조가 바뀌었을 수 있음)")
    info = data.get("mnrlInfo") or {}
    unit = f"{info.get('prcUnitCdNm', 'USD')}/{info.get('weigUnitCd', '')}".rstrip("/")
    df = pd.DataFrame({"date": pd.to_datetime(dates, format="%Y.%m.%d").strftime("%Y-%m-%d"),
                       "price": pd.to_numeric(series[0]["data"], errors="coerce")})
    df["unit"] = unit
    return df.dropna(subset=["price"])


def collect_prices(items: list[dict], start_year: int = 2024, log=print) -> pd.DataFrame:
    rows = []
    for it in items:
        key = source_for(it.get("price_indicator", ""))
        if not key:
            log(f"  [{it['item']}] 가격 지표 없음 → 건너뜀")
            continue
        src = PRICE_SOURCES[key]
        try:
            df = fetch_series(key, start_year)
            df["item"], df["series"], df["source"] = it["item"], src["label"], src["source"]
            rows.append(df)
            log(f"  [{it['item']}] {src['label']} {len(df)}일치 (최근 {df['date'].iloc[-1]}: {df['price'].iloc[-1]:,.2f} {df['unit'].iloc[-1]})")
        except Exception as exc:
            log(f"  [{it['item']}] {src['label']} 실패: {exc}")
    return pd.concat(rows, ignore_index=True)[PRICE_COLUMNS] if rows else pd.DataFrame(columns=PRICE_COLUMNS)


def merge_and_save(new: pd.DataFrame, path: str | Path = "data/prices.csv") -> int:
    path = Path(path)
    old = pd.read_csv(path, dtype={"price": float}, keep_default_na=False, encoding="utf-8-sig") \
        if path.exists() else pd.DataFrame(columns=PRICE_COLUMNS)
    # 같은 날짜는 새로 받은 값으로 (KOMIS가 값을 고치는 경우 반영)
    df = pd.concat([old, new], ignore_index=True)
    df = df.drop_duplicates(subset=["item", "series", "date"], keep="last").sort_values(["item", "date"])
    df[PRICE_COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
    return len(df)
