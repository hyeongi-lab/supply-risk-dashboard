"""매일 수집 실행 파일 (GitHub Actions도 이 파일을 실행)

실행:  .venv\\Scripts\\python collect.py

1) 구글 시트(읽지 못하면 watchlist.csv)에서 감시 품목(사용여부=Y)을 읽고  → data/items.json
2) 품목 키워드 + 위험 키워드로 Google News 검색                         → data/news.csv
3) KOMIS 시장 가격 (텅스텐 APT, 알루미늄 LME)                           → data/prices.csv
4) 정부 수출통제 공고 (미국 BIS, 일본 METI, 중국 상무부)                 → data/notices.csv
5) 출처별 성공·실패 기록                                                 → data/last_run.json

위험 단계 판단은 하지 않습니다 (analyze.py가 함. 수동 AI 판단 결과를 덮어쓰지 않기 위해).
한 부분이 실패해도 나머지는 계속 진행하고, 마지막에 실패한 부분을 알려줍니다.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import news, notices, prices
from src.watchlist import load_items

# 콘솔이 표시하지 못하는 글자가 있어도 멈추지 않도록
sys.stdout.reconfigure(errors="replace")

BASE = Path(__file__).parent
DATA = BASE / "data"
KST = timezone(timedelta(hours=9))


def main() -> None:
    failed = []
    try:
        items = load_items()
    except Exception as exc:  # 품목 목록이 없으면 아무것도 할 수 없으므로 멈춤
        print(f"\n[오류] {exc}")
        sys.exit(1)
    print(f"감시 품목 {len(items)}개: {', '.join(i['item'] for i in items)}")

    DATA.mkdir(exist_ok=True)
    card_items = [{"item": i["item"], "category": i["category"], "price_indicator": i["price_indicator"],
                   **i["info"]} for i in items]
    (DATA / "items.json").write_text(json.dumps(card_items, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: dict[str, list[str]] = {"뉴스": [], "가격": [], "공고": []}

    def logger(part: str):
        def _log(msg: str) -> None:
            print(msg)
            lines[part].append(msg.strip())
        return _log

    print("\n[1/3] 뉴스 검색")
    try:
        new = news.collect(items, log=logger("뉴스"))
        res = news.merge_and_save(new, items, DATA / "news.csv")
        removed = ", ".join(f"{k} {v}건" for k, v in res["removed"].items())
        logger("뉴스")(f"  → 새로 추가 {res['added']}건 (전체 {res['total']}건) · 제목으로 걸러냄: {removed}")
    except Exception as exc:
        failed.append("뉴스"); logger("뉴스")(f"  [실패] {exc}")

    print("\n[2/3] 시장 가격 (KOMIS)")
    try:
        total = prices.merge_and_save(prices.collect_prices(items, log=logger("가격")), DATA / "prices.csv")
        logger("가격")(f"  → 저장 {total}행")
    except Exception as exc:
        failed.append("가격"); logger("가격")(f"  [실패] {exc}")

    print("\n[3/3] 정부 수출통제 공고")
    try:
        total, related = notices.merge_and_save(notices.collect_notices(items, log=logger("공고")), items,
                                                DATA / "notices.csv")
        logger("공고")(f"  → 전체 {total}건 (당사 품목 관련 {related}건)")
    except Exception as exc:
        failed.append("공고"); logger("공고")(f"  [실패] {exc}")

    # 부분 실패(예: 해외 서버에서 KOMIS 차단)도 찾아서 기록
    problems = [l for ls in lines.values() for l in ls if "실패" in l]
    for part, ls in lines.items():
        if any("실패" in l for l in ls) and part not in failed:
            failed.append(part)
    status = {"run_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "ok": not failed, "failed_parts": failed,
              "problems": problems[:30],
              "summary": {part: [l for l in ls if l.startswith(("→", "[")) or "→" in l][-6:] for part, ls in lines.items()}}
    (DATA / "last_run.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    # 위험 단계 판단은 analyze.py가 함 (여기서 risk.json을 덮어쓰지 않음 → 수동 AI 판단 보존)
    print("\n수집 완료" + (f" (실패한 부분: {', '.join(failed)} → data/last_run.json 참고)" if failed else "")
          + "\n다음: analyze.py 로 위험 판단")


if __name__ == "__main__":
    main()
