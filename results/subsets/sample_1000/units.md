# F-11 대조 필드 단위·자료형

README F-11 "필드별 단위 기록". 좌표 변환과 집계는 float64로 계산한다.
자료형은 원래 표(Parquet·Spark 결과)의 자료형이다. CSV에서 읽을 때 NULL이 있는 정수 열은
float64로 읽어 비교한다(2^53 이하 정수는 정확히 표현되므로 '오차 0' 판정에 영향 없음).

- 실수 허용 오차: `abs(a-b) <= 1e-06 + 1e-06 × abs(b)` (a = Spark, b = 순차)
- 좌표계: 격자는 Velodyne(x 전방, y 좌측, z 위, 원점 = 센서), 객체 위치·거리는 카메라 rect 좌표
- 격자 필터: x, y, z 중 NaN/Inf인 점과 ROI 밖 점은 FLOOR 전에 뺀다(Spark GRID_SQL·numpy 동일)
- points_in_box: .bin이 없거나 크기 오류인 장면은 모든 객체가 NULL(사이드카 `_skipped_frames.json`)

## 생성 노트: 난이도 경계 (`eligible_*`, `diff_level`)

- 2D 박스 높이 기준은 `>=` 40/25 px(경계 포함)로 DB 과목 `diff_level`과 같다(README 요구).
- KITTI 공식 devkit 평가는 엄격한 `>`를 쓴다. 학습 세트에서 높이가 정확히 40 px 또는 25 px인
  실제 객체 17개는 devkit과 등급이 다를 수 있다(동작은 바꾸지 않고 기록만 한다).

## 같은 함수로 계산하는 열 (F-11 범위)

- object_features의 points_in_box·dist_m·난이도 열(eligible_easy/moderate/hard, diff_level, dist_bin)은 양쪽 모두 같은 순수 함수(kitti.features.features_from_arrays)로 계산한다. 따라서 F-11은 이 열들에 대해 Spark 배관(장면 분할·직렬화·쓰기·집계)을 검증할 뿐 기하 계산 자체를 독립적으로 검증하지 않는다. F-8 기하의 독립 증거는 `results/f8_independent_check.json`(devkit 꼭짓점 + 면 반공간 재계산), `results/figures/f8_visual_check.png`(시각 점검), MySQL 생성 열 대조(`results/mysql_consistency.json`의 dist_m·diff_level·eligible_*·dist_bin 모순 0건)다.

## 빈 대조·장면 집합 규칙

- 대조 행이 0인 항목은 실패. `f9_low_objects`·`f9_low_scenes`만 `f9_thresholds`가 1행 이상
  대조되고 `f9_low_objects` 대조 행 수가 `f9_thresholds` 행 수 이상일 때 비어 있어도 된다.
- `f9_thresholds`가 있으면 `f9_low_objects` 대조 행 수는 그 행 수 이상이어야 한다(클래스마다
  k번째 객체 자신이 기준값 이하).
- NULL 키는 조인 전에 표지값으로 바꿔 양쪽 같은 NULL 키끼리 맞춘다.
- 양쪽 grid_params.json의 `frame_set`(n, sha256)이 같아야 하며, 다르면 다른 항목은 대조하지 않는다.

