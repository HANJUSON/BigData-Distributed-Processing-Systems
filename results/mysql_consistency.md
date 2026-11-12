# Spark ↔ MySQL 적재 대조 (F-10·F-11)

- 생성: 2026-11-05T05:48:48+00:00
- 기대값: `~/data/kitti/work/mysql_csv/expected.json`
- 판정: **통과** (62 / 62 항목 일치, 불일치 0건)

기대값은 `kitti.export`가 Parquet(Spark 결과)에서 계산했고, 실제값은 `db/04_checks.sql` 결과다.
기준값은 클래스별 NTILE(10) 1번 구간 최댓값(= 정렬 후 ceil(n/10)번째 값)이다.

## 행 수

| 항목 | 기대값 | MySQL | 결과 |
| --- | ---: | ---: | --- |
| `analysis_threshold` | 8 | 8 | 일치 |
| `frame` | 7481 | 7481 | 일치 |
| `gt_object` | 51865 | 51865 | 일치 |
| `object_class` | 9 | 9 | 일치 |
| `object_feature` | 40570 | 40570 | 일치 |
| `stg_frame` | 7481 | 7481 | 일치 |
| `stg_gt_object` | 51865 | 51865 | 일치 |
| `stg_object_feature` | 40570 | 40570 | 일치 |

## NULL 수

| 항목 | 기대값 | MySQL | 결과 |
| --- | ---: | ---: | --- |
| `frame.image_h` | 0 | 0 | 일치 |
| `frame.image_w` | 0 | 0 | 일치 |
| `frame.n_points` | 0 | 0 | 일치 |
| `gt_object.alpha` | 11295 | 11295 | 일치 |
| `gt_object.bbox_bottom` | 0 | 0 | 일치 |
| `gt_object.bbox_h` | 0 | 0 | 일치 |
| `gt_object.bbox_left` | 0 | 0 | 일치 |
| `gt_object.bbox_right` | 0 | 0 | 일치 |
| `gt_object.bbox_top` | 0 | 0 | 일치 |
| `gt_object.diff_level` | 20514 | 20514 | 일치 |
| `gt_object.dim_h` | 11295 | 11295 | 일치 |
| `gt_object.dim_l` | 11295 | 11295 | 일치 |
| `gt_object.dim_w` | 11295 | 11295 | 일치 |
| `gt_object.dist_m` | 11295 | 11295 | 일치 |
| `gt_object.loc_x` | 11295 | 11295 | 일치 |
| `gt_object.loc_y` | 11295 | 11295 | 일치 |
| `gt_object.loc_z` | 11295 | 11295 | 일치 |
| `gt_object.occluded` | 11295 | 11295 | 일치 |
| `gt_object.rotation_y` | 11295 | 11295 | 일치 |
| `gt_object.truncated` | 11295 | 11295 | 일치 |
| `gt_object.valid_3d` | 11295 | 11295 | 일치 |
| `object_feature.points_in_box` | 8 | 8 | 일치 |

## 클래스별 기준값

| 항목 | 기대값 | MySQL | 결과 |
| --- | ---: | ---: | --- |
| `Car` | 11 | 11 | 일치 |
| `Cyclist` | 14 | 14 | 일치 |
| `Misc` | 25 | 25 | 일치 |
| `Pedestrian` | 19 | 19 | 일치 |
| `Person_sitting` | 37 | 37 | 일치 |
| `Tram` | 3 | 3 | 일치 |
| `Truck` | 49 | 49 | 일치 |
| `Van` | 20 | 20 | 일치 |

## 기준값 계산 대상 객체 수

| 항목 | 기대값 | MySQL | 결과 |
| --- | ---: | ---: | --- |
| `Car` | 28741 | 28741 | 일치 |
| `Cyclist` | 1627 | 1627 | 일치 |
| `Misc` | 972 | 972 | 일치 |
| `Pedestrian` | 4487 | 4487 | 일치 |
| `Person_sitting` | 222 | 222 | 일치 |
| `Tram` | 510 | 510 | 일치 |
| `Truck` | 1091 | 1091 | 일치 |
| `Van` | 2912 | 2912 | 일치 |

## 모순 건수 (0이어야 함)

| 항목 | 기대값 | MySQL | 결과 |
| --- | ---: | ---: | --- |
| `diff_level_mismatch` | 0 | 0 | 일치 |
| `dist_bin_mismatch` | 0 | 0 | 일치 |
| `dist_m_mismatch` | 0 | 0 | 일치 |
| `dist_m_null_mismatch` | 0 | 0 | 일치 |
| `dontcare_feature` | 0 | 0 | 일치 |
| `eligible_easy_vs_diff_level` | 0 | 0 | 일치 |
| `eligible_hard_vs_diff_level` | 0 | 0 | 일치 |
| `eligible_moderate_vs_diff_level` | 0 | 0 | 일치 |
| `invalid_but_value` | 0 | 0 | 일치 |
| `missing_feature` | 0 | 0 | 일치 |
| `points_in_box_mismatch` | 0 | 0 | 일치 |
| `stg_gt_object_not_loaded` | 0 | 0 | 일치 |
| `stg_object_feature_not_loaded` | 0 | 0 | 일치 |
| `stg_unmapped_class` | 0 | 0 | 일치 |
| `valid_3d_mismatch` | 0 | 0 | 일치 |
| `valid_but_null` | 0 | 0 | 일치 |

## 참고값 (비교하지 않음)

- `dist_m_exact_mismatch`: 0
- `dist_m_max_abs_diff`: 0
