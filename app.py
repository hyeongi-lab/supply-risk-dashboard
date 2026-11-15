"""원자재 수급 리스크 조기경보 대시보드

실행:  .venv\\Scripts\\streamlit run app.py

화면 구성 (CLAUDE.md '목표 대시보드 화면'):
 1. '경계' 이상 품목 빨간 경고 배너
 2. AI 오늘의 구매 브리핑 + 위험 단계별 품목 수
 3. 품목 카드
 4. 가격 추이 차트 (최근 12주, 첫 주=100)
 5. 위험 이슈 표
 6. 정부 수출통제 공고 + 알림 이력

data/ 폴더의 items.json, news.csv, prices.csv, notices.csv, risk.json 을 읽어서 보여줍니다.
품목 정보(원산지, 의존도, 리드타임 등)는 민감 자료가 아니므로 표시합니다. (CLAUDE.md 정보 관리 원칙)
지금 위험 단계는 규칙 기반이고, AI 브리핑·판단 이유·예상 리드타임·메일 알림은 2단계에서 채워집니다.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from src.risk import issue_type  # noqa: E402

DATA = BASE / "data"
KST = timezone(timedelta(hours=9))
LEVELS = ["관심", "주의", "경계", "심각"]
LEVEL_COLOR = {"관심": "#2e7d32", "주의": "#f9a825", "경계": "#ef6c00", "심각": "#c62828"}
LEVEL_ICON = {"관심": "🟢", "주의": "🟡", "경계": "🟠", "심각": "🔴"}
IMPORTANCE_ORDER = {"상": 0, "중": 1, "하": 2}
LANG_LABELS = {"en": "영문", "zh": "중문", "ko": "한글"}
DELTA_MIN_DAYS = 7  # 수집을 시작하고 이 기간이 지나야 '전주 대비'를 보여줌

st.set_page_config(page_title="원자재 수급 리스크 조기경보", page_icon="⚠️", layout="wide")


# ── 데이터 읽기 ───────────────────────────────────────

# 파일이 바뀌면(수정 시각이 달라지면) 바로 새로 읽고, 그대로면 저장해 둔 것을 씀
def _mtime(name: str) -> float:
    p = DATA / name
    return p.stat().st_mtime if p.exists() else 0.0


@st.cache_data
def _load_json(name: str, mtime: float):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


@st.cache_data
def _load_csv(name: str, mtime: float) -> pd.DataFrame:
    p = DATA / name
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def load_json(name: str):
    return _load_json(name, _mtime(name))


def load_csv(name: str) -> pd.DataFrame:
    return _load_csv(name, _mtime(name)).copy()


items = load_json("items.json")
risks = {r["item"]: r for r in load_json("risk.json")}
news = load_csv("news.csv")
prices = load_csv("prices.csv")
notices = load_csv("notices.csv")
if len(news):
    news["published_dt"] = pd.to_datetime(news["published"], errors="coerce")
if len(prices):
    prices["date"] = pd.to_datetime(prices["date"])
    prices["price"] = pd.to_numeric(prices["price"], errors="coerce")

now = datetime.now(KST).replace(tzinfo=None)  # 서버가 해외에 있어도 한국 시간 기준


def level_of(item: str) -> str:
    return risks.get(item, {}).get("level_name", "관심")


def line_chart(df: pd.DataFrame, x: str, y: str, color: str | None = None, height: int = 300,
               y_title: str = "", mini: bool = False):
    """세로축이 0부터 시작하지 않는(변동이 잘 보이는) 선 차트"""
    enc = {"x": alt.X(f"{x}:T", title=None, axis=None if mini else alt.Axis(format="%m/%d")),
           "y": alt.Y(f"{y}:Q", title=y_title or None, scale=alt.Scale(zero=False),
                      axis=None if mini else alt.Axis()),
           "tooltip": [alt.Tooltip(f"{x}:T", title="날짜"), alt.Tooltip(f"{y}:Q", format=",.1f", title="값")]}
    if color:
        enc["color"] = alt.Color(f"{color}:N", title=None, legend=alt.Legend(orient="bottom"))
        enc["tooltip"].insert(0, alt.Tooltip(f"{color}:N", title="품목"))
    st.altair_chart(alt.Chart(df).mark_line(strokeWidth=2).encode(**enc).properties(height=height),
                    width="stretch")


def badge(level: str) -> str:
    c = LEVEL_COLOR[level]
    return (f"<span style='background:{c};color:white;padding:2px 10px;border-radius:12px;"
            f"font-weight:600'>{LEVEL_ICON[level]} {level}</span>")


# 경계 이상 카드 테두리 강조 (카드 key 끝이 _lvl2, _lvl3)
st.markdown(
    f"""<style>
    div[class*="st-key-card_"][class*="_lvl2"] {{ border: 3px solid {LEVEL_COLOR['경계']} !important; }}
    div[class*="st-key-card_"][class*="_lvl3"] {{ border: 3px solid {LEVEL_COLOR['심각']} !important; }}
    </style>""",
    unsafe_allow_html=True,
)

# ── 제목 ──────────────────────────────────────────────
st.title("⚠️ 원자재 수급 리스크 조기경보")
last = news["collected_at"].max() if len(news) else "-"
run = load_json("last_run.json") or {}
run_txt = ""
if run:
    run_txt = (f" · 자동 수집 {run.get('run_at', '')} " +
               ("정상" if run.get("ok") else f"⚠️ 일부 실패({', '.join(run.get('failed_parts', []))})"))
st.caption(f"마지막 수집: {last} (한국 시간){run_txt} · 뉴스: Google News · 가격: KOMIS · "
           f"공고: 미국 BIS, 일본 METI, 중국 상무부 · 품목 정보: 구매팀 감시 품목 시트")


def md_date(s: str) -> str:
    """'2026-11-15' → '11월 15일'"""
    try:
        d = datetime.strptime(str(s)[:10], "%Y-%m-%d")
        return f"{d.month}월 {d.day}일"
    except ValueError:
        return str(s)


ai_dates = sorted({r.get("ai_date") for r in risks.values() if r.get("ai_date")})
ai_date_txt = md_date(ai_dates[-1]) if ai_dates else ""

if not items:
    st.warning("품목 목록이 없습니다. 먼저 `collect.py`를 실행해 주세요.")
    st.stop()

# 중요도 상 → 중 → 하 → 미입력, 같은 중요도면 위험 단계 높은 순
items = sorted(items, key=lambda i: (IMPORTANCE_ORDER.get(str(i.get("중요도", "")).strip(), 9),
                                     -LEVELS.index(level_of(i["item"]))))

# ── 1. 경고 배너 ──────────────────────────────────────
alerts = [risks[i["item"]] for i in items if i["item"] in risks and risks[i["item"]]["level"] >= 2]
if alerts:
    rows = "".join(
        f"<li><b>{a['item']}</b> {badge(a['level_name'])}<br>"
        f"이유: {' / '.join(a['reasons'])}<br>권장 조치: {a['action']}<br>"
        f"메일: {a.get('mail_status', '미발송 (메일 알림은 2단계에서 연결)')}</li>"
        for a in alerts
    )
    st.markdown(
        f"<div style='background:#fdecea;border:2px solid {LEVEL_COLOR['심각']};border-radius:10px;"
        f"padding:12px 18px;margin-bottom:12px'><h4 style='color:{LEVEL_COLOR['심각']};margin:0 0 6px'>"
        f"🚨 경계 이상 품목 {len(alerts)}개</h4><ul style='margin:0'>{rows}</ul></div>",
        unsafe_allow_html=True,
    )

# ── 2. 오늘의 구매 브리핑 + 단계별 품목 수 ─────────────
left, right = st.columns([3, 2])
with left:
    with st.container(border=True):
        st.markdown("#### 🤖 오늘의 구매 브리핑")
        briefing = load_json("briefing.json")
        if briefing:
            for line in briefing.get("lines", [])[:3]:
                st.markdown(f"- {line}")
            st.caption(f"**AI 판단 기준일: {md_date(briefing.get('date', ''))}** · {briefing.get('source') or briefing.get('model', '')}")
        else:
            st.caption("AI 브리핑은 Claude API 키를 설정하면 매일 아침 표시됩니다. (.env의 ANTHROPIC_API_KEY)")
with right:
    with st.container(border=True):
        st.markdown("#### 위험 단계별 품목 수")
        counts = {lv: sum(1 for i in items if level_of(i["item"]) == lv) for lv in LEVELS}
        for col, lv in zip(st.columns(4), LEVELS):
            col.markdown(f"<div style='text-align:center'><div style='font-size:28px;font-weight:700;"
                         f"color:{LEVEL_COLOR[lv]}'>{counts[lv]}</div>{LEVEL_ICON[lv]} {lv}</div>",
                         unsafe_allow_html=True)
        if ai_date_txt:
            st.caption(f"AI 판단 기준일 {ai_date_txt}과 오늘 규칙 기반 판단 중 높은 단계 · "
                       f"규칙 판단 {next(iter(risks.values())).get('assessed_at', '')}")
        else:
            st.caption("규칙 기반 판단 (가격 1주 10%↑ 주의, 관련 공고 경계, 리드타임 3개월↑ 한 단계 상향)")

# ── 3. 품목 카드 ──────────────────────────────────────
st.subheader("품목별 현황")
first_collected = pd.to_datetime(news["collected_at"], errors="coerce").min() if len(news) else pd.NaT
days_collected = (now - first_collected).days if pd.notna(first_collected) else 0
week_ago, two_weeks_ago = now - timedelta(days=7), now - timedelta(days=14)


def val(it: dict, field: str) -> str:
    return str(it.get(field, "")).strip() or "미입력"


cols = st.columns(min(len(items), 3))
for idx, it in enumerate(items):
    r = risks.get(it["item"], {})
    lv = r.get("level_name", "관심")
    with cols[idx % len(cols)].container(border=True, key=f"card_{idx}_lvl{LEVELS.index(lv)}"):
        st.markdown(f"### {it['item']}")
        st.markdown(badge(lv) + f" &nbsp; <span style='color:gray'>{it.get('category', '')}</span>",
                    unsafe_allow_html=True)
        if r.get("ai_date"):
            raised = " · <b>규칙으로 상향</b>" if r.get("raised_by_rule") else ""
            st.markdown(f"<span style='color:gray;font-size:0.85em'>AI 판단 기준일 {md_date(r['ai_date'])}: "
                        f"{r.get('ai_level_name', '-')} · 규칙(오늘): {r.get('rule_level_name', '-')}{raised}</span>",
                        unsafe_allow_html=True)

        st.markdown(f"**주 원산지·의존도**: {val(it, '주 원산지')} · {val(it, '의존도')}  \n"
                    f"**리드타임**: {val(it, '리드타임')} <span style='color:gray'>(시트)</span> → "
                    f"**AI 예상**: {r.get('lead_time_ai') or '<span style=\"color:gray\">AI 판단 전</span>'}  \n"
                    f"**대체 공급선**: {val(it, '대체 공급선')}",
                    unsafe_allow_html=True)

        ps = r.get("price") or {}
        if ps:
            ch = ps.get("change_1w")
            ch_txt = "-" if ch is None else f"{ch:+.1%}"
            st.markdown(f"**시장 가격**: {ps['price']:,.0f} {ps['unit']} · 1주 **{ch_txt}**  \n"
                        f"<span style='color:gray;font-size:0.85em'>{ps['series']}, {ps['date']} 기준</span>",
                        unsafe_allow_html=True)
            p = prices[(prices["item"] == it["item"]) & (prices["date"] >= prices["date"].max() - pd.Timedelta(days=90))]
            line_chart(p, "date", "price", height=80, mini=True)
            st.caption("최근 90일")
        else:
            st.markdown(f"**시장 가격**: <span style='color:gray'>{it.get('price_indicator') or '가격 지표 없음'}</span>",
                        unsafe_allow_html=True)

        is_ai = bool(r.get("ai_date"))
        label = (f"AI 판단 이유 <span style='color:gray;font-size:0.85em'>({md_date(r['ai_date'])} 기준)</span>"
                 if is_ai else "판단 이유 <span style='color:gray;font-size:0.85em'>(규칙 기반)</span>")
        st.markdown(f"**{label}**  \n" + "  \n".join(f"· {x}" for x in r.get("reasons", ["판단 전"])),
                    unsafe_allow_html=True)
        st.markdown(f"**권장 조치**: {r.get('action', '-')}")

        sub = news[news["item"] == it["item"]] if len(news) else news
        this_week = int((sub["published_dt"] >= week_ago).sum()) if len(sub) else 0
        if days_collected >= DELTA_MIN_DAYS:
            prev_week = int(((sub["published_dt"] >= two_weeks_ago) & (sub["published_dt"] < week_ago)).sum())
            st.metric("최근 7일 기사 수", f"{this_week}건", delta=f"{this_week - prev_week:+d} (전주 대비)",
                      delta_color="off")
        else:
            st.metric("최근 7일 기사 수", f"{this_week}건")
            st.caption(f"전주 대비는 수집 시작 {DELTA_MIN_DAYS}일 후부터 표시 (현재 수집 {days_collected + 1}일째)")

# ── 4. 가격 추이 차트 ─────────────────────────────────
st.subheader("가격 추이 (최근 12주, 첫 주 = 100)")
if len(prices):
    start = prices["date"].max() - pd.Timedelta(weeks=12)
    weekly = (prices[prices["date"] > start]
              .pivot_table(index="date", columns="series", values="price")
              .resample("W-FRI").last().ffill().dropna(how="all"))  # 휴일로 비는 주는 직전 값으로
    index = weekly / weekly.iloc[0] * 100
    long = index.reset_index().melt(id_vars="date", var_name="series", value_name="index")
    line_chart(long, "date", "index", color="series", height=320, y_title="지수 (첫 주=100)")
    last_w = index.iloc[-1]
    st.caption(" · ".join(f"{s}: {v:.1f} ({v - 100:+.1f}%)" for s, v in last_w.items())
               + " · 출처: KOMIS (텅스텐 APT: AsianMetal, 알루미늄: LME CASH)")
else:
    st.info("가격 데이터가 없습니다. `collect.py`를 실행해 주세요.")

# ── 5. 위험 이슈 표 ───────────────────────────────────
st.subheader("위험 이슈")
if len(news):
    issues = news.copy()
    issues["유형"] = issues["title"].map(issue_type)
    ai_issues = load_csv("issues_ai.csv")
    if len(ai_issues):
        issues = issues.merge(ai_issues[["item", "link", "level", "reason"]], on=["item", "link"], how="left")
    else:
        issues["level"], issues["reason"] = None, None
    issues["단계"] = issues["level"].fillna("판단 전").replace("", "판단 전")
    issues["AI 판단 이유"] = issues["reason"].fillna("").replace("", "-")
    f1, f2, f3, f4 = st.columns(4)
    level_opts = LEVELS + ["판단 전"]
    sel_level = f1.multiselect("단계", level_opts, default=level_opts, disabled=not len(ai_issues),
                               help=None if len(ai_issues) else "AI 판단 후 사용할 수 있습니다.")
    sel_items = f2.multiselect("품목", [i["item"] for i in items], default=[i["item"] for i in items])
    types = ["수출통제", "관세·무역분쟁", "제재", "공급 차질", "공급사 변화", "가격", "기타"]
    sel_types = f3.multiselect("유형", types, default=types)
    period = f4.selectbox("기간", ["최근 7일", "최근 30일", "최근 1년", "전체"], index=1)
    cut = {"최근 7일": 7, "최근 30일": 30, "최근 1년": 365}.get(period)
    v = issues[issues["item"].isin(sel_items) & issues["유형"].isin(sel_types) & issues["단계"].isin(sel_level)]
    if cut:
        v = v[v["published_dt"] >= now - timedelta(days=cut)]
    v = v.sort_values("published_dt", ascending=False)
    st.caption(f"{len(v)}건 · 열 제목을 누르면 정렬됩니다")
    st.dataframe(
        pd.DataFrame({"날짜": v["published"].str[:10],
                      "단계": v["단계"].map(lambda s: f"{LEVEL_ICON.get(s, '⚪')} {s}"),
                      "품목": v["item"], "유형": v["유형"], "제목": v["title"], "AI 판단 이유": v["AI 판단 이유"],
                      "언어": v["lang"].map(LANG_LABELS), "원문": v["link"]}),
        hide_index=True, width="stretch", height=420,
        column_config={"제목": st.column_config.TextColumn(width="large"),
                       "원문": st.column_config.LinkColumn(display_text="열기", width="small")},
    )
else:
    st.info("뉴스가 없습니다. `collect.py`를 실행해 주세요.")

# ── 6. 정부 수출통제 공고 + 알림 이력 ──────────────────
st.subheader("정부 수출통제 공고")
if len(notices):
    g1, g2 = st.columns([1, 3])
    only_related = g1.toggle("당사 품목 관련만", value=False)
    sel_country = g2.multiselect("국가", ["미국", "일본", "중국"], default=["미국", "일본", "중국"])
    n = notices[notices["country"].isin(sel_country)]
    if only_related:
        n = n[n["related_items"] != ""]
    st.caption(f"{len(n)}건 (당사 품목 관련 {int((notices['related_items'] != '').sum())}건)")
    st.dataframe(
        pd.DataFrame({"날짜": n["date"], "국가": n["country"], "기관": n["agency"], "제목": n["title"],
                      "관련 품목": n["related_items"].replace("", "-"), "원문": n["link"]}),
        hide_index=True, width="stretch", height=360,
        column_config={"제목": st.column_config.TextColumn(width="large"),
                       "원문": st.column_config.LinkColumn(display_text="열기", width="small")},
    )
else:
    st.info("공고 데이터가 없습니다. `collect.py`를 실행해 주세요.")

st.subheader("알림 이력")
alert_log = load_csv("alerts.csv")
if len(alert_log):
    st.dataframe(
        alert_log.sort_values("sent_at", ascending=False).rename(columns={
            "sent_at": "발송 시각", "item": "품목", "level": "단계", "recipients": "받는 사람",
            "status": "결과", "subject": "제목"}),
        hide_index=True, width="stretch")
else:
    st.caption("아직 보낸 알림이 없습니다. '경계' 이상이 되면 메일을 보내고 여기에 기록합니다.")
