# UI 와이어프레임 (U-1)

README 9.4 화면별 설계를 화면 하나당 HTML 한 장으로 옮긴 와이어프레임입니다. 브라우저로 열면 됩니다(외부 파일 없음, 공통 스타일 `wireframe.css`).
일부러 무채색으로 그렸습니다. 레이아웃과 위계만 정하고, 색·글꼴은 디자인 토큰(`webapp/static/css/tokens.css`, 9.6)이 정합니다. 빨간 번호(①②…)는 오른쪽 주석과 짝입니다.

| ID | 화면 | 경로 | 소속 | 와이어프레임 | 상태(9.5) |
| --- | --- | --- | --- | --- | --- |
| S1 | 장면 검색 | `/` | 공동 | [S1-search.html](S1-search.html) — 1440 기준 + 1024 서랍 · 390 변형 | 초기·로딩·빈 결과(완화 제안)·오류·부분 성공 |
| S2 | 장면 상세 | `/scenes/<frame_id>` | 공동 | [S2-scene.html](S2-scene.html) — 전체 폭, 어두운 시각화 영역 + 390 세로 배치 | 로딩(BEV 진행 표시줄)·부분 성공(LiDAR 없음) 등 |
| S3 | 검수 세트 | `/review-sets` | DB 과목 | [S3-review-sets.html](S3-review-sets.html) — 세트 목록 + 판정 패널 | 빈 결과·NR-4 오류·잠김 |
| S4 | 분석 대시보드 | `/dashboard` | DB 과목 | [S4-dashboard.html](S4-dashboard.html) — 질문 1–4 카드 | 카드 단위 로딩·오류 |
| S5 | 처리 결과 | `/pipeline` | 빅데이터 과목 | [S5-pipeline.html](S5-pipeline.html) — 탭 5개, 한 줄 결론 → 근거 → 원본 파일 | 결과 파일 없음·해석 실패 |
| S6 | 정보 | `/about` | 공동 | [S6-about.html](S6-about.html) — 용어 정의(F-7·F-8·F-9)·출처·라이선스 | 기준값 산출 전 |

공통 레이아웃(모든 화면): 56 px 헤더(앱 이름, 주 메뉴 5개, 검수자 선택, 단축키, 라이트/다크), 바닥글 “KITTI Vision Benchmark Suite · CC BY-NC-SA 3.0”, 본문 최대 폭 1440 px(S2만 전체 폭).

## 구현과의 대응

| 항목 | 위치 |
| --- | --- |
| 디자인 토큰 (색·간격·글자·반경, 라이트/다크, 클래스 색) | `webapp/static/css/tokens.css` |
| 화면 스타일 (토큰만 사용, 색상 값 직접 사용 0건) | `webapp/static/css/app.css` |
| 공통 컴포넌트 (Jinja2 매크로) | `webapp/templates/macros/*.html` |
| 컴포넌트 갤러리 (두 테마 나란히, debug 모드에서만) | `/_components` |
| 문구표 | `webapp/i18n/ko.py` (README 9.10의 `app/i18n/ko.py`에 해당) |
| 화면 캡처 (1440 · 1024 · 390, 라이트·다크) | [screenshots/](screenshots/) |

S3·S4는 데이터베이스 과목 프로젝트에서 구현합니다. 이 저장소에서는 메뉴 항목과 안내 화면만 제공합니다.

## 와이어프레임과 현재 구현의 차이

- S2 객체 표의 ‘필터 [전체 ▾]’는 아직 넣지 않았습니다. DB 과목 NR-1(화면 JS에서 행을 거르지 않음)에 맞추려면 서버 쿼리 매개변수로 구현해야 하므로, 데이터 연결 단계에서 추가합니다.
- S1의 결과 갱신은 지금 HTML 전체를 받아 결과 영역만 바꿉니다. 결과 조각만 돌려주는 엔드포인트는 MySQL 연결 단계(U-4)에서 붙입니다.
