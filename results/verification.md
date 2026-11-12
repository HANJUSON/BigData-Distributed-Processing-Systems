# F-11 정확성 검증: Spark ↔ Python 순차 처리

- 생성 시각(UTC): 2026-11-05T04:31:11Z
- Spark 작업 폴더: `~/data/kitti/work` / 결과 폴더: `results`
- 순차 결과 폴더: `~/data/kitti/work/seq/full`
- 대상 장면 수: 7481
- 실수 허용 오차: `abs(a-b) <= 1e-06 + 1e-06 × abs(b)` (a = Spark, b = 순차)
- **종합 결과: 통과** (13/13 항목 통과)

- 장면 집합: Spark {'n': 7481, 'sha256': 'ed822896af3e966c5181d25178c5e93e13221e04dd681443a2ac415da4ab494e'} / 순차 {'n': 7481, 'sha256': 'ed822896af3e966c5181d25178c5e93e13221e04dd681443a2ac415da4ab494e'}

## 항목별 결과

| 항목 | 행 수 (Spark / 순차) | 키 중복 (Spark / 순차) | 키 누락 (Spark만 / 순차만) | NULL 위치 불일치 | 값 불일치 | 최대 실수 차 | 결과 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 대조 장면 집합 동일 (n, sha256) (`frame_set`) | - | - | - | - | - | - | 통과 |
| 객체 특징 표 (F-7·F-8) (`object_features`) | 51,865 / 51,865 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 장면별 격자 통계 (F-6) (`grid_stats`) | 9,746,109 / 9,746,109 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 클래스별 요약 (F-5) (`f5_per_class`) | 9 / 9 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 전체 누적 격자 (F-6) (`grid_cumulative`) | 6,400 / 6,400 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 정상 처리 프레임 수 (F-6 평균 분모) (`grid_params`) | 1 / 1 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 클래스 × 거리 구간 (F-7) (`f7_class_dist`) | 31 / 31 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 클래스 × 가림 (F-7) (`f7_class_occluded`) | 32 / 32 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 클래스 × 잘림 구간 (F-7) (`f7_class_truncated`) | 40 / 40 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 클래스별 난이도 적격 (F-7) (`f7_class_difficulty`) | 8 / 8 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 클래스별 관측 부족 기준값 (F-9) (`f9_thresholds`) | 8 / 8 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 관측 부족 객체 목록 (F-9) (`f9_low_objects`) | 4,298 / 4,298 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |
| 관측 부족 장면 목록 (F-9) (`f9_low_scenes`) | 2,729 / 2,729 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 통과 |

## 범위

- object_features의 points_in_box·dist_m·난이도 열(eligible_easy/moderate/hard, diff_level, dist_bin)은 양쪽 모두 같은 순수 함수(kitti.features.features_from_arrays)로 계산한다. 따라서 F-11은 이 열들에 대해 Spark 배관(장면 분할·직렬화·쓰기·집계)을 검증할 뿐 기하 계산 자체를 독립적으로 검증하지 않는다. F-8 기하의 독립 증거는 `results/f8_independent_check.json`(devkit 꼭짓점 + 면 반공간 재계산), `results/figures/f8_visual_check.png`(시각 점검), MySQL 생성 열 대조(`results/mysql_consistency.json`의 dist_m·diff_level·eligible_*·dist_bin 모순 0건)다.

필드별 단위·자료형은 `units.md`를 참고한다.
