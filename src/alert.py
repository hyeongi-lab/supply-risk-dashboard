"""'경계' 이상 품목 메일 알림 (Gmail) + 알림 이력 data/alerts.csv

- 경계: ALERT_TO(담당자)에게
- 심각: ALERT_TO + ALERT_TO_TEAM(팀 전체)에게
- 단계가 '올라갔을 때만' 보냅니다. 같은 단계가 유지되는 동안은 다시 보내지 않음 (data/alert_state.json에 지난 단계 기록)
- 알림 이력에는 받는 사람 '주소'를 남기지 않습니다. (웹 화면에 공개되므로 '담당자'/'팀 전체'로만 기록)

필요한 값 (.env 또는 GitHub Secrets): GMAIL_ADDRESS, GMAIL_APP_PASSWORD, ALERT_TO, ALERT_TO_TEAM(선택)
"""

import json
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd

KST = timezone(timedelta(hours=9))
ALERT_COLUMNS = ["sent_at", "item", "level", "recipients", "status", "subject"]
LEVELS = ["관심", "주의", "경계", "심각"]


def _addrs(name: str) -> list[str]:
    return [a.strip() for a in os.environ.get(name, "").split(",") if a.strip()]


def simulate() -> bool:
    """MAIL_SIMULATE=1 이면 실제로 보내지 않고 '모의 발송'으로만 기록 (Gmail 연결 전 시험용)"""
    return os.environ.get("MAIL_SIMULATE", "").strip().lower() in ("1", "true", "yes")


def configured() -> bool:
    return simulate() and bool(_addrs("ALERT_TO")) or bool(
        os.environ.get("GMAIL_ADDRESS") and os.environ.get("GMAIL_APP_PASSWORD") and _addrs("ALERT_TO"))


def send_mail(to: list[str], subject: str, body: str) -> None:
    sender = os.environ["GMAIL_ADDRESS"].strip()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(to)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(sender, os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
        s.sendmail(sender, to, msg.as_string())


def _body(r: dict, dashboard_url: str) -> str:
    lines = [
        f"[원자재 수급 리스크 조기경보] {r['item']} — {r['level_name']}",
        "",
        f"판단 이유: {' / '.join(r.get('reasons', []))}",
        f"권장 조치: {r.get('action', '')}",
    ]
    if r.get("lead_time_ai"):
        lines.append(f"예상 리드타임: {r['lead_time_ai']}")
    ps = r.get("price") or {}
    if ps:
        ch = ps.get("change_1w")
        lines.append(f"시장 가격: {ps['series']} {ps['price']:,.0f} {ps['unit']} (1주 {'-' if ch is None else f'{ch:+.1%}'})")
    lines += ["", f"판단 방식: {r.get('method', '')} · 판단 시각: {r.get('assessed_at', '')} (한국 시간)"]
    if dashboard_url:
        lines.append(f"대시보드: {dashboard_url}")
    return "\n".join(lines)


def notify(risks: list[dict], path: str | Path = "data/alerts.csv",
           state_path: str | Path = "data/alert_state.json", log=print) -> list[dict]:
    """'경계' 이상이면서 지난 실행보다 단계가 올라간 품목에만 메일을 보내고 이력을 남깁니다.
    같은 단계가 유지되는 동안은 다시 보내지 않습니다. (내려갔다가 다시 올라가면 다시 보냄)
    각 품목 dict에 mail_status를 채웁니다."""
    path, state_path = Path(path), Path(state_path)
    hist = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig") \
        if path.exists() else pd.DataFrame(columns=ALERT_COLUMNS)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    now = datetime.now(KST)
    dashboard_url = os.environ.get("DASHBOARD_URL", "")
    new_rows = []

    for r in risks:
        prev = state.get(r["item"])
        past = hist[hist["item"] == r["item"]]
        if prev is None:  # 처음 실행: 이전 알림 이력의 단계를 기준으로 (이미 보낸 알림을 다시 보내지 않도록)
            prev = LEVELS.index(past["level"].iloc[-1]) if len(past) else 0
        state[r["item"]] = r["level"]
        if r["level"] < 2:
            continue
        if r["level"] <= prev:
            last = past.iloc[-1] if len(past) else None
            r["mail_status"] = (f"{last['status']} · {last['sent_at'][5:16]} (같은 단계 유지 중이라 다시 보내지 않음)"
                                if last is not None else "단계 유지 중 (새로 올라갈 때만 발송)")
            continue

        to = _addrs("ALERT_TO") + (_addrs("ALERT_TO_TEAM") if r["level"] >= 3 else [])
        who = "담당자 + 팀 전체" if r["level"] >= 3 and _addrs("ALERT_TO_TEAM") else "담당자"
        subject = f"[수급 경보·{r['level_name']}] {r['item']} — {r.get('action', '')}"[:120]
        if not configured():
            status = "미발송 (메일 설정 없음)"
        elif simulate():
            status = "모의 발송"
            log(f"  [모의] 받는 사람 {len(to)}명 · 제목: {subject}")
        else:
            try:
                send_mail(to, subject, _body(r, dashboard_url))
                status = "발송"
            except Exception as exc:
                status = f"실패: {type(exc).__name__}"
                log(f"  메일 실패 ({r['item']}): {exc}")
        r["mail_status"] = (f"{status} · {who} · {now:%m-%d %H:%M}" if status in ("발송", "모의 발송") else status) \
            + (" (Gmail 연결 전이라 실제로는 보내지 않음)" if status == "모의 발송" else "")
        log(f"  {r['item']} ({r['level_name']}): {r['mail_status']}")
        if not configured():
            continue  # 메일 설정 전에는 이력에 남기지 않음
        new_rows.append({"sent_at": now.strftime("%Y-%m-%d %H:%M"), "item": r["item"], "level": r["level_name"],
                         "recipients": who, "status": status, "subject": subject})

    if new_rows:
        hist = pd.concat([hist, pd.DataFrame(new_rows)], ignore_index=True)
        hist[ALERT_COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return risks
