"""위험 판단 + 메일 알림 실행 파일 (수집은 하지 않음. data/ 폴더에 있는 자료로만 판단)

실행:              .venv\\Scripts\\python analyze.py
수동 판단 지정:     .venv\\Scripts\\python analyze.py --manual data\\judgments\\2026-11-15.json
메일 시험:          .venv\\Scripts\\python analyze.py --test-mail

AI 판단을 어디서 가져오는지 (위에서부터 먼저 해당하는 것)
  1) --manual 파일을 지정하면 그 파일
  2) ANTHROPIC_API_KEY가 있으면 Claude API로 오늘 판단  (GitHub Secrets에 키를 넣으면 자동으로 이 방식)
  3) 없으면 data/judgments/ 의 가장 최근 수동 판단 파일 (YYYY-MM-DD.json)
  → 'AI 판단 기준일'을 함께 기록합니다. (수동 판단이면 그 파일 날짜)

화면 단계 = AI 판단과 오늘 규칙 기반 판단 중 '높은 단계' (2026-11-15 사용자 결정)
메일 = '경계' 이상이면서 단계가 '올라갔을 때만' (같은 단계 유지 중에는 다시 안 보냄)

저장: data/risk.json, data/briefing.json, data/issues_ai.csv, data/alerts.csv, data/alert_state.json
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from src import ai_judge, alert, risk

sys.stdout.reconfigure(errors="replace")
BASE = Path(__file__).parent
DATA = BASE / "data"
JUDGMENTS = DATA / "judgments"
KST = timezone(timedelta(hours=9))
LEVELS = ai_judge.LEVELS
load_dotenv(BASE / ".env")  # 내 PC: .env / GitHub Actions: Secrets가 환경 변수로 들어옴


def _read(name: str, **kw) -> pd.DataFrame:
    p = DATA / name
    return pd.read_csv(p, keep_default_na=False, encoding="utf-8-sig", **kw) if p.exists() else pd.DataFrame()


def latest_judgment_file() -> Path | None:
    """data/judgments/ 에서 날짜 이름(YYYY-MM-DD.json)인 가장 최근 파일. '_1차' 같은 보관본은 제외"""
    files = sorted(p for p in JUDGMENTS.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))
    return files[-1] if files else None


def load_manual(path: Path, items, rule, news, notices, now) -> dict:
    out = json.loads(path.read_text(encoding="utf-8"))
    _, amap = ai_judge.build_input(items, rule, news, notices, now)
    for a in out["articles"]:
        if not a.get("link"):
            a["item"], a["link"] = amap.get(a["id"], ("", ""))
    out["articles"] = [a for a in out["articles"] if a.get("link")]
    out.setdefault("model", "수동 판단")
    out.setdefault("cost_usd", 0)
    m = re.match(r"\d{4}-\d{2}-\d{2}", path.stem)
    out["ai_date"] = m.group(0) if m else now.strftime("%Y-%m-%d")
    out["source"] = f"수동 판단 파일 {path.name}"
    return out


def main() -> None:
    if "--test-mail" in sys.argv:
        if alert.simulate():
            print("MAIL_SIMULATE=1 상태라 실제로 보내지 않습니다. (.env에서 0으로 바꾸고 Gmail 값을 넣으세요)")
            return
        if not alert.configured():
            print("[오류] .env에 GMAIL_ADDRESS, GMAIL_APP_PASSWORD, ALERT_TO를 먼저 넣어 주세요.")
            sys.exit(1)
        alert.send_mail(alert._addrs("ALERT_TO"), "[수급 경보] 시험 메일",
                        "원자재 수급 리스크 조기경보 시스템의 시험 메일입니다. 이 메일이 보이면 알림 설정이 끝난 것입니다.")
        print("시험 메일을 보냈습니다. 받은편지함(또는 스팸함)을 확인하세요.")
        return

    now = datetime.now(KST).replace(tzinfo=None)
    flat = json.loads((DATA / "items.json").read_text(encoding="utf-8"))
    items = [{**i, "info": {f: i.get(f, "") for f in ["주 원산지", "의존도", "리드타임", "대체 공급선", "중요도"]}}
             for i in flat]
    prices = _read("prices.csv", dtype={"price": float})
    notices = _read("notices.csv", dtype=str)
    news = _read("news.csv", dtype=str)

    print("[1/3] 규칙 기반 판단 (오늘)")
    rule_list = risk.assess(items, prices, notices, now)
    for r in rule_list:
        r["rule_level"], r["rule_level_name"], r["rule_reasons"] = r["level"], r["level_name"], list(r["reasons"])
        print(f"  {r['item']}: {r['level_name']} — {' / '.join(r['reasons'])}")
    rule = {r["item"]: r for r in rule_list}

    manual = sys.argv[sys.argv.index("--manual") + 1] if "--manual" in sys.argv else None
    out = None
    if manual:
        print(f"\n[2/3] AI 판단 — 지정한 수동 판단 파일 {manual}")
        out = load_manual(Path(manual), items, rule, news, notices, now)
    elif ai_judge.available():
        print("\n[2/3] AI 판단 — Claude API")
        try:
            out = ai_judge.judge(items, rule, news, notices, now)
            out["ai_date"], out["source"] = now.strftime("%Y-%m-%d"), "Claude API"
        except Exception as exc:
            print(f"  [실패] {exc}")
    if out is None and not manual:
        f = latest_judgment_file()
        if f:
            print(f"\n[2/3] AI 판단 — 가장 최근 수동 판단 파일 {f.name}")
            out = load_manual(f, items, rule, news, notices, now)
        else:
            print("\n[2/3] AI 판단 — 없음 (규칙 기반만 사용)")

    if out:
        ai_date = out["ai_date"]
        for a in out["items"]:
            r = rule.get(a["item"])
            if not r:
                continue
            ai_lv = LEVELS.index(a["level"])
            r.update(ai_level=ai_lv, ai_level_name=a["level"], reasons=[a["reason"]], action=a["action"],
                     lead_time_ai=a["lead_time_estimate"], lead_time_basis=a["lead_time_basis"],
                     method=f"AI ({out['model']})", ai_date=ai_date, ai_source=out["source"])
            # 화면 단계 = AI 판단과 오늘 규칙 판단 중 높은 단계
            if r["rule_level"] > ai_lv:
                r["level"], r["level_name"] = r["rule_level"], r["rule_level_name"]
                r["raised_by_rule"] = True
                r["reasons"] = r["reasons"] + [f"[규칙으로 상향 {a['level']}→{r['rule_level_name']}] "
                                               + " / ".join(r["rule_reasons"])]
                r["action"] = risk.ACTIONS[r["level_name"]] + f" (AI 판단 {ai_date} 이후 새 신호)"
            else:
                r["level"], r["level_name"] = ai_lv, a["level"]
            print(f"  {r['item']}: {r['level_name']} (AI {a['level']} · 규칙 {r['rule_level_name']})")

        briefing = {"date": ai_date, "lines": out["briefing"][:3], "model": out["model"], "source": out["source"]}
        (DATA / "briefing.json").write_text(json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8")
        new = pd.DataFrame([{"item": a["item"], "link": a["link"], "level": a["level"], "reason": a["reason"],
                             "judged_at": ai_date} for a in out["articles"]])
        old = _read("issues_ai.csv", dtype=str)
        pd.concat([old, new], ignore_index=True).drop_duplicates(["item", "link"], keep="last") \
            .to_csv(DATA / "issues_ai.csv", index=False, encoding="utf-8-sig")
        print(f"  AI 판단 기준일 {ai_date} · 기사별 판단 {len(new)}건")

    print("\n[3/3] 메일 알림 ('경계' 이상, 단계가 올라갔을 때만)")
    result = alert.notify(rule_list, DATA / "alerts.csv", DATA / "alert_state.json")
    for r in result:
        r["assessed_at"] = now.strftime("%Y-%m-%d %H:%M")
    risk.save(result, DATA / "risk.json")
    print("\n완료")


if __name__ == "__main__":
    main()
