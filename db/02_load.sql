-- F-10 적재: CSV(kitti.export 출력) → 스테이징(stg_*) → 본 테이블 UPSERT (README 7.2 "적재 (MySQL)")
--
-- {{CSV_DIR}}는 kitti.db_load가 CSV 폴더의 절대 경로로 바꾼다(작은따옴표는 이스케이프).
-- CSV: UTF-8, 헤더 1줄, 쉼표 구분, 따옴표 없음, NULL = \N, 부동소수는 왕복 가능한 최단 표기.
-- 서버·클라이언트 모두 local_infile이 켜져 있어야 한다(mysql --local-infile=1).
--
-- DontCare 무효 표식(-1, -10, -1000)은 여기서 NULL로 바꾸고, DontCare의 특징 행은 넣지 않는다.
-- 다시 실행해도 결과가 같다(멱등): 스테이징은 비우고 다시 채우며, 본 테이블은 UPSERT한다.
-- stg_* 내용은 다음 적재까지 남겨 04_checks.sql이 Spark 파생값과 대조한다.

SET NAMES utf8mb4;

TRUNCATE TABLE stg_frame;
TRUNCATE TABLE stg_gt_object;
TRUNCATE TABLE stg_object_feature;

LOAD DATA LOCAL INFILE '{{CSV_DIR}}/frame.csv' INTO TABLE stg_frame
  CHARACTER SET utf8mb4
  FIELDS TERMINATED BY ',' ESCAPED BY '\\'
  LINES TERMINATED BY '\n'
  IGNORE 1 LINES
  (frame_id, image_w, image_h, n_points, quality);

LOAD DATA LOCAL INFILE '{{CSV_DIR}}/gt_object.csv' INTO TABLE stg_gt_object
  CHARACTER SET utf8mb4
  FIELDS TERMINATED BY ',' ESCAPED BY '\\'
  LINES TERMINATED BY '\n'
  IGNORE 1 LINES
  (frame_id, object_id, class_name, truncated, occluded, alpha,
   bbox_left, bbox_top, bbox_right, bbox_bottom, dim_h, dim_w, dim_l,
   loc_x, loc_y, loc_z, rotation_y,
   spark_valid_3d, spark_dist_m, spark_diff_level,
   spark_eligible_easy, spark_eligible_moderate, spark_eligible_hard, spark_dist_bin);

LOAD DATA LOCAL INFILE '{{CSV_DIR}}/object_feature.csv' INTO TABLE stg_object_feature
  CHARACTER SET utf8mb4
  FIELDS TERMINATED BY ',' ESCAPED BY '\\'
  LINES TERMINATED BY '\n'
  IGNORE 1 LINES
  (frame_id, object_id, points_in_box);

START TRANSACTION;

-- ON DUPLICATE KEY UPDATE는 파생 테이블 열(src.*)을 참조한다(VALUES()는 8.0.20부터 사용 중단 예고).
INSERT INTO frame (frame_id, image_w, image_h, n_points, quality)
SELECT * FROM (
  SELECT s.frame_id, s.image_w, s.image_h, s.n_points, s.quality
  FROM stg_frame s
) AS src
ON DUPLICATE KEY UPDATE
  image_w = src.image_w, image_h = src.image_h,
  n_points = src.n_points, quality = src.quality;

-- 클래스 이름 → class_id. 매핑되지 않는 이름은 JOIN에서 빠지고 04_checks의 stg_unmapped_class로 드러난다.
INSERT INTO gt_object (frame_id, object_id, class_id, truncated, occluded, alpha,
                       bbox_left, bbox_top, bbox_right, bbox_bottom,
                       dim_h, dim_w, dim_l, loc_x, loc_y, loc_z, rotation_y)
SELECT * FROM (
  SELECT s.frame_id, s.object_id, c.class_id,
         CASE WHEN c.is_ignore THEN NULL ELSE s.truncated  END AS truncated,
         CASE WHEN c.is_ignore THEN NULL ELSE s.occluded   END AS occluded,
         CASE WHEN c.is_ignore THEN NULL ELSE s.alpha      END AS alpha,
         s.bbox_left, s.bbox_top, s.bbox_right, s.bbox_bottom,
         CASE WHEN c.is_ignore THEN NULL ELSE s.dim_h      END AS dim_h,
         CASE WHEN c.is_ignore THEN NULL ELSE s.dim_w      END AS dim_w,
         CASE WHEN c.is_ignore THEN NULL ELSE s.dim_l      END AS dim_l,
         CASE WHEN c.is_ignore THEN NULL ELSE s.loc_x      END AS loc_x,
         CASE WHEN c.is_ignore THEN NULL ELSE s.loc_y      END AS loc_y,
         CASE WHEN c.is_ignore THEN NULL ELSE s.loc_z      END AS loc_z,
         CASE WHEN c.is_ignore THEN NULL ELSE s.rotation_y END AS rotation_y
  FROM stg_gt_object s
  JOIN object_class c ON c.class_name = s.class_name
) AS src
ON DUPLICATE KEY UPDATE
  class_id = src.class_id, truncated = src.truncated, occluded = src.occluded,
  alpha = src.alpha, bbox_left = src.bbox_left, bbox_top = src.bbox_top,
  bbox_right = src.bbox_right, bbox_bottom = src.bbox_bottom,
  dim_h = src.dim_h, dim_w = src.dim_w, dim_l = src.dim_l,
  loc_x = src.loc_x, loc_y = src.loc_y, loc_z = src.loc_z,
  rotation_y = src.rotation_y;

-- DontCare가 아닌 객체의 특징만 넣는다.
INSERT INTO object_feature (frame_id, object_id, points_in_box)
SELECT * FROM (
  SELECT s.frame_id, s.object_id, s.points_in_box
  FROM stg_object_feature s
  JOIN gt_object o ON o.frame_id = s.frame_id AND o.object_id = s.object_id
  JOIN object_class c ON c.class_id = o.class_id AND c.is_ignore = FALSE
) AS src
ON DUPLICATE KEY UPDATE points_in_box = src.points_in_box;

COMMIT;
