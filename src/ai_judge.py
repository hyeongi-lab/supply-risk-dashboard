"""Claude API로 품목별 위험 단계·판단 이유·권장 조치·예상 리드타임, 기사별 위험 단계, 오늘의 구매 브리핑을 만듭니다.

- 하루 1번, 요청 1번으로 모든 품목을 함께 판단합니다. (품목 간 비교가 되고 비용이 적음)
- 보내는 것: 품목 정보(시트), 최근 30일 기사 제목, 가격 요약, 정부 공고 제목, 규칙 기반 판단 결과
  (CLAUDE.md 정보 관리 원칙: 품목 정보는 AI에 보내도 됨. API 키 등 비밀 값은 보내지 않음)
- 위험도 = 외부 위험(뉴스·공고·가격) × 당사 노출도(의존도, 리드타임, 대체 공급선, 중요도)
"""

import json
import os
from datetime import datetime, timedelta, timezone

import anthropic
import pandas as pd

KST = timezone(timedelta(hours=9))
MODEL = "claude-opus-5-5"
PRICE_PER_MTOK = {"input": 4.00, "output": 20.00}  # USD, claude-opus-5-5
LEVELS = ["관심", "주의", "경계", "심각"]

SYSTEM = """당신은 제조업체 구매팀의 원자재·부품 수급 리스크 분석가입니다.
매일 아침 감시 품목별로 위험 단계를 판단하고, 구매 담당자가 바로 행동할 수 있게 한국어로 짧고 분명하게 씁니다.

위험 단계 기준 (회사 기준)
- 관심: 관련 뉴스가 평소 수준. 대시보드에 표시만 함
- 주의: 관련 기사 급증, 또는 가격 1주 10% 이상 상승. 추이 주시
- 경계: 주요 생산국의 수출 규제 '검토·발표' 보도, 무역 보복 조치 발표. 담당자 즉시 확인, 재고·공급사 점검
- 심각: 당사 품목에 대한 수출통제가 '확정·시행'됨. 팀 전체 알림, 긴급 확보·대체 공급선 검토

판단 방법
- 위험도 = 외부 위험(뉴스·정부 공고·가격) × 당사 노출도(주 원산지 의존도, 리드타임, 대체 공급선 유무, 중요도)
- 같은 외부 사건이라도 의존도가 높고 리드타임이 길고 대체 공급선이 약하면 한 단계 높게 봅니다.
- 오래전에 이미 시행된 조치는 '현재 진행 중인 제약'으로 보고, 최근 30일 안의 새 변화가 있는지를 중심으로 판단합니다.
- 규칙 기반 판단 결과가 함께 주어집니다. 그보다 낮게 판단하려면 그 이유(예: 단어만 겹친 무관한 기사)를 판단 이유에 밝힙니다.
- 근거가 없는 추측은 쓰지 않습니다. 기사·공고·가격에 없는 사실을 만들지 않습니다.
- 시트 값이 '(확인 필요)'처럼 비어 있으면 일반적인 시장 지식으로 보수적으로 가정하고, 가정했다고 밝힙니다.

쓰는 방법
- reason(판단 이유): 1~2문장. 어떤 기사·공고·가격 때문인지 구체적으로
- action(권장 조치): 1문장. 구매 담당자가 오늘 할 일
- lead_time_estimate: 현재 상황에서 예상되는 실제 리드타임 (예: "4~6개월 (평소 4개월)")
- lead_time_basis: 그렇게 본 근거 1문장
- briefing: 오늘의 구매 브리핑 정확히 3줄. 가장 중요한 것부터. 한 줄 60자 안팎
- articles: 주어진 기사 id마다 위험 단계와 한 문장 이유"""

