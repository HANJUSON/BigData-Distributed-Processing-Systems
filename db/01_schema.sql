-- F-10 MySQL 서비스 DB: 최소 스키마 (spec Assumption 1)
--
-- DB 과목 제안서 3.2절·부록 SQL과 review_1_설계.md의 열 이름을 따른다. 이 파일은 F-10에
-- 필요한 부분(frame, object_class, gt_object, object_feature, analysis_threshold)과 적재용
-- 스테이징 테이블(stg_*)만 만든다. 검수·태그·프리셋 테이블은 DB 과목 범위다.
--
-- 다시 실행해도 안전하다(CREATE TABLE IF NOT EXISTS). 기존 행·사용자 데이터를 지우지 않는다(DB DR-4).
-- 실행: mysql --database=<db> < db/01_schema.sql   (kitti.db_load가 01→04 순서로 실행)

SET NAMES utf8mb4;

-- --------------------------------------------------------------------------
-- 코드 테이블: KITTI 클래스 9종. DontCare 여부는 클래스가 정하므로 여기 한 번만 둔다(3NF).
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS object_class (
  class_id   TINYINT UNSIGNED NOT NULL,
  class_name VARCHAR(20)      NOT NULL,
  is_ignore  BOOLEAN          NOT NULL DEFAULT FALSE,
  PRIMARY KEY (class_id),
  UNIQUE KEY uq_object_class_name (class_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- class_id는 kitti.config.KITTI_CLASSES 순서(1부터)와 같다.
INSERT INTO object_class (class_id, class_name, is_ignore) VALUES
  (1, 'Car', FALSE), (2, 'Van', FALSE), (3, 'Truck', FALSE),
  (4, 'Pedestrian', FALSE), (5, 'Person_sitting', FALSE), (6, 'Cyclist', FALSE),
  (7, 'Tram', FALSE), (8, 'Misc', FALSE), (9, 'DontCare', TRUE) AS new
ON DUPLICATE KEY UPDATE class_name = new.class_name, is_ignore = new.is_ignore;

-- --------------------------------------------------------------------------
-- 장면
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS frame (
  frame_id SMALLINT UNSIGNED NOT NULL,
  image_w  SMALLINT UNSIGNED NULL,
  image_h  SMALLINT UNSIGNED NULL,
  n_points INT UNSIGNED      NULL COMMENT 'velodyne .bin 레코드 수, 읽지 못하면 NULL',
  quality  ENUM('ok', 'missing_file', 'bad_value') NOT NULL DEFAULT 'ok',
  PRIMARY KEY (frame_id),
  CONSTRAINT ck_frame_id CHECK (frame_id BETWEEN 0 AND 7480)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------------------
-- 정답 객체: label_2의 15개 필드. DontCare의 무효 표식(-1, -10, -1000)은 NULL.
-- truncated는 DOUBLE로 둔다. 원본에 소수 셋째 자리 이상인 값이 있어(학습 세트 113개)
-- DECIMAL(3,2)로 반올림하면 난이도 경계(0.15/0.30/0.50) 판정이 Spark와 달라질 수 있다.
-- 생성 열(bbox_h, dist_m, diff_level, valid_3d)은 Spark 규칙과 같다(README F-7·F-8).
--   diff_level: 1 Easy, 2 Moderate, 3 Hard, NULL 해당 없음 (Spark diff_level 4 = NULL)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gt_object (
  frame_id    SMALLINT UNSIGNED NOT NULL,
  object_id   SMALLINT UNSIGNED NOT NULL COMMENT 'label_2 파일의 1부터 시작하는 원본 줄 번호',
  class_id    TINYINT UNSIGNED  NOT NULL,
  truncated   DOUBLE  NULL,
  occluded    TINYINT NULL,
  alpha       DOUBLE  NULL,
  bbox_left   DOUBLE  NOT NULL,
  bbox_top    DOUBLE  NOT NULL,
  bbox_right  DOUBLE  NOT NULL,
  bbox_bottom DOUBLE  NOT NULL,
  dim_h       DOUBLE  NULL,
  dim_w       DOUBLE  NULL,
  dim_l       DOUBLE  NULL,
  loc_x       DOUBLE  NULL,
  loc_y       DOUBLE  NULL,
  loc_z       DOUBLE  NULL,
  rotation_y  DOUBLE  NULL,
  bbox_h      DOUBLE AS (bbox_bottom - bbox_top) STORED,
  dist_m      DOUBLE AS (SQRT(loc_x * loc_x + loc_z * loc_z)) STORED,
  diff_level  TINYINT AS (CASE
      WHEN bbox_bottom - bbox_top >= 40 AND occluded = 0  AND truncated <= 0.15 THEN 1
      WHEN bbox_bottom - bbox_top >= 25 AND occluded <= 1 AND truncated <= 0.30 THEN 2
      WHEN bbox_bottom - bbox_top >= 25 AND occluded <= 2 AND truncated <= 0.50 THEN 3
    END) STORED,
  valid_3d    BOOLEAN AS (dim_h > 0 AND dim_w > 0 AND dim_l > 0 AND loc_z > 0) STORED,
  PRIMARY KEY (frame_id, object_id),
  KEY ix_gt_object_search (class_id, occluded, dist_m),
  CONSTRAINT fk_gt_object_frame FOREIGN KEY (frame_id) REFERENCES frame (frame_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_gt_object_class FOREIGN KEY (class_id) REFERENCES object_class (class_id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT ck_gt_truncated CHECK (truncated IS NULL OR truncated BETWEEN 0 AND 1),
  CONSTRAINT ck_gt_occluded  CHECK (occluded IS NULL OR occluded BETWEEN 0 AND 3),
  CONSTRAINT ck_gt_bbox      CHECK (bbox_left <= bbox_right AND bbox_top <= bbox_bottom)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------------------
-- Spark 계산 결과: 3D 박스 내부 포인트 수. NULL = 계산 불가(무효 3D), 0 = 포인트 없음.
-- DontCare 객체는 행이 없다(1:0..1).
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS object_feature (
  frame_id      SMALLINT UNSIGNED NOT NULL,
  object_id     SMALLINT UNSIGNED NOT NULL,
  points_in_box INT UNSIGNED NULL,
  PRIMARY KEY (frame_id, object_id),
  KEY ix_object_feature_points (points_in_box),
  CONSTRAINT fk_object_feature_gt FOREIGN KEY (frame_id, object_id)
    REFERENCES gt_object (frame_id, object_id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------------------
-- 분석 결과: 클래스별 관측 부족 기준값 (README F-9 = NTILE(10) 1번 구간의 최댓값)
-- sp_refresh_threshold(03_views_procs.sql)가 채운다.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_threshold (
  class_id    TINYINT UNSIGNED NOT NULL,
  low_points  INT UNSIGNED     NOT NULL,
  method      VARCHAR(20)      NOT NULL DEFAULT 'p10',
  computed_at DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (class_id),
  CONSTRAINT fk_threshold_class FOREIGN KEY (class_id) REFERENCES object_class (class_id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- --------------------------------------------------------------------------
-- 스테이징 (제약조건 없음). CSV를 그대로 받는다. stg_gt_object의 spark_* 열은 Spark가 계산한
-- 파생값으로, 본 테이블에는 넣지 않고 04_checks.sql에서 DB 생성 열과 대조하는 데만 쓴다.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stg_frame (
  frame_id INT, image_w INT, image_h INT, n_points BIGINT, quality VARCHAR(20)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stg_gt_object (
  frame_id INT, object_id INT, class_name VARCHAR(20),
  truncated DOUBLE, occluded INT, alpha DOUBLE,
  bbox_left DOUBLE, bbox_top DOUBLE, bbox_right DOUBLE, bbox_bottom DOUBLE,
  dim_h DOUBLE, dim_w DOUBLE, dim_l DOUBLE,
  loc_x DOUBLE, loc_y DOUBLE, loc_z DOUBLE, rotation_y DOUBLE,
  spark_valid_3d TINYINT, spark_dist_m DOUBLE, spark_diff_level INT,
  spark_eligible_easy TINYINT, spark_eligible_moderate TINYINT, spark_eligible_hard TINYINT,
  spark_dist_bin VARCHAR(8),
  KEY ix_stg_gt_object (frame_id, object_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stg_object_feature (
  frame_id INT, object_id INT, points_in_box BIGINT,
  KEY ix_stg_object_feature (frame_id, object_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
