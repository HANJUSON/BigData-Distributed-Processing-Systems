# 결과 요약 (보고서용)

README의 요구사항 D-1 ~ D-4, F-1 ~ F-14, N-1 ~ N-4, U-1 ~ U-13에 대한 달성 상태와 핵심 수치입니다. 모든 수치는 괄호 안의 결과 파일에서 가져왔습니다. 경로는 프로젝트 폴더(`finalproject/`) 기준입니다. 실행 방법은 [RUNBOOK.md](RUNBOOK.md), 측정 환경은 [ENVIRONMENT.md](ENVIRONMENT.md)를 보세요.

상태 표기: **완료** = README 완료 기준 충족 · **부분** = 일부만 충족하거나 증거가 일부만 있음 · **사용자 몫** = 사람이 직접 해야 하는 작업이 남음

## 1. 요구사항별 상태

| ID | 상태 | 근거 파일 | 핵심 수치 |
| --- | --- | --- | --- |
| D-1 | 완료 | `results/quality_report.md` | 학습 장면 7,481개 처리, 시험 장면 제외 |
| D-2 | 완료 | `results/quality_report.md` 1절 | image_2·velodyne·label_2·calib 각 7,481개, 누락 0 |
| D-3 | 완료 | `results/quality_report.md` 4절 | 실제 처리 용량 20,507,768,620 B(19.10 GiB), 총 포인트 891,919,836개 |
| D-4 | 완료 | `results/quality_report.md` 머리말, `results/analysis_summary.json` `source`, `.gitignore` | 출처·CC BY-NC-SA 3.0 표기, 원본·Parquet은 저장소 밖(`~/data/kitti`) |
| F-1 | 완료 | `results/quality_report.md` 1절 | 7,481개 중 7,481개 연결, 미연결 0, 중복 0 |
| F-2 | 완료 | `results/quality_report.md` 2–3절 | DontCare 11,295(별도), 일반 객체 범위 오류 0, 3D 무효 8, 비정상 포인트 0 |
| F-3 | 완료 | `kitti/schema.py`, `results/quality_report.md` 5절 | frames 7,481 · objects 51,865 · points 891,919,836행 |
| F-4 | 완료 | `results/quality_report.md` 6절, `~/data/kitti/work/ingest.log` | 32개 프레임 구간으로 나눠 처리, 전체 변환 33.59 s에 정상 종료 |
| F-5 | 완료 | `results/f5_dataframe_vs_sql.json` | 질의 2개(클래스별·장면별), 행 수 같음, 양방향 `exceptAll` 0행 |
| F-6 | 완료 | `results/grid_params.json`, `results/analysis_summary.json`, `results/figures/f6_*.png` | 1 m 격자 80×80, 장면별 격자 9,746,109행, 누적 히트맵·표본 장면 3개 그림 |
| F-7 | 완료 | `results/f7_*.csv`, `results/figures/f7_*.png` | 클래스×거리 31행, ×가림 32행, ×잘림 40행, 난이도 8행 |
| F-8 | 완료 | `results/figures/f8_visual_check.png`, `results/verification.md` | 객체 51,865행에 `points_in_box`, 3D 무효 8개는 NULL, 표본 3장면 시각 확인 |
| F-9 | 완료 | `results/f9_thresholds.csv`, `results/mysql_consistency.md` | 8개 클래스 기준값, 관측 부족 객체 4,298개·장면 2,729개, MySQL 기준값 8/8 일치 |
| F-10 | 완료 | `results/mysql_consistency.md`, `results/ui_qa.md`, `scripts/smoke_webapp.py` | MySQL 적재 62/62 일치, S1 검색 → S2 이미지·BEV·객체 표 동작 |
| F-11 | 완료 | `results/verification.md`, `results/subsets/sample_{100,1000}/verification.md` | 3개 규모 × 13항목 = 39항목 모두 통과, 값 불일치 0 |
| F-12 | 완료 | `results/bench/summary.csv`, `bench_report.md` | 4개 방식 × 3개 규모 표, 파티션 32 고정 |
| F-13 | 완료 | `results/bench/bench_raw.csv`, `results/bench/mem/*.csv` | 조건별 3회, 중앙값·처리량·peak RSS, 메모리 시계열 36 + 예열 3개 |
| F-14 | 완료 | `results/bench/figures/*.png`, 이 문서 5절 | 7,481장면 `local[8]` 순차 대비 1.93×, 단계별 시간 분해 |
| N-1 | 완료 | `requirements.txt`, `samples/`, `results/bench/environment.json`, `docs/ENVIRONMENT.md` | 버전 고정·표본 고정 완료. 데이터는 2026-11-04~05 공식 배포본(`s3.eu-central-1.amazonaws.com/avg-kitti`) 다운로드, 파일 수·크기로 사본 식별 |
| N-2 | 완료 | `docs/RUNBOOK.md` | 데이터 배치 → 변환 → 분석 → 검증 → 적재 → 화면 → 측정 순서 |
| N-3 | 완료 | `docs/ENVIRONMENT.md`, `results/bench/environment.json` | CPU·코어·RAM·저장장치·OS·Spark·Java 버전, 측정 조건 |
| N-4 | 완료 | `results/bench/n4_judgment.json` | 36/36 정상 종료, 대조 36/36 통과, 최대 peak RSS 6,817.7 MiB ≤ 예산 24,604.6 MiB |
| U-1 | 완료 | `docs/ui/S1-search.html` ~ `S6-about.html` | 와이어프레임 6장 |
| U-2 | 완료 | `webapp/static/css/tokens.css`, `webapp/templates/macros/` | 화면 코드의 색상 값 직접 사용 0건(아래 6절) |
| U-3 | 완료 | `results/u3_thumbnails.json` | 7,481개 생성, 누락·실패 0, 장당 최대 13,194 B |
| U-4 | 부분 | `results/ui_qa.md`, `scripts/smoke_webapp.py` | 종류·거리·가림·포인트 수·관측 부족·칩·정렬·URL 동기화 동작, 결과 = `CALL` 결과. 미검수만·태그·프리셋은 DB 과목 테이블이 없어 비활성 |
| U-5 | 부분 | `results/ui_qa.md` 키보드 T1 표, `webapp/static/js/scene.js` | 표 행 선택 → 이미지 박스 강조를 자동 확인. 이미지·BEV에서 시작하는 강조는 기록 없음 |
| U-6 | 완료 | `results/figures/f8_visual_check.png`, `docs/ui/screenshots/qa/bev-{0,1000,5000}-after-zoompan.png` | 표본 5장면(0·311·1000·5000·6801)에서 BEV 박스 안 점을 다시 세어 Parquet `points_in_box`와 전부 일치 |
| U-7 | 완료 | `results/ui_qa.md`, `scripts/smoke_webapp.py` | S5 탭 5개 표시, 한 줄 결론 = 결과 파일 값(`results/smoke_webapp.txt`: 102개 PASS, 실패 0건) |
| U-8 | 완료 | `results/ui_qa.md` U-8 표 | 로딩·빈 결과·오류·부분 성공 9/9 재현 |
| U-9 | 완료 | `results/ui_qa.md` | Lighthouse 접근성 S1·S2·S5 데스크톱·모바일 모두 100, axe 위반 0, 키보드만으로 T1 14/14 단계 |
| U-10 | 완료 | `results/ui_qa.md` U-10 표 | S1·S2·S5 × 1440·1024·768·390 px 가로 스크롤·헤더 겹침 0 |
| U-11 | 완료 | `results/ui_qa.md` 요약 | 검색 p95 463 ms, BEV 첫 그리기 최대 190 ms, 확대·이동 54.2 fps 이상, CSS·JS 최대 294.3 KB |
| U-12 | 완료 | `results/ui_qa.md` U-12 표 | 라이트·다크 토큰 대비 미달 0쌍 |
| U-13 | 사용자 몫 | `docs/USABILITY_TEST.md` | 참여자 3–5명 필요. 절차·기록 양식만 준비 |

