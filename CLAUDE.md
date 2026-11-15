# 원자재 수급 리스크 조기경보 대시보드

## 작업 방식
- 사용자는 구매팀 담당자이고 개발 경험이 거의 없다. 단계별로 쉽게 설명하고, 실행 명령어는 Windows 기준으로 정확히 알려준다.
- 큰 작업은 먼저 짧은 계획을 보여주고 확인받은 뒤 진행한다.
- 오류가 나면 원인을 쉬운 말로 설명하고 고친다.
- data/ 폴더의 CSV·JSON은 통째로 읽지 말고 파이썬으로 개수·요약·상위 몇 건만 확인한다.

## 프로젝트 목적
원자재·장납기 소재의 수급 리스크(수출통제, 무역분쟁, 가격 급등)를 매일 자동으로 감시하는 조기경보 대시보드.
계기: 작년 중국의 텅스텐 수출통제로 공장이 멈춤. 미·중·일 수출통제와 보복 흐름 등 전조가 있었지만 놓쳤다.
배경·목적·위험 판단 기준·일정 등 상세 내용은 `docs/기획서.md` 참고.

## 확정된 구성
- 언어: Python / 웹 화면: Streamlit
- 매일 자동 실행: GitHub Actions (매일 07:00 KST)
- 배포: Streamlit Community Cloud (로그인 없이 회사 PC 크롬에서 접속. 회사 PC에는 아무것도 설치하지 않음)
- AI 판단: Claude API (2단계)
- 데이터 저장: 별도 DB 없이 저장소의 CSV/JSON 파일 (data/ 폴더)
- 알림: '경계' 이상이면 메일 발송 + 웹에 빨간 경고등

## 감시 품목 (구글 시트가 기준)
- 품목 목록의 기준은 구글 시트 "[구매팀] 감시 품목 관리"이다.
  https://docs.google.com/spreadsheets/d/1snMBHS7KlDA7Jghxc73j0ShIz2ZhgyTRZ49vhsQml9s/edit
- 읽는 방법 (2026-11-15 확정): 시트를 '링크가 있는 모든 사용자 - 뷰어'로 공유하고, 서비스 계정 없이 CSV 내보내기 주소로 읽는다.
  `https://docs.google.com/spreadsheets/d/<시트ID>/export?format=csv` (gid 없이 = 첫 번째 탭. 이 시트의 첫 탭은 gid=0이 아님) (코드: `src/watchlist.py`)
  → 구글 인증 파일·구글 관련 Secrets는 필요 없다. 서비스 계정 방식은 쓰지 않기로 했다.
  → 시트를 읽지 못하면 이유를 출력하고 로컬 `watchlist.csv`(예비용)로 대신 읽는다.
- 열: 사용여부(Y/N), 품목명, 구분, 상세 사양, 제조사/공급사, 주 원산지, 의존도, 리드타임, 대체 공급선, 중요도(상/중/하), 검색 키워드(영문), 검색 키워드(중문), 검색 키워드(한글), 가격 지표, 비고
- 키워드는 `;`로 구분. 사용여부=Y인 행만 감시한다.
- 시트 연결 전에는 같은 열 구조의 로컬 `watchlist.csv`로 테스트한다.
- 현재 품목: 텅스텐 분말(원자재, 중국 의존 90%), 알루미늄(원자재), Cr 커넥터 Cristek MDV-1015-6A1-C(부품, 단일 공급사)
- 기획서 5장의 표는 1차 기록용이며, 품목이 바뀌면 시트를 따른다.

## 뉴스 수집 방식 (2026-11-15 확정, 코드: `src/news.py`)
- 품목명만으로 검색하지 않는다. 시트의 품목 키워드 1개씩 + 위험 키워드 묶음으로 검색한다.
  예) `"tungsten powder" ("export control" OR tariff OR shortage ...)` (품목 키워드 여러 개를 한 검색어에 OR로 넣으면 결과가 크게 줄어서 1개씩)
