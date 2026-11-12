# UI 브라우저 QA 결과 (README 9절, U-4–U-12)

- 측정 일시: 2026-11-05T07:09:37.064Z
- 브라우저: Chrome/155.0.8059.39 headless (/usr/bin/google-chrome), puppeteer-core 23, axe-core 4, Lighthouse 12.8.2
- 앱: flask --app webapp:create_app run --port 5057 (real MySQL via ~/.my.cnf); DB-down instance on 5058 with KITTI_MYSQL_CNF=<nonexistent>
- 도구는 `/tmp/claude-1000/qa-ui`(임시 npm 디렉터리)에만 설치했다. 원자료: `results/ui_qa.json`. 화면 캡처: `docs/ui/screenshots/qa/`.
- 서버 쪽 데이터 일치·서버 응답 시간은 `scripts/smoke_webapp.py`가 따로 확인한다(여기서는 중복하지 않음).

## 요약

| 항목 | 목표 | 측정 | 판정 |
| --- | --- | --- | --- |
| U-9 Lighthouse 접근성 S1 | ≥ 95 | 데스크톱 100 · 모바일 100 | 통과 |
| U-9 Lighthouse 접근성 S2(1000) | ≥ 95 | 데스크톱 100 · 모바일 100 | 통과 |
| U-9 Lighthouse 접근성 S5 | ≥ 95 | 데스크톱 100 · 모바일 100 | 통과 |
| U-9 Lighthouse 접근성 S6 | (목표 없음, 참고) | 데스크톱 100 · 모바일 100 | 통과 |
| U-9 axe-core (WCAG 2.1 A/AA + best-practice), S1·S2·S5·S6 × 라이트·다크 | 위반 0 | 0건 | 통과 |
| U-9 마우스 없이 T1 완료 | 가능 | 14/14 단계 통과 | 통과 |
| U-11 BEV 첫 그리기 장면 0 (3회) | ≤ 1,500 ms | 중앙값 182 ms · 최대 190 ms | 통과 |
| U-11 BEV 첫 그리기 장면 1000 (3회) | ≤ 1,500 ms | 중앙값 165 ms · 최대 179 ms | 통과 |
| U-11 BEV 첫 그리기 장면 5000 (3회) | ≤ 1,500 ms | 중앙값 166 ms · 최대 186 ms | 통과 |
| U-11 BEV 확대·이동 fps 장면 0 | ≥ 30 fps | 54.3 fps (프레임 p95 33.3 ms, BEV 다시 그리기 80회) | 통과 |
| U-11 BEV 확대·이동 fps 장면 1000 | ≥ 30 fps | 56 fps (프레임 p95 33.3 ms, BEV 다시 그리기 80회) | 통과 |
| U-11 BEV 확대·이동 fps 장면 5000 | ≥ 30 fps | 54.2 fps (프레임 p95 33.3 ms, BEV 다시 그리기 80회) | 통과 |
| U-11 BEV 한 번 그리기 비용 (장면 1000, 점 58,655개) | ≤ 33 ms | p50 17.9 ms · p95 20.3 ms | 통과 |
| U-11 검색 응답 (브라우저, 조건 변경→결과 표시, 디바운스 300 ms 제외, 20회) | p95 ≤ 500 ms | p50 423 · p95 463 · 최대 472 ms | 통과 |
| U-11 CSS·JS 합계 | ≤ 300 KB | S1 93.7 KB · S2 101.2 KB · S5 294.3 KB · S6 86.3 KB | 통과 |
| U-10 가로 스크롤·헤더 겹침 (S1·S2·S5 × 1440·1024·768·390) | 0 | 0건 | 통과 |
| U-12 토큰 대비 (라이트·다크, 본문 4.5:1 · UI 3:1) | 모두 만족 | 미달 0쌍 | 통과 |
| U-12 테마 전환 (시스템 설정 + 헤더 버튼 + 저장) | 동작 | 전환·저장·다음 화면 유지·시스템 다크에서 라이트 고정 | 통과 |
| U-8 상태 처리 (로딩·빈 결과·오류·부분 성공) | 재현 가능 | 9/9 | 통과 |
| 콘솔 오류·404 (10개 화면) | 0 | 0 | 통과 |