## 2. 데이터 규모 (D-3, F-3)

| 항목 | 값 | 근거 |
| --- | ---: | --- |
| 학습 장면 | 7,481 | `results/quality_report.md` |
| 총 LiDAR 포인트 | 891,919,836 (장면당 평균 약 119,224) | 같은 파일 4절 |
| 원본 처리 용량 (4종 합계) | 20,507,768,620 B (19.10 GiB) | 같은 파일 4절 |
| └ image_2 / velodyne | 5.79 GiB / 13.29 GiB | 같은 파일 4절 |
| 객체 행 (DontCare 포함) | 51,865 | 같은 파일 2절 |
| 일반 객체 (DontCare 제외) | 40,570 | `results/analysis_summary.json` |
| `points` Parquet | 891,919,836행, 32파일, 8.52 GiB (zstd) | `results/quality_report.md` 5절 |
| 장면별 격자 `grid_stats` | 9,746,109행 | `results/analysis_summary.json` |
| 관심 영역(x 0–80 m, y −40–40 m) 안 포인트 | 451,387,002 | `results/analysis_summary.json` `f6` |

장면당 평균 포인트 수는 891,919,836 / 7,481로 계산했습니다. 공식 배포 용량(이미지 약 12 GB, LiDAR 약 29 GB)은 시험 장면을 포함한 값이라 실제 처리 용량과 다릅니다.

