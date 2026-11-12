-- F-10/F-11 Spark↔MySQL 적재 대조 질의 (spec "Critic 반영 결정" 6)
--
-- 결과는 한 결과 집합, 세 열(section, item, value)이다. kitti.db_load가
--   mysql --batch --raw --skip-column-names
-- 로 실행해 탭 구분 행을 읽고, kitti.export가 Parquet에서 만든 expected.json과 비교한다.
--   row_count     : 테이블 행 수
--   null_count    : <table>.<column>의 NULL 수
--   threshold     : 클래스별 기준값 (analysis_threshold, NTILE(10) 1번 구간 최댓값)
--   threshold_n   : 기준값 계산 대상 객체 수
--   contradiction : 0이어야 하는 모순 건수
--   info          : 비교하지 않는 참고값
-- 모든 value는 문자열로 바꿔 내보낸다(UNION 형 맞춤).

SET NAMES utf8mb4;

SELECT section, item, value FROM (
  -- ---------------------------------------------------------------- 행 수
  SELECT 1 AS ord, 'row_count' AS section, 'frame' AS item, CAST(COUNT(*) AS CHAR) AS value FROM frame
  UNION ALL SELECT 1, 'row_count', 'object_class',       CAST(COUNT(*) AS CHAR) FROM object_class
  UNION ALL SELECT 1, 'row_count', 'gt_object',          CAST(COUNT(*) AS CHAR) FROM gt_object
  UNION ALL SELECT 1, 'row_count', 'object_feature',     CAST(COUNT(*) AS CHAR) FROM object_feature
  UNION ALL SELECT 1, 'row_count', 'analysis_threshold', CAST(COUNT(*) AS CHAR) FROM analysis_threshold
  UNION ALL SELECT 1, 'row_count', 'stg_frame',          CAST(COUNT(*) AS CHAR) FROM stg_frame
  UNION ALL SELECT 1, 'row_count', 'stg_gt_object',      CAST(COUNT(*) AS CHAR) FROM stg_gt_object
  UNION ALL SELECT 1, 'row_count', 'stg_object_feature', CAST(COUNT(*) AS CHAR) FROM stg_object_feature

  -- ---------------------------------------------------------------- NULL 수
  UNION ALL SELECT 2, 'null_count', 'frame.image_w',  CAST(COALESCE(SUM(image_w IS NULL), 0)   AS CHAR) FROM frame
  UNION ALL SELECT 2, 'null_count', 'frame.image_h',  CAST(COALESCE(SUM(image_h IS NULL), 0)   AS CHAR) FROM frame
  UNION ALL SELECT 2, 'null_count', 'frame.n_points', CAST(COALESCE(SUM(n_points IS NULL), 0)  AS CHAR) FROM frame
  UNION ALL SELECT 2, 'null_count', 'gt_object.truncated',   CAST(COALESCE(SUM(truncated IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.occluded',    CAST(COALESCE(SUM(occluded IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.alpha',       CAST(COALESCE(SUM(alpha IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.bbox_left',   CAST(COALESCE(SUM(bbox_left IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.bbox_top',    CAST(COALESCE(SUM(bbox_top IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.bbox_right',  CAST(COALESCE(SUM(bbox_right IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.bbox_bottom', CAST(COALESCE(SUM(bbox_bottom IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.dim_h',       CAST(COALESCE(SUM(dim_h IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.dim_w',       CAST(COALESCE(SUM(dim_w IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.dim_l',       CAST(COALESCE(SUM(dim_l IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.loc_x',       CAST(COALESCE(SUM(loc_x IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.loc_y',       CAST(COALESCE(SUM(loc_y IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.loc_z',       CAST(COALESCE(SUM(loc_z IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.rotation_y',  CAST(COALESCE(SUM(rotation_y IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.bbox_h',      CAST(COALESCE(SUM(bbox_h IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.dist_m',      CAST(COALESCE(SUM(dist_m IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.diff_level',  CAST(COALESCE(SUM(diff_level IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'gt_object.valid_3d',    CAST(COALESCE(SUM(valid_3d IS NULL), 0)  AS CHAR) FROM gt_object
  UNION ALL SELECT 2, 'null_count', 'object_feature.points_in_box',
                   CAST(COALESCE(SUM(points_in_box IS NULL), 0)  AS CHAR) FROM object_feature

  -- ---------------------------------------------------------------- 기준값 (F-9)
  UNION ALL
  SELECT 3, 'threshold', c.class_name, CAST(t.low_points AS CHAR)
  FROM analysis_threshold t JOIN object_class c ON c.class_id = t.class_id
  UNION ALL
  SELECT 3, 'threshold_n', c.class_name, CAST(COUNT(*) AS CHAR)
  FROM gt_object o
  JOIN object_class c   ON c.class_id = o.class_id AND c.is_ignore = FALSE
  JOIN object_feature f ON f.frame_id = o.frame_id AND f.object_id = o.object_id
  WHERE o.valid_3d AND f.points_in_box IS NOT NULL
  GROUP BY c.class_name

  -- ---------------------------------------------------------------- 모순 (모두 0이어야 함)
  -- 적재 누락: 스테이징 행이 본 테이블에 들어가지 않음
  UNION ALL
  SELECT 4, 'contradiction', 'stg_unmapped_class', CAST(COUNT(*) AS CHAR)
  FROM stg_gt_object s LEFT JOIN object_class c ON c.class_name = s.class_name
  WHERE c.class_id IS NULL
  UNION ALL
  SELECT 4, 'contradiction', 'stg_gt_object_not_loaded', CAST(COUNT(*) AS CHAR)
  FROM stg_gt_object s
  LEFT JOIN gt_object o ON o.frame_id = s.frame_id AND o.object_id = s.object_id
  WHERE o.frame_id IS NULL
  UNION ALL
  SELECT 4, 'contradiction', 'stg_object_feature_not_loaded', CAST(COUNT(*) AS CHAR)
  FROM stg_object_feature s
  LEFT JOIN object_feature f ON f.frame_id = s.frame_id AND f.object_id = s.object_id
  WHERE f.frame_id IS NULL
  UNION ALL
  SELECT 4, 'contradiction', 'points_in_box_mismatch', CAST(COUNT(*) AS CHAR)
  FROM stg_object_feature s
  JOIN object_feature f ON f.frame_id = s.frame_id AND f.object_id = s.object_id
  WHERE NOT (f.points_in_box <=> s.points_in_box)

  -- 유효·NULL 모순 (DB DR-2): 유효 3D인데 NULL, 무효인데 값 있음, 특징 행 누락, DontCare 특징 행
  UNION ALL
  SELECT 4, 'contradiction', 'valid_but_null', CAST(COALESCE(SUM(o.valid_3d AND f.frame_id IS NOT NULL AND f.points_in_box IS NULL), 0) AS CHAR)
  FROM gt_object o
  JOIN object_class c ON c.class_id = o.class_id AND c.is_ignore = FALSE
  LEFT JOIN object_feature f ON f.frame_id = o.frame_id AND f.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'invalid_but_value', CAST(COALESCE(SUM(NOT o.valid_3d AND f.points_in_box IS NOT NULL), 0) AS CHAR)
  FROM gt_object o
  JOIN object_class c ON c.class_id = o.class_id AND c.is_ignore = FALSE
  LEFT JOIN object_feature f ON f.frame_id = o.frame_id AND f.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'missing_feature', CAST(COALESCE(SUM(f.frame_id IS NULL), 0) AS CHAR)
  FROM gt_object o
  JOIN object_class c ON c.class_id = o.class_id AND c.is_ignore = FALSE
  LEFT JOIN object_feature f ON f.frame_id = o.frame_id AND f.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'dontcare_feature', CAST(COUNT(*) AS CHAR)
  FROM object_feature f
  JOIN gt_object o    ON o.frame_id = f.frame_id AND o.object_id = f.object_id
  JOIN object_class c ON c.class_id = o.class_id AND c.is_ignore = TRUE

  -- DB 생성 열 ↔ Spark 파생값 (stg_gt_object.spark_*)
  UNION ALL
  SELECT 4, 'contradiction', 'valid_3d_mismatch', CAST(COALESCE(SUM(COALESCE(o.valid_3d, 0) <> s.spark_valid_3d), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'diff_level_mismatch', CAST(COALESCE(SUM(COALESCE(o.diff_level, 4) <> s.spark_diff_level), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'eligible_easy_vs_diff_level', CAST(COALESCE(SUM(s.spark_eligible_easy <> (COALESCE(o.diff_level, 4) = 1)), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'eligible_moderate_vs_diff_level', CAST(COALESCE(SUM(s.spark_eligible_moderate <> (COALESCE(o.diff_level, 4) <= 2)), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'eligible_hard_vs_diff_level', CAST(COALESCE(SUM(s.spark_eligible_hard <> (COALESCE(o.diff_level, 4) <= 3)), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'dist_m_null_mismatch', CAST(COALESCE(SUM((o.dist_m IS NULL) <> (s.spark_dist_m IS NULL)), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'dist_m_mismatch', CAST(COALESCE(SUM(ABS(o.dist_m - s.spark_dist_m) > 1e-9), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
  UNION ALL
  SELECT 4, 'contradiction', 'dist_bin_mismatch', CAST(COALESCE(SUM(NOT (fn_dist_bin(o.dist_m) <=> s.spark_dist_bin)), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id

  -- ---------------------------------------------------------------- 참고값 (비교하지 않음)
  UNION ALL
  SELECT 5, 'info', 'dist_m_exact_mismatch', CAST(COALESCE(SUM(o.dist_m <> s.spark_dist_m), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
  UNION ALL
  SELECT 5, 'info', 'dist_m_max_abs_diff', CAST(COALESCE(MAX(ABS(o.dist_m - s.spark_dist_m)), 0) AS CHAR)
  FROM gt_object o JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
) checks
ORDER BY ord, section, item;