- 위험 키워드는 코드의 `RISK_KEYWORDS`(영·중·한)에 있다. 위험 키워드와 똑같은 시트 키워드(예: `出口管制`)는 품목 키워드로 쓰지 않는다.
- 뜻이 여러 개인 키워드는 바꿔 쓴다: `APT` → `tungsten APT`, `ammonium paratungstate`
- 수집 후 제목으로 거른다: 제목에 품목 키워드가 없으면 제외, 시장 전망 보고서·광고성 기사(Market Forecast/Size/Growth, CAGR 등) 제외,
  일반 PC·전자제품 커넥터 기사(USB, HDMI, Type-C, 노트북 등) 제외. 기존 기록도 매번 같은 기준으로 다시 거른다.
- Cr 커넥터(Cristek MDV-1015-6A1-C, Micro-D 15핀): 시트 키워드를 Cristek·Qnnect(모회사)·micro-D connector·MIL-DTL-83513·defense/aerospace connector로 넓혔지만
  위험 키워드 조합 검색으로는 관련 기사 0건 (2026-11-15).
- **부품 예외 (2026-11-15 사용자 결정)**: 구분이 '부품'인 품목은 한 단어 키워드(제조사명·품번: Cristek, Qnnect, MIL-DTL-83513)를
  위험 키워드 없이 단독 검색한다 (`src/news.py` `is_standalone`). 단일 공급사 부품은 공급사 인수합병·이전이 위험 신호이기 때문.
  여러 단어 키워드(micro-D connector 등)는 계속 위험 키워드와 조합. 결과: 커넥터 기사 6건(모두 Cristek·Qnnect 소유주 변화 기사)
- 기사 유형에 '공급사 변화'(인수·합병·이전·파산) 추가
- 대시보드 '전주 대비'는 수집 시작 7일 후부터 표시한다.

## 위험 판단 4단계 (초안, 기획서 6장)
- 관심: 관련 뉴스가 평소 수준
- 주의: 관련 기사 급증, 또는 가격 1주 10% 이상 상승
- 경계: 주요 생산국의 수출 규제 검토·발표 보도, 무역 보복 조치 발표 → 메일 알림
- 심각: 당사 품목 수출통제 확정·시행 → 팀 전체 알림

## 정보 관리 원칙 (2026-11-15 확정)
- 품목 정보(의존도, 리드타임, 대체 공급선, 제조사/공급사, 중요도)는 민감 자료가 아니다.
  → 대시보드 화면에 표시해도 되고, Claude API(AI 판단)에 보내도 되고, GitHub에 올려도 된다.
  → docs/기획서.md 5장·8장에 적힌 "내부 정보는 시스템에 넣지 않음" 원칙은 이 내용으로 대체된다.
- AI 위험 판단에 품목 정보를 적극 활용한다: 위험도 = 외부 위험(뉴스·공고·가격) × 당사 노출도(의존도, 리드타임, 대체 공급선 유무, 중요도).
- 단, 비밀 값(API 키, 메일 비밀번호)은 절대 코드나 GitHub에 넣지 않는다 (.env / GitHub Secrets / Streamlit Secrets).

## 3단계 구현 내용 (2026-11-15 완료)
- 실행 파일: `collect.py` (뉴스 → 가격 → 공고 → 위험 단계. GitHub Actions도 이 파일 실행). `collect_news.py`는 없어짐
- 가격 `src/prices.py` → `data/prices.csv`: KOMIS 가격 화면의 내부 데이터 주소(공식 API 아님, 하루 1회)
  텅스텐 APT(MNRL0018, 가격기준 845, AsianMetal, USD/mt), 알루미늄 LME CASH(MNRL0009, 가격기준 495, USD/ton).
  품목↔가격 연결은 시트 '가격 지표' 열(APT → 텅스텐 APT, LME+알루미늄 → 알루미늄 LME)
- 공고 `src/notices.py` → `data/notices.csv`: 미국 BIS(Federal Register API), 일본 METI(안전보장무역관리 개정정보 페이지),
  중국 상무부(exportcontrol.mofcom.gov.cn 국내·국제 동향. 본 사이트 aqygzj는 접속 차단). 제목·요약에 품목 키워드 → related_items
- 위험 단계 `src/risk.py` → `data/risk.json` (규칙, 2단계 AI 판단 전까지): 가격 1주 +10% → 주의, 최근 30일 관련 공고 → 경계,
  리드타임 3개월 이상이면 주의 이상일 때 한 단계 상향(최대 심각). 기사 유형(수출통제/관세·무역분쟁/제재/공급 차질/가격)은 제목 단어로 분류