## 3. 데이터 품질 (F-1, F-2)

| 검사 | 건수 | 근거 |
| --- | ---: | --- |
| 4종 파일이 모두 연결된 장면 | 7,481 / 7,481 | `results/quality_report.md` 1절 |
| 중복 장면 / 인식하지 못한 파일 | 0 / 0 | 같은 파일 1절 |
| DontCare (오류와 별도) | 11,295 | 같은 파일 2절 |
| 범위 오류가 있는 일반 객체 | 0 | 같은 파일 2절 |
| 3D 박스 무효 일반 객체 (`points_in_box` = NULL) | 8 | 같은 파일 2절, `mysql_consistency.md` |
| 라벨·보정·PNG 헤더 파싱 오류 | 0 / 0 / 0 | 같은 파일 2절 |
| 16바이트 배수가 아닌 `.bin` / 비정상 포인트 | 0 / 0 | 같은 파일 3절 |

3D 무효 8개는 클래스별 유효 3D 수에서 나옵니다: Car 1, Van 2, Truck 3, Tram 1, Misc 1(`results/f5_dataframe_vs_sql.json`의 `n_objects` − `n_valid_3d`).

## 4. 분석 결과 (F-6, F-7, F-8, F-9)

### F-6 공간 격자

- 좌표계 Velodyne(x 전방, y 좌측, 원점 = 센서), 격자 1.0 m, 관심 영역 x ∈ [0, 80), y ∈ [−40, 40) → 80 × 80 격자. 평균은 빈 격자를 0으로 넣고 정상 처리 프레임 7,481로 나눔(`results/grid_params.json`).
- 관심 영역 안 포인트의 91.38%가 전방 20 m 이내(x < 20 m)에 있습니다. 평균 포인트가 가장 많은 격자는 (`grid_x` 0, `grid_y` 44), 즉 x 0–1 m · y 4–5 m 칸으로 장면당 743.6개입니다(`results/analysis_summary.json` `f6`).
- 그림: `results/figures/f6_grid_cumulative.png`(전체 누적, 로그 척도), `f6_sample_scenes.png`(장면 0·1000·5000).

### F-7 조건별 분포

20 m 이내 객체 비율(`results/analysis_summary.json` `f7.share_within_20m_pct`): Person_sitting 90.99%, Pedestrian 65.43%, Cyclist 39.46%, Misc 35.77%, Car 33.63%, Van 24.85%, Tram 12.92%, Truck 11.06%.