SCHEMA = {
    "type": "object",
    "properties": {
        "briefing": {"type": "array", "items": {"type": "string"}},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "level": {"type": "string", "enum": LEVELS},
                    "reason": {"type": "string"},
                    "action": {"type": "string"},
                    "lead_time_estimate": {"type": "string"},
                    "lead_time_basis": {"type": "string"},
                },
                "required": ["item", "level", "reason", "action", "lead_time_estimate", "lead_time_basis"],
                "additionalProperties": False,
            },
        },
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "level": {"type": "string", "enum": LEVELS},
                    "reason": {"type": "string"},
                },
                "required": ["id", "level", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["briefing", "items", "articles"],
    "additionalProperties": False,
}


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def build_input(items: list[dict], rule: dict, news: pd.DataFrame, notices: pd.DataFrame,
                now: datetime, news_days: int = 30, per_item: int = 15) -> tuple[str, dict]:
    """AI에 보낼 자료(글)와, 기사 id → (품목, 링크) 표를 만듭니다."""
    article_map = {}
    lines = [f"오늘: {now:%Y-%m-%d} (한국 시간)", ""]
    news = news.copy()
    if len(news):
        news["dt"] = pd.to_datetime(news["published"], errors="coerce")

    for it in items:
        name = it["item"]
        r = rule.get(name, {})
        ps = r.get("price") or {}
        lines.append(f"## 품목: {name} ({it.get('category', '')})")
        for f in ["주 원산지", "의존도", "리드타임", "대체 공급선", "중요도"]:
            lines.append(f"- {f}: {it.get(f) or '(미입력)'}")
        if ps:
            ch = ps.get("change_1w")
            lines.append(f"- 시장 가격: {ps['series']} {ps['price']:,.2f} {ps['unit']} ({ps['date']}), "
                         f"1주 변동 {'-' if ch is None else f'{ch:+.1%}'}")
        else:
            lines.append(f"- 시장 가격: 없음 ({it.get('price_indicator') or '가격 지표 없음'})")
        lines.append(f"- 규칙 기반 판단: {r.get('level_name', '관심')} — {' / '.join(r.get('reasons', []))}")

        sub = news[(news["item"] == name) & (news["dt"] >= now - timedelta(days=news_days))] if len(news) else news
        sub = sub.sort_values("dt", ascending=False).head(per_item) if len(sub) else sub
        lines.append(f"- 최근 {news_days}일 기사 {len(sub)}건 (id | 날짜 | 언어 | 제목 | 언론사):")
        for _, a in sub.iterrows():
            aid = f"A{len(article_map) + 1}"
            article_map[aid] = (name, a["link"])
            lines.append(f"  {aid} | {a['published'][:10]} | {a['lang']} | {a['title']} | {a['source']}")
        lines.append("")

    if len(notices):
        n = notices.copy()
        n["dt"] = pd.to_datetime(n["date"], errors="coerce")
        related = n[n["related_items"] != ""].sort_values("dt", ascending=False).head(10)
        recent = n[(n["dt"] >= now - timedelta(days=30)) & (n["related_items"] == "")].sort_values("dt", ascending=False).head(20)
        lines.append("## 당사 품목 키워드가 들어간 정부 공고 (최근 순)")
        lines += [f"- {r.date} | {r.country} {r.agency} | {r.title} | 관련: {r.related_items}" for r in related.itertuples()] or ["- 없음"]
        lines.append("\n## 최근 30일 기타 정부 수출통제 공고 (품목 키워드 없음, 간접 영향 판단용)")
        lines += [f"- {r.date} | {r.country} {r.agency} | {r.title}" for r in recent.itertuples()] or ["- 없음"]

    return "\n".join(lines), article_map


def judge(items: list[dict], rule: dict, news: pd.DataFrame, notices: pd.DataFrame,
          now: datetime | None = None, log=print) -> dict:
    """Claude API 판단 결과 {briefing, items, articles, usage}"""
    now = now or datetime.now(KST).replace(tzinfo=None)
    text, article_map = build_input(items, rule, news, notices, now)
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        # Claude Opus 5.5가 안전 기준으로 답을 거절하면 같은 요청을 다른 모델로 자동 재시도
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": "아래 자료로 오늘의 판단을 해 주세요.\n\n" + text}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("AI가 판단을 거절했습니다. (규칙 기반 결과를 그대로 씁니다)")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("AI 답이 너무 길어 중간에 끊겼습니다. (규칙 기반 결과를 그대로 씁니다)")
    data = json.loads(next(b.text for b in response.content if b.type == "text"))

    u = response.usage
    cost = u.input_tokens / 1e6 * PRICE_PER_MTOK["input"] + u.output_tokens / 1e6 * PRICE_PER_MTOK["output"]
    log(f"  AI 사용량: 입력 {u.input_tokens:,} / 출력 {u.output_tokens:,} 토큰 (약 ${cost:.3f}, 모델 {response.model})")

    for a in data["articles"]:
        a["item"], a["link"] = article_map.get(a["id"], ("", ""))
    data["articles"] = [a for a in data["articles"] if a["link"]]
    data["briefing"] = data["briefing"][:3]
    data["model"] = response.model
    data["cost_usd"] = round(cost, 4)
    return data