- 4단계 주의: GitHub Actions(미국 서버)에서 KOMIS·중국 사이트가 막힐 수 있음 → 배포 때 확인

## 2단계 구현 내용 (2026-11-15)
- **수집은 사용자가 요청할 때만 (2026-11-15 사용자 요청)**: 평소에는 `collect.py`를 실행하지 않고 data/에 있는 자료로만 작업한다.
  주의: `collect.py`는 마지막에 risk.json을 규칙 기반으로 덮어쓰므로, 수집 뒤에는 그날 판단 파일을 `analyze.py --manual`로 다시 적용한다.
- 실행 파일: `analyze.py` (수집 없이 판단만) → 규칙 판단 → Claude API 판단 → '경계' 이상 메일. `--test-mail`로 시험 메일
- AI `src/ai_judge.py`: 모델 `claude-opus-5-5`, effort medium, 구조화 출력(JSON schema), 거절 시 서버 측 fallback(`fallbacks: "default"`).
  하루 1회 요청 1번으로 전 품목 판단 (입력 약 4천 자, 하루 수십 원 수준). 결과: 품목별 단계·이유·조치·예상 리드타임, 기사별 단계·이유, 브리핑 3줄
  → `data/risk.json`(method "AI"), `data/briefing.json`, `data/issues_ai.csv`. API 키가 없거나 실패하면 규칙 기반 결과 사용
- 메일 `src/alert.py`: Gmail SMTP(앱 비밀번호). 경계 → ALERT_TO, 심각 → + ALERT_TO_TEAM. 같은 품목·단계는 하루 1번.
  `data/alerts.csv`에는 받는 사람 주소를 남기지 않음(웹 공개) — '담당자'/'팀 전체'로만 기록
- 비밀 값: `.env`(gitignore됨, 양식은 `.env.example`) — ANTHROPIC_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, ALERT_TO, ALERT_TO_TEAM, (선택) DASHBOARD_URL
- **현재 운영 상태 (2026-11-15 사용자 결정)**:
  - Claude API는 시스템이 안정화된 뒤 연결한다. 그 전에는 대화 중 Claude가 같은 양식(JSON)으로 판단을 써서
    `data/judgments/YYYY-MM-DD.json`에 기록하고 `analyze.py --manual <파일>`로 적용한다. (method에 "Claude 대화 판단·수동"으로 표시)
  - Gmail은 "연결했다 치고" 진행: `.env`의 `MAIL_SIMULATE=1` → 실제로 보내지 않고 alerts.csv에 '모의 발송'으로 기록.
    받는 주소(ALERT_TO)는 `.env`에만 있다 (CLAUDE.md·data/에는 주소를 쓰지 않는다)
  - 2026-11-15 판단: 텅스텐 분말 경계, 알루미늄 주의, Cr 커넥터 관심 (규칙 기반은 셋 다 관심)
    기록: `data/judgments/2026-11-15.json`(3차: 부품 제조사명 단독 검색 반영 — 커넥터에 11/4 Qnnect 추가 인수 기사 반영, 단계는 관심 유지),
    `2026-11-15_1차.json`(1차). 2차(커넥터 키워드 확대)는 3차로 덮어씀

## 목표 대시보드 화면 (2026-11-15 확정, 2·3단계를 진행하며 이 구성으로 맞춘다)
위에서부터 순서대로:
1. **경고 배너**: '경계' 이상 품목이 있으면 맨 위에 빨간 배너 (품목, 이유, 권장 조치, 메일 발송 여부)
2. **AI "오늘의 구매 브리핑"** 3줄 + 위험 단계별(관심/주의/경계/심각) 품목 수
3. **품목 카드**: 위험 단계 신호등, 주 원산지·의존도, 리드타임(시트 값 → AI 예상), 시장 가격·1주 변동,
   대체 공급선, 가격 미니 차트, AI 판단 이유, 권장 조치, 최근 7일 기사 수. '경계' 이상 카드는 테두리 강조