## U-9 Lighthouse 점수 (수정 후)

| 화면 | 프리셋 | 접근성 | 권장사항 | 성능 | FCP 관측 / 시뮬레이션 | LCP 관측 / 시뮬레이션 |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | desktop | 100 | 100 | 66 | 549 / 2526 ms | 577 / 3470 ms |
| S1 | mobile | 100 | 96 | 55 | 546 / 14107 ms | 566 / 19057 ms |
| S2(1000) | desktop | 100 | 100 | 63 | 170 / 2487 ms | 170 / 4847 ms |
| S2(1000) | mobile | 100 | 100 | 55 | 156 / 13808 ms | 156 / 29426 ms |
| S5 | desktop | 100 | 100 | 67 | 172 / 2485 ms | 172 / 3325 ms |
| S5 | mobile | 100 | 100 | 55 | 148 / 13808 ms | 148 / 19358 ms |
| S6 | desktop | 100 | 100 | 69 | 173 / 2405 ms | 173 / 3125 ms |
| S6 | mobile | 100 | 100 | 56 | 147 / 10206 ms | 147 / 13506 ms |

README에는 Lighthouse 성능 점수 목표가 없다(목표는 9.9의 실측 시간). 성능 점수가 낮은 이유는 Lighthouse의 시뮬레이션 회선(데스크톱 10 Mbps, 모바일 1.6 Mbps·4× CPU)에서 앱에 포함한 Pretendard 한글 전체 글꼴 4개(각 약 770 KB, 합계 약 3.1 MB)와 압축하지 않은 Flask 개발 서버 응답(app.css 63 KB 등)이 첫 그리기를 늦추기 때문이다. 같은 실행에서 실제 관측한 첫 그리기(FCP)는 S2·S5·S6 0.15–0.17 s, S1 0.55 s(MySQL 검색 포함)이다. TBT 0 ms, CLS ≤ 0.022. 모바일 S1 권장사항 96점은 `image-size-responsive`(320 px 썸네일을 고해상도 화면 2.6배로 표시) 1건이다.

## U-9 키보드만으로 T1 (마우스 없이)

| 단계 | 결과 | 판정 |
| --- | --- | --- |
| Tab → skip link | `{"cls": "skip-link", "text": "본문으로 건너뛰기", "outline": "solid 2px", "visible": true}` | 통과 |
| Enter on skip link → focus main | `{"active": "main"}` | 통과 |
| '/' → first filter control | `{"id": "cls-car", "name": "cls"}` | 통과 |
| focus ring on custom checkbox | `{"outlineStyle": "solid", "outlineWidth": "2px", "outlineColor": "rgb(36, 87, 214)", "accent": "#2457D6"}` | 통과 |
| T1 filters by keyboard (Space/typing) → URL updated | `{"url": "?cls=Pedestrian&dmin=30&occ=1", "count": "장면 140개· 객체 203개 일치"}` | 통과 |
| Tab → first result card (visible focus) | `{"found": true, "outline": "none 3px", "boxShadow": "rgb(36, 87, 214) 0px 0px 0px 1px", "href": "/scenes/1611?cls=Pedestrian&dmin=30&occ=1&i=1"}` | 통과 |
| Enter → S2 opened | `{"url": "/scenes/1611?cls=Pedestrian&dmin=30&occ=1&i=1", "elapsed_s": 3.522}` | 통과 |
| S2 → (next) | `{"before": "검색 결과 (1 / 140)", "after": "검색 결과 (2 / 140)"}` | 통과 |
| S2 ← (prev) | `{"after": "검색 결과 (1 / 140)"}` | 통과 |
| Tab to object row + Enter → selection fixed, image box highlighted | `{"found": true, "id": "1", "row": true, "ibox": true, "selection": "선택한 객체: 1 보행자", "rowOutline": "solid"}` | 통과 |
| Esc → clears selection | `{}` | 통과 |
| BEV focus ring + '+', '←' pans (no scene change), '0' resets | `{"bevRing": "solid 2px"}` | 통과 |
| '?' opens shortcut dialog, Esc closes it | `{}` | 통과 |
| Esc → back to S1 with same query | `{"url": "/?cls=Pedestrian&dmin=30&occ=1"}` | 통과 |

