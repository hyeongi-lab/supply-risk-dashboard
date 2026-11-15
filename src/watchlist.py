"""감시 품목 목록 읽기.

기준은 구글 시트 "[구매팀] 감시 품목 관리"입니다.
시트는 '링크가 있는 모든 사용자 - 뷰어'로 공유되어 있어서,
로그인이나 인증 파일 없이 CSV 내보내기 주소로 바로 읽습니다.

시트를 읽지 못하면(인터넷 끊김, 공유 설정 변경 등) 이유를 알려주고
로컬 watchlist.csv로 대신 읽습니다(예비용).
"""

import io
import os
import re
from pathlib import Path

import pandas as pd
import requests

# 구글 시트 주소의 /d/ 와 /edit 사이 값 (CLAUDE.md의 시트)
SHEET_ID = os.environ.get("WATCHLIST_SHEET_ID", "1snMBHS7KlDA7Jghxc73j0ShIz2ZhgyTRZ49vhsQml9s")
# 읽을 탭 번호(gid). 주소창의 #gid=숫자. 비워 두면 첫 번째 탭을 읽음
SHEET_GID = os.environ.get("WATCHLIST_SHEET_GID", "")
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv" + (
    f"&gid={SHEET_GID}" if SHEET_GID else ""
)

BASE = Path(__file__).resolve().parent.parent
DEFAULT_CSV = BASE / "watchlist.csv"

KEYWORD_COLUMNS = {
    "en": "검색 키워드(영문)",
    "zh": "검색 키워드(중문)",
    "ko": "검색 키워드(한글)",
}
ALL_COLUMNS = [
    "사용여부", "품목명", "구분", "상세 사양", "제조사/공급사", "주 원산지", "의존도",
    "리드타임", "대체 공급선", "중요도", *KEYWORD_COLUMNS.values(), "가격 지표", "비고",
]
REQUIRED_COLUMNS = ["사용여부", "품목명", "구분", *KEYWORD_COLUMNS.values()]
# 대시보드 품목 카드에 표시하는 품목 정보 (CLAUDE.md 정보 관리 원칙: 민감 자료 아님)
INFO_COLUMNS = ["주 원산지", "의존도", "리드타임", "대체 공급선", "중요도"]


# ── 공통 처리 ─────────────────────────────────────────

def _squash(name: str) -> str:
    """열 이름 비교용: 공백 제거"""
    return re.sub(r"\s+", "", str(name))


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """시트 열 이름이 조금 달라도 맞춰 줍니다.
    예) '사용여부(Y/N)' → '사용여부', '중요도(상/중/하)' → '중요도', '검색 키워드 (영문)' → '검색 키워드(영문)'
    """
    lookup = {_squash(c): c for c in ALL_COLUMNS}
    renamed = {}
    for col in df.columns:
        key = _squash(col)
        if key in lookup:
            renamed[col] = lookup[key]
            continue
        # 끝에 붙은 괄호 설명 떼고 다시 비교 (사용여부(Y/N) 등)
        base = re.sub(r"\(.*?\)$", "", key)
        if base in lookup and "(" not in lookup[base]:
            renamed[col] = lookup[base]
    return df.rename(columns=renamed)


def _split_keywords(text: str) -> list[str]:
    """'a;b; c' → ['a', 'b', 'c'] (빈 값은 버림)"""
    return [k.strip() for k in str(text).split(";") if k.strip()]


def _rows_to_items(df: pd.DataFrame) -> list[dict]:
    """표 → 사용여부=Y인 품목 목록"""
    df = _normalize_columns(df)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"품목 목록에 필요한 열이 없습니다: {', '.join(missing)}\n"
            f"  지금 있는 열: {', '.join(map(str, df.columns))}"
        )

    items = []
    for _, row in df.iterrows():
        if str(row["사용여부"]).strip().upper() != "Y":
            continue
        name = str(row["품목명"]).strip()
        if not name:
            continue
        items.append(
            {
                "item": name,
                "category": str(row["구분"]).strip(),
                "info": {c: str(row[c]).strip() if c in df.columns else "" for c in INFO_COLUMNS},
                "price_indicator": str(row["가격 지표"]).strip() if "가격 지표" in df.columns else "",
                "keywords": {
                    lang: _split_keywords(row[col]) for lang, col in KEYWORD_COLUMNS.items()
                },
            }
        )
    return items


def _read_csv_bytes(data: bytes) -> pd.DataFrame:
    # utf-8-sig: 엑셀에서 저장한 CSV(BOM 포함)도 읽히도록
    return pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")


# ── 읽기 ──────────────────────────────────────────────

def load_watchlist(path: str | Path = DEFAULT_CSV) -> list[dict]:
    """로컬 watchlist.csv (예비용)"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"품목 목록 파일이 없습니다: {path.resolve()}")
    return _rows_to_items(_read_csv_bytes(path.read_bytes()))


def load_watchlist_from_sheet(url: str = SHEET_CSV_URL) -> list[dict]:
    """구글 시트를 CSV 내보내기 주소로 읽습니다."""
    resp = requests.get(url, timeout=30)
    ctype = resp.headers.get("content-type", "")
    if resp.status_code in (401, 403) or (resp.ok and "text/csv" not in ctype):
        raise PermissionError(
            "구글 시트가 공개로 열리지 않습니다. 시트 [공유] → [일반 액세스]가 "
            "'링크가 있는 모든 사용자 - 뷰어'인지 확인하세요."
        )
    if resp.status_code in (400, 404):
        raise FileNotFoundError("구글 시트를 찾을 수 없습니다. 시트 주소(SHEET_ID)나 탭 번호(gid)를 확인하세요.")
    resp.raise_for_status()
    return _rows_to_items(_read_csv_bytes(resp.content))


def load_items(log=print) -> list[dict]:
    """구글 시트에서 품목을 읽습니다. 실패하면 이유를 알리고 watchlist.csv로 대신 읽습니다."""
    try:
        items = load_watchlist_from_sheet()
        log(f"품목 목록: 구글 시트에서 읽음 ({len(items)}개)")
        return items
    except ValueError:
        raise  # 시트 열 이름 문제는 예비 파일로 넘기지 않고 바로 알림
    except Exception as exc:
        log(f"[주의] 구글 시트를 읽지 못했습니다: {exc}")
        log("품목 목록: watchlist.csv로 대신 읽음")
        return load_watchlist(DEFAULT_CSV)