4. **가격 추이 차트**: 최근 12주, 첫 주=100 지수, 텅스텐 APT와 알루미늄 LME
5. **위험 이슈 표**: 날짜, 단계, 품목, 유형, 제목, AI 판단 이유, 원문 링크 (단계·품목 필터)
6. **정부 수출통제 공고 목록 + 알림 이력**
- 단계 색: 관심 초록, 주의 노랑, 경계 주황, 심각 빨강
- 필요한 데이터: 1·2·3(신호등·판단 이유·권장 조치·AI 예상 리드타임)·5 → 2단계(Claude API, 메일),
  3(가격·미니 차트)·4·6(공고) → 3단계(KOMIS 가격, BIS·METI·상무부 공고), 6(알림 이력) → 2단계 메일 발송 기록

## 단계별 계획
1. 폴더 구조, 품목 목록 읽기(시트/CSV), Google News RSS 수집 → data/news.csv, Streamlit 첫 화면, 실행방법.md
2. Claude API 위험 판단(관심/주의/경계/심각), 메일 알림, 빨간 경고등
3. 가격 수집(KOMIS, LME), 정부 공고 수집(중국 상무부, 미국 BIS 등)
4. GitHub Actions 자동 실행 + Streamlit Cloud 배포
5. 2024~2025년 텅스텐 사례로 경보 재현 테스트

## 진행 상황 (2026-11-15 기준)
| 단계 | 상태 |
| --- | --- |
| 1단계 | ✅ 완료. 구글 시트(링크 공유 CSV) 연결, 뉴스 316건(커넥터 6건 포함), 대시보드 목표 화면 6개 구역 |
| 3단계 | ✅ 완료. KOMIS 가격(텅스텐 APT 698일치, 알루미늄 LME 727일치), 정부 공고 105건(관련 4건), 규칙 기반 단계 |
| 2단계 | 🟡 코드 완료, 외부 연결 전. Claude API 키 없음 → 대화 중 수동 판단으로 운영. Gmail 미연결 → 모의 발송 |
| 4단계 | ⬜ 다음 할 일 |
| 5단계 | ⬜ 4단계 후 |
- 로컬 환경: 정식 Python 3.13(`py -3.13`)으로 만든 `.venv` 사용 (`python` 명령은 MSYS2 파이썬이라 쓰지 않음). Git 2.56 설치됨, 저장소 `git init`만 됨(커밋 없음, git 사용자 이름·이메일 미설정)
- 실행: 수집 `collect.py`(요청 시만), 판단 `analyze.py [--manual 파일]`, 화면 `streamlit run app.py` (bat: run_collect.bat, run_dashboard.bat)

## 다음 할 일: 4단계 배포 (화면 기준으로 하나씩 안내)
1. 올리기 전 점검: `git status`로 올라갈 파일 확인. `.env`, `service_account*.json`, 루트의 `*.json`이 빠졌는지 확인.
   `docs/`·`watchlist.csv`는 사용자가 .gitignore에서 뺐으므로 올라감(저장소는 반드시 Private). `.claude/`는 제외 목록에 추가할 것
2. 사용자: GitHub 가입 → Private 저장소 생성. git 작성자 이메일은 GitHub noreply 주소 권장. 첫 push 때 GitHub 로그인 창은 사용자가 직접 로그인
3. GitHub Actions(`.github/workflows/daily.yml`): 매일 07:00 KST = cron `0 22 * * *`(UTC) → `collect.py` → `analyze.py` → data/ 변경분 커밋·푸시
   - API 키가 없으면 analyze.py는 규칙 기반으로 동작(수동 판단은 로컬에서만)
   - 미국 서버에서 KOMIS·중국 상무부 정보망이 막힐 수 있음 → 첫 실행 로그로 확인, 막히면 대안 검토
4. GitHub Secrets: ANTHROPIC_API_KEY(나중에), GMAIL_ADDRESS, GMAIL_APP_PASSWORD, ALERT_TO, ALERT_TO_TEAM, MAIL_SIMULATE. 구글 관련 Secret은 필요 없음
5. Streamlit Community Cloud(share.streamlit.io, GitHub 로그인) → 저장소의 app.py 배포 → 웹 주소 확보 → 회사 PC 크롬에서 접속 확인
   - 웹은 주소를 아는 누구나 볼 수 있음(품목 정보 포함, 원칙상 허용). 필요하면 간단한 접속 비밀번호 검토
6. 배포 주소를 `.env`/Secrets의 DASHBOARD_URL에 넣어 알림 메일에 링크 포함