단축키(README 9.8 `/` · `←` `→` · `Esc` · `?`)와 BEV 키(`+` `−` `0` 방향키 이동, 방향키가 장면을 바꾸지 않음)를 모두 확인했다. 포커스 테두리는 2 px `--accent`(rgb(36, 87, 214) = #2457D6).

## U-11 BEV 세부

- 측정: `scene.js`가 점군을 처음 다 그린 순간 `performance.mark("kse:bev-first-draw")`를 남긴다(이번 QA에서 추가). 값은 탐색 시작(navigation start)부터의 시간이다.
- 장면 0: 점군 1,009,616 B, 전송 24 ms, 첫 그리기 182, 190, 161 ms
- 장면 1000: 점군 938,480 B, 전송 26 ms, 첫 그리기 165, 179, 155 ms
- 장면 5000: 점군 1,005,904 B, 전송 9 ms, 첫 그리기 155, 186, 166 ms
- 확대·이동은 휠 20회 + 드래그 60단계를 실제 입력(CDP)으로 보냈다. headless Chrome(GPU 없음, 소프트웨어 래스터) 기준이다.
- 여유: CPU를 4배 느리게 하면 12.6 fps(rAF p95 82.6 ms)로 떨어진다. 이 장비에서는 목표를 만족하지만, 훨씬 느린 시연 장비라면 README 9.12의 대책(간격 추출 또는 ImageData 직접 쓰기)이 필요할 수 있다.

## U-10 반응형

| 화면 | 폭 | scrollWidth | 가로 스크롤 없음 | 헤더 겹침 | 캡처 |
| --- | --- | --- | --- | --- | --- |
| S1 | 1440 | 1440 | 통과 | 0 | `resp-s1-1440.png` |
| S2 | 1440 | 1440 | 통과 | 0 | `resp-s2-1440.png` |
| S5 | 1440 | 1440 | 통과 | 0 | `resp-s5-1440.png` |
| S1 | 1024 | 1024 | 통과 | 0 | `resp-s1-1024.png` |
| S2 | 1024 | 1024 | 통과 | 0 | `resp-s2-1024.png` |
| S5 | 1024 | 1024 | 통과 | 0 | `resp-s5-1024.png` |
| S1 | 768 | 768 | 통과 | 0 | `resp-s1-768.png` |
| S2 | 768 | 768 | 통과 | 0 | `resp-s2-768.png` |
| S5 | 768 | 768 | 통과 | 0 | `resp-s5-768.png` |
| S1 | 390 | 390 | 통과 | 0 | `resp-s1-390.png` |
| S2 | 390 | 390 | 통과 | 0 | `resp-s2-390.png` |
| S5 | 390 | 390 | 통과 | 0 | `resp-s5-390.png` |

390 px 전체 길이 캡처에서 화면 밖 썸네일이 검게 보이는 것은 `loading="lazy"`(9.9 ‘화면에 들어올 때 불러오기’) 때문이며 결함이 아니다.

## U-12 다크 테마와 대비

`tokens.css`에서 직접 계산했다(`prefers-color-scheme: dark` 블록과 `[data-theme=dark]` 블록이 같은지도 확인: True).

| 테마 | 글자/요소 | 바탕 | 대비 | 기준 | 판정 |
| --- | --- | --- | --- | --- | --- |
| light | `--text` #14171F | `--bg` #FFFFFF | 17.92:1 | 4.5:1 | 통과 |
| light | `--text` #14171F | `--surface` #F6F7F9 | 16.71:1 | 4.5:1 | 통과 |
| light | `--text` #14171F | `--surface-hover` #EEF0F3 | 15.69:1 | 4.5:1 | 통과 |
| light | `--text-muted` #5B6372 | `--bg` #FFFFFF | 6.05:1 | 4.5:1 | 통과 |
| light | `--text-muted` #5B6372 | `--surface` #F6F7F9 | 5.64:1 | 4.5:1 | 통과 |
| light | `--text-muted` #5B6372 | `--surface-hover` #EEF0F3 | 5.3:1 | 4.5:1 | 통과 |
| light | `--accent` #2457D6 | `--bg` #FFFFFF | 6.16:1 | 4.5:1 | 통과 |
| light | `--accent` #2457D6 | `--surface` #F6F7F9 | 5.75:1 | 4.5:1 | 통과 |
| light | `--accent` #2457D6 | `--accent-soft` #E9EFFC | 5.34:1 | 4.5:1 | 통과 |
| light | `--accent-ink` #FFFFFF | `--accent` #2457D6 | 6.16:1 | 4.5:1 | 통과 |
| light | `--accent-ink` #FFFFFF | `--accent-hover` #1C47B3 | 8.07:1 | 4.5:1 | 통과 |
| light | `--warn` #9E5300 | `--bg` #FFFFFF | 5.69:1 | 4.5:1 | 통과 |
| light | `--warn` #9E5300 | `--warn-soft` #FBF1E4 | 5.09:1 | 4.5:1 | 통과 |
| light | `--warn` #9E5300 | `--surface` #F6F7F9 | 5.31:1 | 4.5:1 | 통과 |
| light | `--danger` #C62828 | `--bg` #FFFFFF | 5.62:1 | 4.5:1 | 통과 |
| light | `--danger` #C62828 | `--danger-soft` #FCECEC | 4.91:1 | 4.5:1 | 통과 |
| light | `--danger-ink` #FFFFFF | `--danger` #C62828 | 5.62:1 | 4.5:1 | 통과 |
| light | `--ok` #1E7F4F | `--bg` #FFFFFF | 5.0:1 | 4.5:1 | 통과 |
| light | `--ok` #1E7F4F | `--ok-soft` #EEF8F2 | 4.6:1 | 4.5:1 | 통과 |
| light | `--ok` #1E7F4F | `--surface` #F6F7F9 | 4.66:1 | 4.5:1 | 통과 |
| light | `--accent` #2457D6 | `--bg` #FFFFFF | 6.16:1 | 3.0:1 | 통과 |
| light | `--accent` #2457D6 | `--surface` #F6F7F9 | 5.75:1 | 3.0:1 | 통과 |
| light | `--control-border` #808998 | `--bg` #FFFFFF | 3.53:1 | 3.0:1 | 통과 |
| light | `--control-border` #808998 | `--surface` #F6F7F9 | 3.29:1 | 3.0:1 | 통과 |
| light | `--control-border` #808998 | `--surface-hover` #EEF0F3 | 3.09:1 | 3.0:1 | 통과 |
| light | `--viz-control-border` #6B7385 | `--viz-bg` #0F1115 | 3.97:1 | 3.0:1 | 통과 |
| light | `--viz-control-border` #6B7385 | `--viz-surface` #171A21 | 3.66:1 | 3.0:1 | 통과 |
| light | `--viz-text` #E8EAEE | `--viz-bg` #0F1115 | 15.69:1 | 4.5:1 | 통과 |
| light | `--viz-text` #E8EAEE | `--viz-surface` #171A21 | 14.45:1 | 4.5:1 | 통과 |
| light | `--viz-text-muted` #9AA3B2 | `--viz-bg` #0F1115 | 7.43:1 | 4.5:1 | 통과 |
| light | `--viz-text-muted` #9AA3B2 | `--viz-surface` #171A21 | 6.84:1 | 4.5:1 | 통과 |
| light | `--viz-accent` #6E9BFF | `--viz-bg` #0F1115 | 7.01:1 | 3.0:1 | 통과 |
| light | `--viz-warn` #F0A63A | `--viz-warn-soft` #2E2416 | 7.41:1 | 4.5:1 | 통과 |
| light | `--cls-car-ink` #FFFFFF | `--cls-car` #0072B2 | 5.19:1 | 4.5:1 | 통과 |
| light | `--cls-car` #0072B2 | `--viz-bg` #0F1115 | 3.64:1 | 3.0:1 | 통과 |
| light | `--cls-van-ink` #0F1115 | `--cls-van` #56B4E9 | 8.19:1 | 4.5:1 | 통과 |
| light | `--cls-van` #56B4E9 | `--viz-bg` #0F1115 | 8.19:1 | 3.0:1 | 통과 |
| light | `--cls-truck-ink` #0F1115 | `--cls-truck` #E69F00 | 8.39:1 | 4.5:1 | 통과 |
| light | `--cls-truck` #E69F00 | `--viz-bg` #0F1115 | 8.39:1 | 3.0:1 | 통과 |
| light | `--cls-pedestrian-ink` #0F1115 | `--cls-pedestrian` #D55E00 | 4.89:1 | 4.5:1 | 통과 |
| light | `--cls-pedestrian` #D55E00 | `--viz-bg` #0F1115 | 4.89:1 | 3.0:1 | 통과 |
| light | `--cls-person-sitting-ink` #0F1115 | `--cls-person-sitting` #F0E442 | 14.29:1 | 4.5:1 | 통과 |
| light | `--cls-person-sitting` #F0E442 | `--viz-bg` #0F1115 | 14.29:1 | 3.0:1 | 통과 |
| light | `--cls-cyclist-ink` #0F1115 | `--cls-cyclist` #009E73 | 5.52:1 | 4.5:1 | 통과 |
| light | `--cls-cyclist` #009E73 | `--viz-bg` #0F1115 | 5.52:1 | 3.0:1 | 통과 |
| light | `--cls-tram-ink` #0F1115 | `--cls-tram` #CC79A7 | 6.17:1 | 4.5:1 | 통과 |
| light | `--cls-tram` #CC79A7 | `--viz-bg` #0F1115 | 6.17:1 | 3.0:1 | 통과 |
| light | `--cls-misc-ink` #0F1115 | `--cls-misc` #8C8C8C | 5.62:1 | 4.5:1 | 통과 |
| light | `--cls-misc` #8C8C8C | `--viz-bg` #0F1115 | 5.62:1 | 3.0:1 | 통과 |
| dark | `--text` #E8EAEE | `--bg` #0F1115 | 15.69:1 | 4.5:1 | 통과 |
| dark | `--text` #E8EAEE | `--surface` #171A21 | 14.45:1 | 4.5:1 | 통과 |
| dark | `--text` #E8EAEE | `--surface-hover` #222733 | 12.4:1 | 4.5:1 | 통과 |
| dark | `--text-muted` #9AA3B2 | `--bg` #0F1115 | 7.43:1 | 4.5:1 | 통과 |
| dark | `--text-muted` #9AA3B2 | `--surface` #171A21 | 6.84:1 | 4.5:1 | 통과 |
| dark | `--text-muted` #9AA3B2 | `--surface-hover` #222733 | 5.87:1 | 4.5:1 | 통과 |
| dark | `--accent` #6E9BFF | `--bg` #0F1115 | 7.01:1 | 4.5:1 | 통과 |
| dark | `--accent` #6E9BFF | `--surface` #171A21 | 6.46:1 | 4.5:1 | 통과 |
| dark | `--accent` #6E9BFF | `--accent-soft` #1C2A4A | 5.27:1 | 4.5:1 | 통과 |
| dark | `--accent-ink` #0F1115 | `--accent` #6E9BFF | 7.01:1 | 4.5:1 | 통과 |
| dark | `--accent-ink` #0F1115 | `--accent-hover` #8FB2FF | 8.98:1 | 4.5:1 | 통과 |
| dark | `--warn` #F0A63A | `--bg` #0F1115 | 9.2:1 | 4.5:1 | 통과 |
| dark | `--warn` #F0A63A | `--warn-soft` #2E2416 | 7.41:1 | 4.5:1 | 통과 |
| dark | `--warn` #F0A63A | `--surface` #171A21 | 8.47:1 | 4.5:1 | 통과 |
| dark | `--danger` #FF6B6B | `--bg` #0F1115 | 6.81:1 | 4.5:1 | 통과 |
| dark | `--danger` #FF6B6B | `--danger-soft` #331B1E | 5.75:1 | 4.5:1 | 통과 |
| dark | `--danger-ink` #0F1115 | `--danger` #FF6B6B | 6.81:1 | 4.5:1 | 통과 |
| dark | `--ok` #4CC38A | `--bg` #0F1115 | 8.53:1 | 4.5:1 | 통과 |
| dark | `--ok` #4CC38A | `--ok-soft` #15291F | 6.93:1 | 4.5:1 | 통과 |
| dark | `--ok` #4CC38A | `--surface` #171A21 | 7.86:1 | 4.5:1 | 통과 |
| dark | `--accent` #6E9BFF | `--bg` #0F1115 | 7.01:1 | 3.0:1 | 통과 |
| dark | `--accent` #6E9BFF | `--surface` #171A21 | 6.46:1 | 3.0:1 | 통과 |
| dark | `--control-border` #6B7385 | `--bg` #0F1115 | 3.97:1 | 3.0:1 | 통과 |
| dark | `--control-border` #6B7385 | `--surface` #171A21 | 3.66:1 | 3.0:1 | 통과 |
| dark | `--control-border` #6B7385 | `--surface-hover` #222733 | 3.14:1 | 3.0:1 | 통과 |
| dark | `--viz-control-border` #6B7385 | `--viz-bg` #0F1115 | 3.97:1 | 3.0:1 | 통과 |
| dark | `--viz-control-border` #6B7385 | `--viz-surface` #171A21 | 3.66:1 | 3.0:1 | 통과 |
| dark | `--viz-text` #E8EAEE | `--viz-bg` #0F1115 | 15.69:1 | 4.5:1 | 통과 |
| dark | `--viz-text` #E8EAEE | `--viz-surface` #171A21 | 14.45:1 | 4.5:1 | 통과 |
| dark | `--viz-text-muted` #9AA3B2 | `--viz-bg` #0F1115 | 7.43:1 | 4.5:1 | 통과 |
| dark | `--viz-text-muted` #9AA3B2 | `--viz-surface` #171A21 | 6.84:1 | 4.5:1 | 통과 |
| dark | `--viz-accent` #6E9BFF | `--viz-bg` #0F1115 | 7.01:1 | 3.0:1 | 통과 |
| dark | `--viz-warn` #F0A63A | `--viz-warn-soft` #2E2416 | 7.41:1 | 4.5:1 | 통과 |
| dark | `--cls-car-ink` #FFFFFF | `--cls-car` #0072B2 | 5.19:1 | 4.5:1 | 통과 |
| dark | `--cls-car` #0072B2 | `--viz-bg` #0F1115 | 3.64:1 | 3.0:1 | 통과 |
| dark | `--cls-van-ink` #0F1115 | `--cls-van` #56B4E9 | 8.19:1 | 4.5:1 | 통과 |
| dark | `--cls-van` #56B4E9 | `--viz-bg` #0F1115 | 8.19:1 | 3.0:1 | 통과 |
| dark | `--cls-truck-ink` #0F1115 | `--cls-truck` #E69F00 | 8.39:1 | 4.5:1 | 통과 |
| dark | `--cls-truck` #E69F00 | `--viz-bg` #0F1115 | 8.39:1 | 3.0:1 | 통과 |
| dark | `--cls-pedestrian-ink` #0F1115 | `--cls-pedestrian` #D55E00 | 4.89:1 | 4.5:1 | 통과 |
| dark | `--cls-pedestrian` #D55E00 | `--viz-bg` #0F1115 | 4.89:1 | 3.0:1 | 통과 |
| dark | `--cls-person-sitting-ink` #0F1115 | `--cls-person-sitting` #F0E442 | 14.29:1 | 4.5:1 | 통과 |
| dark | `--cls-person-sitting` #F0E442 | `--viz-bg` #0F1115 | 14.29:1 | 3.0:1 | 통과 |
| dark | `--cls-cyclist-ink` #0F1115 | `--cls-cyclist` #009E73 | 5.52:1 | 4.5:1 | 통과 |
| dark | `--cls-cyclist` #009E73 | `--viz-bg` #0F1115 | 5.52:1 | 3.0:1 | 통과 |
| dark | `--cls-tram-ink` #0F1115 | `--cls-tram` #CC79A7 | 6.17:1 | 4.5:1 | 통과 |
| dark | `--cls-tram` #CC79A7 | `--viz-bg` #0F1115 | 6.17:1 | 3.0:1 | 통과 |
| dark | `--cls-misc-ink` #0F1115 | `--cls-misc` #8C8C8C | 5.62:1 | 4.5:1 | 통과 |
| dark | `--cls-misc` #8C8C8C | `--viz-bg` #0F1115 | 5.62:1 | 3.0:1 | 통과 |

`--border-strong`(구분선·버튼 외곽, 글자 라벨이 있는 요소)은 1.5–1.9:1이라 장식 용도로만 남기고, 상태를 알아봐야 하는 입력 요소 경계는 새 토큰 `--control-border`로 옮겼다(아래 결함 3).

캡처: `dark-system-s1-1440.png`, `dark-system-s2-1440.png`, `dark-system-s5-1440.png`, `dark-system-s6-1440.png`, `dark-toggle-s2-1440.png`.

## U-8 상태 처리

| 상태 | 재현 방법 | 결과 | 판정 |
| --- | --- | --- | --- |
| S1 로딩 | 검색 요청을 1.5 s 지연 | aria-busy, 결과 카드 모양 스켈레톤 True → 끝나면 사라짐 (`state-s1-loading.png`) | 통과 |
| S1 깜빡임 방지 | 지연 없음 | 요청 336 ms (300 ms 이상이므로 스켈레톤 표시가 맞음; 300 ms 미만이면 표시 안 함) | 통과 |
| S2 BEV 로딩 | points.bin 1.5 s 지연 | “점군 불러오는 중…” 진행 표시줄 (`state-s2-bev-loading.png`) | 통과 |
| S2 부분 성공 | points.bin 404 | 이미지는 표시, BEV 자리에 “이 장면의 LiDAR 파일을 찾지 못했습니다(001000.bin).” (`state-s2-partial-lidar.png`) | 통과 |
| S1 빈 결과 | `/?cls=Tram&dmax=5&low_obs=1` | “조건에 맞는 장면이 없습니다” + “거리 조건을 해제하면 55개가 있습니다.” (`state-s1-empty.png`) | 통과 |
| S1 DB 연결 실패 | `KITTI_MYSQL_CNF`=없는 파일 | HTTP 503, “데이터베이스에 연결하지 못했습니다. MySQL 서버가 실행 중인지 확인해 주세요.”, ‘자세히’ 접힘 True, ‘다시 시도’ True (`state-s1-db-down.png`) | 통과 |
| S2 DB 연결 실패 | `KITTI_MYSQL_CNF`=없는 파일 | HTTP 503, “데이터베이스에 연결하지 못했습니다. MySQL 서버가 실행 중인지 확인해 주세요.”, ‘자세히’ 접힘 True, ‘다시 시도’ True (`state-s2-db-down.png`) | 통과 |
| S6 DB 연결 실패 | `KITTI_MYSQL_CNF`=없는 파일 | HTTP 200, “기준값을 불러오지 못했습니다. 데이터베이스 연결을 확인해 주세요.”, ‘자세히’ 접힘 True, ‘다시 시도’ True (`state-s6-db-down.png`) | 통과 |
| S1 DB 실패 중 조건 변경 | 같은 인스턴스에서 차량 체크 | 결과 영역에 “데이터베이스에 연결하지 못했습니다. MySQL 서버가 실행 중인지 확인해 주세요.”, URL `?cls=Car` 유지 | 통과 |

DB 연결 실패 문구는 DB 과목 README의 NR-4 표 ‘DB 연결 실패’ 문구와 글자까지 같다. 500 화면은 0건(DB 실패는 503 안내 화면, S6은 200에 기준값 부분만 오류).

## 콘솔 오류·404

| 화면 | URL | 콘솔 오류 | 페이지 예외 | 4xx·5xx·실패 요청 |
| --- | --- | --- | --- | --- |
| S1 | `/` | 0 | 0 | 0 |
| S2 | `/scenes/1000` | 0 | 0 | 0 |
| S5 | `/pipeline` | 0 | 0 | 0 |
| S6 | `/about` | 0 | 0 | 0 |
| S2_0 | `/scenes/0` | 0 | 0 | 0 |
| S2_5000 | `/scenes/5000` | 0 | 0 | 0 |
| S3 | `/review-sets` | 0 | 0 | 0 |
| S4 | `/dashboard` | 0 | 0 | 0 |
| S1_T1 | `/?cls=Pedestrian&dmin=30&occ=1` | 0 | 0 | 0 |
| S1_empty | `/?cls=Tram&dmax=5&low_obs=1` | 0 | 0 | 0 |

S5는 탭 5개를 모두 눌러 그래프를 그린 뒤, S1은 끝까지 스크롤해 썸네일을 모두 불러온 뒤 셌다.

## 발견한 결함과 조치

| # | 결함 | 영향 | 조치 (파일) | 확인 |
| --- | --- | --- | --- | --- |
| 1 | S1에서 ‘본문으로 건너뛰기’(#main)를 누르면 `popstate` 처리기가 `location.reload()`를 불러 화면 전체를 다시 읽고 포커스가 BODY로 돌아감 | 키보드 사용자 건너뛰기 링크 무력화, 해시 이동마다 MySQL 검색 재실행 (U-9) | 쿼리(`location.search`)가 바뀐 경우에만 다시 읽도록 변경, pushState 때 기준 갱신 (`webapp/static/js/search.js`) | 건너뛰기 후 포커스 = `main#main`, 뒤로 가기 동작 유지 |
| 2 | S1 검색이 300 ms보다 오래 걸려도 결과 카드 모양 스켈레톤이 나오지 않음(aria-busy만 설정, 투명도 변화도 지연 300 ms 뒤라 사실상 변화 없음). 또 중단된 앞 요청이 새 요청의 로딩 표시를 지울 수 있었음 | README 9.5 로딩 상태 미구현 (U-8) | `tpl-loading`(기존 `skeleton_cards` 매크로 8장) 추가, 300 ms 뒤 결과 영역을 스켈레톤으로 교체, 최신 요청만 로딩 표시 해제 (`webapp/templates/pages/search.html`, `webapp/static/js/search.js`) | `state-s1-loading.png`, 끝나면 사라짐 |
| 3 | 체크박스·스위치(꺼짐)·가림 토글·숫자 입력·선택 상자의 경계가 `--border-strong`(라이트 1.47–1.58:1, 다크 1.70–1.85:1) | WCAG 1.4.11(UI 요소 3:1) 미달, 꺼진 체크박스·스위치를 알아보기 어려움 (U-12) | 새 토큰 `--control-border` 라이트 #808998 · 다크 #6B7385, 시각화 영역 `--viz-control-border` 추가(`tokens.css`만 색 값 보유), 다섯 컴포넌트가 이 토큰을 쓰도록 변경 (`webapp/static/css/tokens.css`, `app.css`) | 3.09–3.97:1, axe 위반 0 |
| 4 | S5 ‘원본 파일’ 제목이 h1 다음 바로 h3 | Lighthouse 접근성 S5 99점, axe `heading-order` (U-9) | h2로 변경, CSS 선택자 맞춤 (`webapp/templates/pages/pipeline.html`, `app.css`) | S5 100점, axe 0 |
| 5 | BEV 그리기 완료 시점을 측정할 방법이 없음 | U-11 BEV 1.5 s 기준을 브라우저에서 잴 수 없음 | 첫 그리기 때 `performance.mark("kse:bev-first-draw")` 추가 (`webapp/static/js/scene.js`) | 위 BEV 표 |

수정 뒤 `pytest -q` 275개 통과, `scripts/smoke_webapp.py` 종료 코드 0.

## 측정하지 못했거나 한계가 있는 항목

- Lighthouse 성능 점수: README 목표가 없어 참고로만 기록했다(위 설명). 개선하려면 Pretendard를 한글 사용 범위로 나눈(subset) 글꼴로 바꾸거나 정적 파일 압축이 필요하다. 글꼴 교체는 디자인 결정이라 이번에 바꾸지 않았다.
- fps는 headless Chrome(GPU 없음)에서 rAF 간격으로 쟀다. 실제 화면 주사율·GPU 래스터와 다를 수 있다.
- U-10 ‘겹침’은 헤더 요소끼리의 겹침과 가로 넘침만 자동으로 셌다. 본문 겹침은 캡처를 눈으로 확인했다.
- S5 CSS·JS 294.3 KB는 300 KB 기준 안이지만 Chart.js(207 KB)가 대부분이라 여유가 5.7 KB뿐이다.
- U-13(사용성 점검)은 사람 참여가 필요해 범위 밖이다. U-4·U-7의 데이터 일치는 smoke 스크립트 몫이다.