난이도 적격 수(`results/f7_class_difficulty.csv`, 등급은 누적):

| 클래스 | 객체 | Easy | Moderate | Hard | 적격 아님 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Car | 28,742 | 5,971 | 15,710 | 21,716 | 7,026 |
| Van | 2,914 | 378 | 1,298 | 1,973 | 941 |
| Truck | 1,094 | 253 | 722 | 832 | 262 |
| Pedestrian | 4,487 | 2,310 | 3,569 | 4,276 | 211 |
| Person_sitting | 222 | 39 | 109 | 189 | 33 |
| Cyclist | 1,627 | 654 | 1,098 | 1,194 | 433 |
| Tram | 511 | 139 | 277 | 404 | 107 |
| Misc | 973 | 255 | 589 | 767 | 206 |

### F-8 박스 내부 포인트 수

- 유효 3D 객체 40,562개에 계산, 3D 무효 8개는 NULL(`results/mysql_consistency.md` `object_feature.points_in_box` NULL 8).
- 시각 확인: `results/figures/f8_visual_check.png`(장면 0·311·1000·5000·6801, 다시 센 값 = Parquet 값).
- 독립 점검: `scripts/check_box_independent.py`(devkit 방식 꼭짓점 + 면 반공간 판정, `kitti.geometry` 미사용)로 표본 100장면 전체를 다시 세면 유효 3D 박스 567개 불일치 0건입니다(`results/f8_independent_check.json`).
- 범위 주의: F-11 대조에서 `points_in_box`·`dist_m`·난이도 열은 양쪽이 같은 순수 함수로 계산하므로 Spark 처리 경로를 확인합니다. 기하 공식 자체의 독립 증거는 위 독립 점검·시각 확인과 MySQL 생성 열 대조(모순 0건)입니다.

### F-9 관측 부족 기준값

기준값 = 클래스별 유효 3D 객체의 `points_in_box`를 오름차순 정렬한 `ceil(n/10)`번째 값(= MySQL `NTILE(10)` 1번 구간 최댓값). `points_in_box <= 기준값`이면 관측 부족입니다.

| 클래스 | 유효 3D n | k = ceil(n/10) | 기준값(포인트) | 관측 부족 객체 |
| --- | ---: | ---: | ---: | ---: |
| Car | 28,741 | 2,875 | 11 | 3,093 |
| Van | 2,912 | 292 | 20 | 298 |
| Truck | 1,091 | 110 | 49 | 112 |
| Pedestrian | 4,487 | 449 | 19 | 451 |
| Person_sitting | 222 | 23 | 37 | 23 |
| Cyclist | 1,627 | 163 | 14 | 164 |
| Tram | 510 | 51 | 3 | 55 |
| Misc | 972 | 98 | 25 | 102 |
| **합계** | | | | **4,298** (장면 2,729) |

근거: `results/f9_thresholds.csv`, `results/analysis_summary.json` `f9`. 관측 부족 객체 수가 k보다 많은 것은 기준값과 같은 포인트 수를 가진 객체가 여럿이기 때문입니다(`<=` 비교). 같은 기준값 8개가 MySQL `analysis_threshold`에도 그대로 들어갔습니다(`results/mysql_consistency.md` 클래스별 기준값 8/8 일치).

## 5. 정확성 검증 (F-11)

허용 오차: 정수·키·NULL 위치는 완전 일치, 실수는 `abs(a−b) <= 1e-6 + 1e-6 × abs(b)`.