## 객체 특징 표 (F-7·F-8) (`object_features`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `frame_id` | 키 | int32 | - | 키 집합 일치·중복 0 | 장면 ID |
| `object_id` | 키 | int32 | - | 키 집합 일치·중복 0 | label_2 원본 줄 번호(1부터) |
| `class` | 문자열 | string | - | 완전 일치 | 객체 종류 |
| `truncated` | 실수 | float64 | ratio (0-1) | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 잘림 비율, DontCare -1 |
| `occluded` | 정수 | int32 | level (0-3) | 오차 0 | 가림 정도, DontCare -1 |
| `alpha` | 실수 | float64 | rad | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 관측 각도 |
| `bbox_left` | 실수 | float64 | px | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 2D 박스 왼쪽 |
| `bbox_top` | 실수 | float64 | px | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 2D 박스 위쪽 |
| `bbox_right` | 실수 | float64 | px | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 2D 박스 오른쪽 |
| `bbox_bottom` | 실수 | float64 | px | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 2D 박스 아래쪽 |
| `h` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 3D 높이 |
| `w` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 3D 너비 |
| `l` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 3D 길이 |
| `x` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 박스 바닥 중심 x (카메라 rect 좌표) |
| `y` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 박스 바닥 중심 y (카메라 rect 좌표, 아래 +) |
| `z` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 박스 바닥 중심 z (카메라 rect 좌표, 전방) |
| `ry` | 실수 | float64 | rad | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | rotation_y |
| `is_dontcare` | 논리 | bool | - | 완전 일치 | class == DontCare |
| `valid_3d` | 논리 | bool | - | 완전 일치 | h,w,l > 0 and z > 0 and not DontCare |
| `dist_m` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 카메라 좌표 수평거리 sqrt(x²+z²), DontCare NULL |
| `bbox_height` | 실수 | float64 | px | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 2D 박스 높이 |
| `eligible_easy` | 논리 | bool | - | 완전 일치 | KITTI Easy 적격 |
| `eligible_moderate` | 논리 | bool | - | 완전 일치 | KITTI Moderate 적격 |
| `eligible_hard` | 논리 | bool | - | 완전 일치 | KITTI Hard 적격 |
| `diff_level` | 정수 | int32 | level (1-4) | 오차 0 | 난이도 등급 |
| `points_in_box` | 정수 | int32 | count (points) | 오차 0 | 3D 박스 내부 LiDAR 포인트 수, 무효 3D NULL |
| `dist_bin` | 문자열 | string | - | 완전 일치 | 거리 구간 (<10, <20, <40, ≥40 m) |

## 장면별 격자 통계 (F-6) (`grid_stats`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `frame_id` | 키 | int32 | - | 키 집합 일치·중복 0 | 장면 ID |
| `grid_x` | 키 | int32 | cell (1 m) | 키 집합 일치·중복 0 | floor((x-0)/1.0), Velodyne x |
| `grid_y` | 키 | int32 | cell (1 m) | 키 집합 일치·중복 0 | floor((y+40)/1.0), Velodyne y |
| `n_points` | 정수 | int64 | count (points) | 오차 0 | 격자 안 포인트 수 |
| `z_sum` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 격자 안 Velodyne z 합 |
| `z_min` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 격자 안 Velodyne z 최솟값 |
| `z_max` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 격자 안 Velodyne z 최댓값 |

## 클래스별 요약 (F-5) (`f5_per_class`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `class` | 키 | string | - | 키 집합 일치·중복 0 | 객체 종류(DontCare 포함) |
| `n_objects` | 정수 | int64 | count (objects) | 오차 0 | 객체 수 |
| `n_valid_3d` | 정수 | int64 | count (objects) | 오차 0 | 유효 3D 객체 수 |
| `mean_dist_m` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | AVG(CAST(dist_m AS DECIMAL(20,12))) → DOUBLE |
| `n_scenes` | 정수 | int64 | count (frames) | 오차 0 | 객체가 있는 장면 수 |
| `sum_points_in_box` | 정수 | int64 | count (points) | 오차 0 | 박스 내부 포인트 합, 전부 NULL이면 NULL |

## 전체 누적 격자 (F-6) (`grid_cumulative`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `grid_x` | 키 | int64 | cell (1 m) | 키 집합 일치·중복 0 | 격자 x 번호 |
| `grid_y` | 키 | int64 | cell (1 m) | 키 집합 일치·중복 0 | 격자 y 번호 |
| `x_min_m` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 격자 x 하한 |
| `y_min_m` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 격자 y 하한 |
| `n_points_sum` | 정수 | int64 | count (points) | 오차 0 | 모든 프레임 합 |
| `n_points_mean` | 실수 | float64 | points / frame | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 합 / 정상 처리 프레임 수, 빈 격자 0 |

## 정상 처리 프레임 수 (F-6 평균 분모) (`grid_params`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `param` | 키 | string | - | 키 집합 일치·중복 0 | 항목 이름 |
| `value` | 정수 | int64 | count (frames) | 오차 0 | n_frames_processed |

## 클래스 × 거리 구간 (F-7) (`f7_class_dist`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `class` | 키 | string | - | 키 집합 일치·중복 0 | 객체 종류 |
| `dist_bin` | 키 | string | - | 키 집합 일치·중복 0 | 거리 구간 |
| `n_objects` | 정수 | int64 | count (objects) | 오차 0 | 객체 수 |
| `n_valid_3d` | 정수 | int64 | count (objects) | 오차 0 | 유효 3D 객체 수 |
| `sum_points_in_box` | 정수 | int64 | count (points) | 오차 0 | 박스 내부 포인트 합, 전부 NULL이면 NULL |

