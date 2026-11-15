"""품목별 위험 단계 계산 (규칙 기반) → data/risk.json

2단계에서 Claude API 판단이 들어오기 전까지 쓰는 기본 규칙입니다. (CLAUDE.md '위험 판단 4단계')
  🟢 관심: 아래 어디에도 해당 없음
  🟡 주의: 가격이 1주에 10% 이상 상승
  🟠 경계: 최근 30일 안에 당사 품목 관련 정부 공고
  리드타임 3개월 이상이면, 주의 이상일 때 한 단계 올림 (최대 🔴 심각)
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

KST = timezone(timedelta(hours=9))

LEVELS = ["관심", "주의", "경계", "심각"]
PRICE_JUMP = 0.10          # 1주 가격 상승률 기준
NOTICE_DAYS = 30           # 관련 공고를 보는 기간
LONG_LEAD_MONTHS = 3       # 리드타임이 이 이상이면 한 단계 상향

# 기획서 6장 '조치'
ACTIONS = {
    "관심": "대시보드에서 동향만 확인",
    "주의": "가격·기사 추이 주시, 일일 브리핑에 포함",
    "경계": "담당자 즉시 확인, 재고·공급사 점검",
    "심각": "팀 전체 공유, 긴급 확보·대체 공급선 검토",
}

# 위험 이슈 유형 (기사 제목의 단어로 분류)
ISSUE_TYPES = [
    ("수출통제", r"export (control|ban|restriction|licen[cs]e)|出口管制|出口禁令|出口限制|出口许可|수출 ?(통제|규제|금지|허가)"),
    ("관세·무역분쟁", r"tariff|trade war|retaliat|关税|贸易战|反制|관세|무역 ?(전쟁|분쟁)|보복"),
    ("제재", r"sanction|embargo|entity list|制裁|实体清单|제재"),
    ("공급 차질", r"shortage|supply (disruption|chain|crunch|risk)|lead time|discontinu|短缺|断供|供应|수급|공급 ?차질|품귀|납기|단종"),
    ("공급사 변화", r"acquir|acquisition|merger|buys|relocat|bankrupt|收购|并购|破产|인수|합병|파산|(공장|본사|사업장) ?이전"),
    ("가격", r"price|prices|涨价|价格|가격|급등|폭등"),
]


def issue_type(title: str) -> str:
    for name, pat in ISSUE_TYPES:
        if re.search(pat, title or "", re.I):
            return name
    return "기타"


def lead_time_months(text: str) -> float | None:
    """'4개월' → 4, '12주' → 2.8, '90일' → 3.0. 숫자가 없으면 None"""
    t = str(text or "")
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(개월|달|months?)", t, re.I):
        return float(m.group(1))
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(주|weeks?)", t, re.I):
        return round(float(m.group(1)) / 4.345, 1)
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(일|days?)", t, re.I):
        return round(float(m.group(1)) / 30, 1)
    return None


def price_summary(prices: pd.DataFrame, item: str) -> dict:
    """최근 가격과 1주 변동률"""
    p = prices[prices["item"] == item].copy()
    if p.empty:
        return {}
    p["date"] = pd.to_datetime(p["date"])
    p = p.sort_values("date")
    last = p.iloc[-1]
    before = p[p["date"] <= last["date"] - pd.Timedelta(days=7)]
    change = (last["price"] / before.iloc[-1]["price"] - 1) if len(before) else None
    return {"series": last["series"], "date": last["date"].strftime("%Y-%m-%d"),
            "price": float(last["price"]), "unit": last["unit"],
            "change_1w": None if change is None else round(float(change), 4)}


def assess(items: list[dict], prices: pd.DataFrame, notices: pd.DataFrame, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(KST).replace(tzinfo=None)
    recent = notices[pd.to_datetime(notices["date"], errors="coerce") >= now - timedelta(days=NOTICE_DAYS)] \
        if len(notices) else notices
    out = []
    for it in items:
        level, reasons = 0, []
        ps = price_summary(prices, it["item"]) if len(prices) else {}
        if ps.get("change_1w") is not None and ps["change_1w"] >= PRICE_JUMP:
            level = max(level, 1)
            reasons.append(f"{ps['series']} 가격 1주 {ps['change_1w']:+.1%} 상승 (기준 {PRICE_JUMP:.0%})")

        rel = recent[recent["related_items"].str.contains(re.escape(it["item"]), na=False)] if len(recent) else recent
        if len(rel):
            level = max(level, 2)
            top = rel.sort_values("date", ascending=False).iloc[0]
            reasons.append(f"최근 {NOTICE_DAYS}일 관련 정부 공고 {len(rel)}건 (최근: {top['country']} {top['date']} '{top['title'][:40]}')")

        lt = lead_time_months(it["info"].get("리드타임", ""))
        if level >= 1 and lt is not None and lt >= LONG_LEAD_MONTHS and level < 3:
            level += 1
            reasons.append(f"리드타임 {lt:g}개월(≥{LONG_LEAD_MONTHS}개월) → 한 단계 상향")

        if not reasons:
            reasons.append("가격 급등·관련 정부 공고 없음")
        name = LEVELS[level]
        out.append({"item": it["item"], "level": level, "level_name": name, "reasons": reasons,
                    "action": ACTIONS[name], "price": ps, "lead_time_months": lt,
                    "method": "규칙", "assessed_at": now.strftime("%Y-%m-%d %H:%M")})
    return out


def save(result: list[dict], path: str | Path = "data/risk.json") -> None:
    Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