| 항목 | 100 장면 | 1,000 장면 | 7,481 장면 |
| --- | --- | --- | --- |
| 장면 집합 (n, sha256) | 통과 | 통과 | 통과 |
| `object_features` (F-7·F-8) | 700행 통과 | 7,000행 통과 | 51,865행 통과 |
| `grid_stats` (F-6) | 141,543행 통과 | 1,319,166행 통과 | 9,746,109행 통과 |
| `f5_per_class` (F-5) | 통과 | 통과 | 9행 통과 |
| `grid_cumulative` (F-6) | 통과 | 통과 | 6,400행 통과 |
| `grid_params` (F-6 평균 분모) | 통과 | 통과 | 통과 |
| `f7_class_dist` · `_occluded` · `_truncated` · `_difficulty` | 통과 | 통과 | 31 · 32 · 40 · 8행 통과 |
| `f9_thresholds` · `f9_low_objects` · `f9_low_scenes` | 통과 | 통과 | 8 · 4,298 · 2,729행 통과 |
| **종합** | **13/13** | **13/13** | **13/13** |

근거: `results/subsets/sample_100/verification.md`, `results/subsets/sample_1000/verification.md`, `results/verification.md`. 7,481 규모의 모든 항목에서 키 중복·누락·NULL 위치 불일치·값 불일치가 0이고 최대 실수 차도 0입니다. 필드별 단위·자료형은 `results/units.md`에 있습니다.

성능 측정의 36회 실행도 각각 같은 규모의 순차 결과와 대조해 36/36 통과했습니다(`results/bench/n4_judgment.json` `runs_verify_pass`).

### Spark ↔ MySQL 적재 대조

**62 / 62 항목 일치, 불일치 0건**(`results/mysql_consistency.md`). 행 수 8항목(`frame` 7,481 · `gt_object` 51,865 · `object_feature` 40,570 등), NULL 수 22항목, 클래스별 기준값 8항목, 기준값 대상 객체 수 8항목, 모순 건수 16항목(난이도·거리 구간·`dist_m`·`valid_3d`·DontCare 특징 행 등 모두 0)입니다.

## 6. 화면 품질 (U-2 ~ U-12)

| 항목 | 목표 | 측정 | 근거 |
| --- | --- | --- | --- |
| Lighthouse 접근성 S1·S2·S5 | ≥ 95 | 데스크톱·모바일 모두 100 | `results/ui_qa.md` |
| axe-core 위반 (S1·S2·S5·S6 × 라이트·다크) | 0 | 0 | 같은 파일 |
| 마우스 없이 T1 | 가능 | 14/14 단계 통과 | 같은 파일 |
| 검색 응답 (브라우저, 20회) | p95 ≤ 500 ms | p50 423 · p95 463 · 최대 472 ms | 같은 파일 |
| BEV 첫 그리기 (장면 0·1000·5000, 각 3회) | ≤ 1,500 ms | 중앙값 165–182 ms, 최대 190 ms | 같은 파일 |
| BEV 확대·이동 | ≥ 30 fps | 54.2–56 fps | 같은 파일 |
| 썸네일 크기 | ≤ 30 KB | 최대 13,194 B, 평균 7,852.6 B | `results/u3_thumbnails.json` |
| CSS·JS 합계 | ≤ 300 KB | S1 93.7 · S2 101.2 · S5 294.3 · S6 86.3 KB | `results/ui_qa.md` |
| 반응형 (S1·S2·S5 × 4개 폭) | 겹침·가로 스크롤 0 | 0 | 같은 파일 |
| 토큰 대비 (라이트·다크) | 본문 4.5:1, UI 3:1 | 미달 0쌍 | 같은 파일 |
| 상태 처리 | 재현 가능 | 9/9 | 같은 파일 |
| 콘솔 오류·404 (10개 화면) | 0 | 0 | 같은 파일 |
| 화면 코드의 색상 값 직접 사용 (U-2) | 0 | 0 | 아래 명령 |

U-2 확인 명령(`webapp/`에서, 2026-11-05 실행):

```bash
grep -rnoE "#[0-9a-fA-F]{3,8}\b|rgba?\(" static/css/app.css templates static/js
```

출력은 `static/js/scene.js:54:rgb(` 한 줄뿐이며, 이것은 `tokens.css`의 viridis 토큰을 읽어 색 단계를 계산하는 코드라 색상 값을 직접 쓴 것이 아닙니다.