## 클래스 × 가림 (F-7) (`f7_class_occluded`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `class` | 키 | string | - | 키 집합 일치·중복 0 | 객체 종류 |
| `occluded` | 키 | int32 | level (0-3) | 키 집합 일치·중복 0 | 가림 |
| `n_objects` | 정수 | int64 | count (objects) | 오차 0 | 객체 수 |
| `n_valid_3d` | 정수 | int64 | count (objects) | 오차 0 | 유효 3D 객체 수 |
| `sum_points_in_box` | 정수 | int64 | count (points) | 오차 0 | 박스 내부 포인트 합, 전부 NULL이면 NULL |

## 클래스 × 잘림 구간 (F-7) (`f7_class_truncated`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `class` | 키 | string | - | 키 집합 일치·중복 0 | 객체 종류 |
| `trunc_bucket` | 키 | string | ratio bucket | 키 집합 일치·중복 0 | 잘림 구간('invalid' 포함) |
| `n_objects` | 정수 | int64 | count (objects) | 오차 0 | 객체 수 |
| `n_valid_3d` | 정수 | int64 | count (objects) | 오차 0 | 유효 3D 객체 수 |
| `sum_points_in_box` | 정수 | int64 | count (points) | 오차 0 | 박스 내부 포인트 합, 전부 NULL이면 NULL |

## 클래스별 난이도 적격 (F-7) (`f7_class_difficulty`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `class` | 키 | string | - | 키 집합 일치·중복 0 | 객체 종류 |
| `n_objects` | 정수 | int64 | count (objects) | 오차 0 | 객체 수 |
| `n_eligible_easy` | 정수 | int64 | count (objects) | 오차 0 | Easy 적격 수 |
| `n_eligible_moderate` | 정수 | int64 | count (objects) | 오차 0 | Moderate 적격 수 |
| `n_eligible_hard` | 정수 | int64 | count (objects) | 오차 0 | Hard 적격 수 |
| `n_not_eligible` | 정수 | int64 | count (objects) | 오차 0 | diff_level = 4 수 |

## 클래스별 관측 부족 기준값 (F-9) (`f9_thresholds`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `class` | 키 | string | - | 키 집합 일치·중복 0 | 객체 종류 |
| `n` | 정수 | int64 | count (objects) | 오차 0 | 유효 3D 객체 수 |
| `k` | 정수 | int32 | rank | 오차 0 | ceil(n/10) |
| `threshold` | 정수 | int32 | count (points) | 오차 0 | 오름차순 k번째 points_in_box |

## 관측 부족 객체 목록 (F-9) (`f9_low_objects`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `frame_id` | 키 | int32 | - | 키 집합 일치·중복 0 | 장면 ID |
| `object_id` | 키 | int32 | - | 키 집합 일치·중복 0 | 객체 줄 번호 |
| `class` | 문자열 | string | - | 완전 일치 | 객체 종류 |
| `points_in_box` | 정수 | int32 | count (points) | 오차 0 | 박스 내부 포인트 수 |
| `threshold` | 정수 | int32 | count (points) | 오차 0 | 클래스 기준값 |
| `dist_m` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 수평거리 |
| `dist_bin` | 문자열 | string | - | 완전 일치 | 거리 구간 |
| `occluded` | 정수 | int32 | level (0-3) | 오차 0 | 가림 |
| `truncated` | 실수 | float64 | ratio (0-1) | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 잘림 |
| `diff_level` | 정수 | int32 | level (1-4) | 오차 0 | 난이도 등급 |
| `h` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 3D 높이 |
| `w` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 3D 너비 |
| `l` | 실수 | float64 | m | abs(a-b) ≤ 1e-06 + 1e-06·abs(b) | 3D 길이 |

## 관측 부족 장면 목록 (F-9) (`f9_low_scenes`)

| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `frame_id` | 키 | int32 | - | 키 집합 일치·중복 0 | 장면 ID |
| `n_low_objects` | 정수 | int64 | count (objects) | 오차 0 | 장면의 관측 부족 객체 수 |
| `classes` | 문자열 | string | - | 완전 일치 | 관측 부족 클래스(정렬, ';' 연결) |