QA 중 발견해 고친 결함 5건(건너뛰기 링크가 새로고침을 일으킴, 로딩 스켈레톤 없음, 입력 요소 경계 대비 미달, S5 제목 순서, BEV 측정 지점 없음)은 `results/ui_qa.md` "발견한 결함과 조치"에 있습니다.

## 7. 성능 비교 (F-12 ~ F-14)

조건: 파티션 32, AQE 끔, driver 8g, BLAS 스레드 1, 실행마다 새 프로세스, 예열 제외, seed 42 순서, MySQL·웹 앱 중지([ENVIRONMENT.md](ENVIRONMENT.md)). 값은 3회 중앙값 [최솟값–최댓값]입니다.

| 규모 | 방식 | 벽시계 시간 (s) | 순차 대비 | scenes/s | peak RSS (MiB) |
| --- | --- | ---: | ---: | ---: | ---: |
| 100 | 순차 | 1.72 [1.70–1.72] | 1.00× | 58.2 | 143 |
| 100 | local[1] | 26.95 [26.89–27.55] | 0.06× | 3.7 | 1,707 |
| 100 | local[4] | 18.66 [17.40–19.99] | 0.09× | 5.4 | 2,919 |
| 100 | local[8] | 18.31 [17.21–19.09] | 0.09× | 5.5 | 4,169 |
| 1,000 | 순차 | 14.43 [14.31–14.68] | 1.00× | 69.3 | 210 |
| 1,000 | local[1] | 43.80 [43.79–44.01] | 0.33× | 22.8 | 2,251 |
| 1,000 | local[4] | 25.01 [24.28–27.66] | 0.58× | 40.0 | 4,234 |
| 1,000 | local[8] | 22.67 [22.64–24.14] | 0.64× | 44.1 | 4,520 |
| 7,481 | 순차 | 110.59 [107.33–113.77] | 1.00× | 67.6 | 712 |
| 7,481 | local[1] | 159.10 [156.79–174.48] | 0.70× | 47.0 | 2,786 |
| 7,481 | local[4] | 62.21 [61.03–69.48] | 1.78× | 120.2 | 4,915 |
| 7,481 | local[8] | 57.18 [56.00–58.89] | **1.93×** | 130.8 | 6,625 |

근거: `results/bench/summary.csv`, `results/bench/bench_report.md`. 그림: `results/bench/figures/runtime_vs_scale.png`, `speedup.png`, `phase_breakdown.png`, `peak_rss.png`. 7,481 규모 `local[8]`의 처리량은 15,597,841 points/s입니다.

### 단계별 시간 중앙값 (s)

| 규모 | 방식 | 세션·JVM 시작 | 읽기+계산 | 집계 | 쓰기 | 기타 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 100 | 순차 | 0.00 | 1.21 | 0.00 | 0.12 | 0.38 |
| 100 | local[8] | 4.42 | 5.89 | 6.35 | 0.02 | 1.11 |
| 1,000 | 순차 | 0.00 | 12.87 | 0.00 | 1.10 | 0.46 |
| 1,000 | local[8] | 4.22 | 9.51 | 8.28 | 0.02 | 1.08 |
| 7,481 | 순차 | 0.00 | 101.57 | 0.00 | 7.99 | 1.03 |
| 7,481 | local[1] | 4.40 | 107.34 | 46.82 | 0.04 | 0.87 |
| 7,481 | local[4] | 4.20 | 39.63 | 17.68 | 0.04 | 0.89 |
| 7,481 | local[8] | 4.22 | 36.64 | 14.85 | 0.04 | 1.01 |

근거: `results/bench/bench_report.md` "단계별 시간 중앙값". 순차 처리는 집계 시간이 "읽기+계산"에 들어 있습니다.

### 해석

**측정으로 확인된 것**

1. **작은 규모에서는 순차가 빠릅니다.** 100장면에서 Spark는 세션·JVM 시작만 4.2–4.4 s로, 순차 전체(1.72 s)보다 깁니다. 집계 단계(`local[8]` 6.35 s)도 순차 전체보다 깁니다. 집계 단계는 최종 GROUP BY와 F-5/F-6/F-7/F-9 질의를 여러 Spark 작업으로 실행하므로 데이터가 작아도 작업마다 고정 비용이 듭니다. 1,000장면에서도 `local[8]` 22.67 s로 순차 14.43 s보다 느립니다.
2. **7,481장면에서 `local[8]`이 순차 대비 1.93×(순차·Spark 각 3회 최솟값·최댓값으로 계산한 범위 1.82–2.03×)입니다.** `local[4]`는 1.78×입니다.
3. **`local[1]`은 7,481장면에서도 순차보다 느립니다(0.70×).** Spark 경로는 순차에 없는 일을 합니다. 모든 포인트(891,919,836개)를 Python 작업에서 JVM으로 넘겨 Spark SQL GROUP BY로 격자를 집계합니다. 순차는 같은 집계를 프레임마다 numpy로 끝냅니다. 그 결과 `local[1]`의 읽기+계산(107.34 s)이 순차의 읽기+계산+집계(101.57 s)와 비슷하고, 여기에 집계 46.82 s가 더해집니다.
4. **`local[1]` → `local[4]`는 2.56×, `local[4]` → `local[8]`은 1.09×입니다**(벽시계 159.10 → 62.21 → 57.18 s). 단계별로 보면 읽기+계산은 107.34 → 39.63 s(2.71×) → 36.64 s(1.08×), 집계는 46.82 → 17.68 s(2.65×) → 14.85 s(1.19×)입니다. 세션 시작(약 4.2 s)은 방식과 관계없이 거의 같습니다.
5. **원본 읽기는 순차 시간의 작은 부분입니다.** F-11용 순차 전체 실행의 내부 시간은 읽기 14.9 s, 계산 94.4 s, 쓰기 8.2 s입니다(`~/data/kitti/work/seq/full/timings.json`). 예열로 페이지 캐시를 데운 조건이므로, 이 측정에서 디스크 읽기는 주된 병목이 아닙니다.
6. **병렬도가 오를수록 메모리를 더 씁니다.** 7,481장면 peak RSS 중앙값은 순차 712 MiB, `local[1]` 2,786, `local[4]` 4,915, `local[8]` 6,625 MiB입니다. 가장 큰 값 6,817.7 MiB도 예산(물리 RAM 80%, 24,604.6 MiB)의 28%입니다.

**`local[4]` → `local[8]` 향상이 작은 이유 (검증하지 않은 추정)**

아래는 위 단계 시간과 맞는 설명이지만, 따로 실험해 확인하지는 않았습니다.

- 코어 경쟁: 장비는 물리 8코어입니다. `local[8]`에서는 Python worker 8개가 계산하는 동안 JVM도 넘겨받은 포인트를 변환·부분 집계하므로, 바쁜 스레드가 물리 코어 수를 넘을 수 있습니다.
- 작업 꼬리: 장면 목록을 연속 구간 32개로 나눴으므로 `local[8]`은 4차례, `local[4]`는 8차례로 작업을 돌립니다. 구간마다 포인트 수가 다르면 마지막 차례에서 놀고 있는 슬롯이 생깁니다.
- 포인트 단위 고정 비용: 포인트마다 Python → JVM 전달(Arrow 변환)과 SQL 집계가 필요해, 프레임당 numpy 계산보다 병렬화 효과가 작을 수 있습니다.

확인하려면 작업별 시간 기록(`<실행 폴더>/_tasks/part-*.json`)으로 꼬리 길이를 보거나, 파티션 수를 바꿔 다시 측정해야 합니다(README F-12 "초기 비교"의 다음 단계).

## 8. N-4 확장성 판정

**통과**(`results/bench/n4_judgment.json`).

| 기준 | 결과 |
| --- | --- |
| 36회 실행 모두 정상 종료 | 기록 36 / 정상 종료 36 |
| 누락·제외 사유 설명 | 제외 3회 = 규모별 예열 실행(설계상 제외), 그 밖의 누락 0 |
| F-11 정확성 기준 | 36/36 대조 통과 |
| 메모리 예산(물리 RAM 80%) | 최대 peak RSS 6,817.7 MiB ≤ 24,604.6 MiB |

범위: 이 판정이 입증하는 것은 **7,481장면까지**입니다(`scope_note`). 같은 코드·스키마·분석 정의로 입력 장면 목록과 실행 설정만 바꿔 세 규모를 처리했습니다. 속도 향상 배수는 판정 조건이 아닙니다.

## 9. 남은 일과 한계

| 항목 | 내용 | 근거 |
| --- | --- | --- |
| 결정 대기: S1 검수·태그·프리셋 | 미검수만·태그·프리셋·검수자 선택은 DB 과목 테이블(review, tag, search_preset, reviewer)이 필요해 비활성으로 두었습니다(선택지 B). U-4가 "부분"인 이유입니다. | `.omc/autopilot/decisions-pending.md` |
| 결정 대기: 난이도 높이 경계 `>=` vs `>` | README·DB 과목과 같게 `>= 40/25 px`를 씁니다. KITTI devkit은 `>`라서 높이가 정확히 40.00 px인 객체 10개, 25.00 px인 객체 7개(합 17개)는 공식 평가와 등급이 다를 수 있습니다. | `decisions-pending.md`, `results/units.md` |
| DB 과목 범위 기능 | 검수 판정, S3 검수 세트, S4 분석 대시보드는 DB 과목에서 구현합니다. 이 저장소에는 메뉴와 안내 화면만 있습니다. | `docs/ui/README.md` |
| U-5 증거 | 표 → 이미지 강조만 자동 확인했습니다. 이미지·BEV에서 시작하는 강조는 눈으로 확인해 캡처를 남겨야 합니다. | `results/ui_qa.md` |
| fps 측정 조건 | headless Chrome(GPU 없음, 소프트웨어 래스터)에서 rAF 간격으로 쟀습니다. CPU를 4배 느리게 하면 12.6 fps로 떨어집니다. 실제 시연 화면에서는 다를 수 있습니다. | `results/ui_qa.md` U-11 BEV 세부 |
| Lighthouse 성능 점수 | 55–69점입니다. README에 목표가 없어 참고로만 기록했습니다. 주원인은 Pretendard 전체 글꼴 4개(약 3.1 MB)와 압축하지 않은 개발 서버 응답입니다. 실제 관측 FCP는 0.15–0.55 s입니다. | `results/ui_qa.md` |
| S2 첫 표시(목표 1초) 기록 | `ui_qa.md` 요약표에 이 항목이 따로 없습니다. Lighthouse 관측 LCP는 S2 0.156–0.170 s이지만, 키보드 T1 단계 "Enter → S2 opened"는 `elapsed_s` 3.522로 기록됐습니다(자동화 대기 시간 포함 여부 불명). 서버 쪽 S2 응답 시간은 `smoke_webapp.py`가 재지만 파일로 남기지 않았습니다. | `results/ui_qa.md` |
| S5 페이지 용량 여유 | CSS·JS 294.3 KB로 기준(300 KB) 안이지만 Chart.js가 207 KB라 여유가 5.7 KB입니다. | `results/ui_qa.md` |
| U-13 사용성 점검 | 프로젝트를 모르는 참여자 3–5명이 필요합니다. 양식은 [USABILITY_TEST.md](USABILITY_TEST.md)에 있습니다. | README 9.11 |
| 콜드 캐시 성능 | 페이지 캐시를 데운 상태만 측정했습니다. 처음 읽는 디스크 성능은 결과에 없습니다. | `results/bench/bench_report.md` |
| 점검 스크립트 출력 | `check_box_independent.py`·`check_features_real.py`·`smoke_webapp.py`의 통과 출력은 파일로 남지 않습니다(`smoke_webapp.py`는 `--json`을 주면 저장). | `scripts/` |
