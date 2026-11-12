-- F-10 함수·뷰·프로시저 (DB 과목 제안서 부록 SQL과 같은 이름·시그니처) + 웹 화면용 프로시저
--
-- 저장 함수를 만들려면 바이너리 로그가 켜진 서버에서 root가 한 번
--   SET GLOBAL log_bin_trust_function_creators = 1;
-- 을 실행해 두어야 한다(DB 제안서 3.5절).
-- 다시 실행해도 안전하다(DROP … IF EXISTS 후 생성). 마지막에 기준값을 다시 계산한다.

SET NAMES utf8mb4;

-- --------------------------------------------------------------------------
-- 거리 구간: README F-7·spec Critic 7과 같은 <10, <20, <40, >=40 m.
-- 반환값은 Spark object_features.dist_bin과 같은 문자열이라 그대로 대조할 수 있다.
-- --------------------------------------------------------------------------
DROP FUNCTION IF EXISTS fn_dist_bin;
DELIMITER //
CREATE FUNCTION fn_dist_bin(d DOUBLE) RETURNS VARCHAR(8) DETERMINISTIC NO SQL
BEGIN
  RETURN CASE WHEN d IS NULL THEN NULL
              WHEN d < 10 THEN '0-10'
              WHEN d < 20 THEN '10-20'
              WHEN d < 40 THEN '20-40'
              ELSE '40+' END;
END //
DELIMITER ;

-- --------------------------------------------------------------------------
-- 객체별 최신 판정: DB 과목이 review 테이블과 함께 실제 정의를 만든다.
-- 이 최소 스키마에는 review가 없으므로, 뷰가 아직 없을 때만 빈 결과의 자리표시 뷰를 만든다
-- (sp_search_scenes가 참조하므로 필요). 이미 있으면 DB 과목 정의를 그대로 둔다.
-- --------------------------------------------------------------------------
SET @has_review_summary := (
  SELECT COUNT(*) FROM information_schema.tables
  WHERE table_schema = DATABASE() AND table_name = 'v_object_review_summary');
SET @ddl := IF(@has_review_summary = 0,
  'CREATE VIEW v_object_review_summary AS SELECT o.frame_id, o.object_id FROM gt_object o WHERE FALSE',
  'DO 0');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- --------------------------------------------------------------------------
-- 분석 대상 객체와 관측 부족 여부: 검색·대기열·분석이 공유하는 정의 (제안서 부록과 같음)
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_object_obs AS
SELECT o.frame_id, o.object_id, o.class_id, o.occluded, o.truncated,
       o.dist_m, fn_dist_bin(o.dist_m) AS dist_bin, f.points_in_box,
       (f.points_in_box <= t.low_points) AS is_low
FROM gt_object o
JOIN object_class c   ON c.class_id = o.class_id AND c.is_ignore = FALSE
JOIN object_feature f ON (f.frame_id, f.object_id) = (o.frame_id, o.object_id)
LEFT JOIN analysis_threshold t ON t.class_id = o.class_id
WHERE f.points_in_box IS NOT NULL;

-- --------------------------------------------------------------------------
-- 관측 부족 기준값 (README F-9, spec Critic 6)
-- 클래스별 유효 3D 객체(points_in_box IS NOT NULL)를 포인트 수 오름차순으로 NTILE(10)에 나누고
-- 1번 구간의 최댓값을 기준값으로 한다. = 정렬했을 때 ceil(n/10)번째 값 (Spark와 같은 정의).
-- PERCENT_RANK() <= 0.10 정의는 쓰지 않는다.
-- --------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_refresh_threshold;
DELIMITER //
CREATE PROCEDURE sp_refresh_threshold()
BEGIN
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;
  START TRANSACTION;
    DELETE FROM analysis_threshold;
    INSERT INTO analysis_threshold (class_id, low_points, method, computed_at)
    WITH ranked AS (
      SELECT o.class_id, f.points_in_box,
             NTILE(10) OVER (PARTITION BY o.class_id ORDER BY f.points_in_box) AS bucket
      FROM gt_object o
      JOIN object_class c   ON c.class_id = o.class_id AND c.is_ignore = FALSE
      JOIN object_feature f ON f.frame_id = o.frame_id AND f.object_id = o.object_id
      WHERE o.valid_3d AND f.points_in_box IS NOT NULL
    )
    SELECT class_id, MAX(points_in_box), 'ntile10_b1', NOW()
    FROM ranked
    WHERE bucket = 1
    GROUP BY class_id;
  COMMIT;
  SELECT c.class_name, t.low_points, t.method, t.computed_at
  FROM analysis_threshold t JOIN object_class c USING (class_id)
  ORDER BY t.class_id;
END //
DELIMITER ;

-- --------------------------------------------------------------------------
-- 조건 검색 (DB 제안서 부록과 같은 시그니처). 입력하지 않은 조건은 NULL, 가림 기본 범위는 0~2.
-- 검색 의미: 한 객체가 모든 조건을 만족하는 장면, frame_id 중복 없음.
-- p_low_only·p_unreviewed가 NULL이면 FALSE로 본다.
-- --------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_search_scenes;
DELIMITER //
CREATE PROCEDURE sp_search_scenes(IN p_class TINYINT, IN p_max_dist DOUBLE,
    IN p_occ_min TINYINT, IN p_occ_max TINYINT, IN p_max_points INT,
    IN p_low_only BOOLEAN, IN p_unreviewed BOOLEAN, IN p_limit INT, IN p_offset INT)
BEGIN
  SELECT v.frame_id, COUNT(*) AS matched_objects, MIN(v.points_in_box) AS min_points
  FROM v_object_obs v
  LEFT JOIN v_object_review_summary r ON (r.frame_id, r.object_id) = (v.frame_id, v.object_id)
  WHERE (p_class IS NULL OR v.class_id = p_class)
    AND (p_max_dist IS NULL OR v.dist_m <= p_max_dist)
    AND v.occluded BETWEEN COALESCE(p_occ_min, 0) AND COALESCE(p_occ_max, 2)
    AND (p_max_points IS NULL OR v.points_in_box <= p_max_points)
    AND (NOT COALESCE(p_low_only, FALSE) OR v.is_low)
    AND (NOT COALESCE(p_unreviewed, FALSE) OR r.frame_id IS NULL)
  GROUP BY v.frame_id
  ORDER BY matched_objects DESC, v.frame_id
  LIMIT p_limit OFFSET p_offset;
END //
DELIMITER ;

-- --------------------------------------------------------------------------
-- 웹 화면용 프로시저 (README 9.4 S1·S2, DB 과목 README NR-1 표)
-- 화면이 보여주는 수(결과 목록, 결과 수, 조건 완화 제안 수, 카드 정보, 장면 상세)는 모두 여기서 계산한다.
-- Flask는 CALL과 표시만 한다. sp_search_scenes(DB 과목 제출 시그니처)는 그대로 두고, README 9.4 S1의
-- 조건을 모두 받는 확장 검색 sp_search_scenes_ex를 따로 둔다.
--
-- 확장 검색 조건 (sp_search_scenes_ex · sp_search_summary · sp_scene_cards 공통, 의미가 같아야 한다)
--   p_classes    JSON 배열의 class_id (예: '[4,6]'). NULL 또는 '[]' = 모든 클래스
--   p_min_dist   거리(m) 하한, 포함. NULL = 없음      p_max_dist  거리 상한, 포함. NULL = 없음
--   p_occ        JSON 배열의 가림 값 집합 (예: '[0,2]'). NULL 또는 '[]' = 기본 0~2 (sp_search_scenes와 같음)
--   p_min_points 포인트 수 하한, 포함. NULL = 없음    p_max_points 상한, 포함. NULL = 없음
--   p_low_only   관측 부족 객체만     p_unreviewed 판정이 없는 객체만 (NULL = FALSE)
-- 검색 의미: 한 객체가 모든 조건을 만족하는 장면 (sp_search_scenes와 같음).
-- 불변식: p_classes='[c]', p_min_dist=NULL, p_occ='[a..b]', p_min_points=NULL, p_sort='match'이면
--         sp_search_scenes(c, max_dist, a, b, max_points, low, unrev, limit, offset)와 같은 행·같은 순서다.
-- --------------------------------------------------------------------------

-- 확장 검색: 장면 목록. p_sort = match(일치 객체 많은 순, 기본) | far(일치 객체의 최대 거리 먼 순)
--   | few_points(일치 객체의 최소 포인트 수 적은 순) | frame(장면 번호 순). 동점은 frame_id 오름차순.
-- 앞 세 열(frame_id, matched_objects, min_points)은 sp_search_scenes와 같은 정의이고,
-- nearest_m·farthest_m은 일치 객체의 최소·최대 거리다.
DROP PROCEDURE IF EXISTS sp_search_scenes_ex;
DELIMITER //
CREATE PROCEDURE sp_search_scenes_ex(IN p_classes JSON, IN p_min_dist DOUBLE, IN p_max_dist DOUBLE,
    IN p_occ JSON, IN p_min_points INT, IN p_max_points INT,
    IN p_low_only BOOLEAN, IN p_unreviewed BOOLEAN, IN p_sort VARCHAR(16), IN p_limit INT, IN p_offset INT)
BEGIN
  IF COALESCE(p_sort, 'match') NOT IN ('match', 'far', 'few_points', 'frame') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'p_sort must be match, far, few_points or frame';
  END IF;
  SELECT v.frame_id, COUNT(*) AS matched_objects, MIN(v.points_in_box) AS min_points,
         MIN(v.dist_m) AS nearest_m, MAX(v.dist_m) AS farthest_m
  FROM v_object_obs v
  LEFT JOIN v_object_review_summary r ON (r.frame_id, r.object_id) = (v.frame_id, v.object_id)
  WHERE (p_classes IS NULL OR JSON_LENGTH(p_classes) = 0 OR v.class_id MEMBER OF (p_classes))
    AND (p_min_dist IS NULL OR v.dist_m >= p_min_dist)
    AND (p_max_dist IS NULL OR v.dist_m <= p_max_dist)
    AND (CASE WHEN p_occ IS NULL OR JSON_LENGTH(p_occ) = 0 THEN v.occluded BETWEEN 0 AND 2
              ELSE v.occluded MEMBER OF (p_occ) END)
    AND (p_min_points IS NULL OR v.points_in_box >= p_min_points)
    AND (p_max_points IS NULL OR v.points_in_box <= p_max_points)
    AND (NOT COALESCE(p_low_only, FALSE) OR v.is_low)
    AND (NOT COALESCE(p_unreviewed, FALSE) OR r.frame_id IS NULL)
  GROUP BY v.frame_id
  ORDER BY CASE WHEN COALESCE(p_sort, 'match') = 'match' THEN COUNT(*) END DESC,
           CASE WHEN p_sort = 'far' THEN MAX(v.dist_m) END DESC,
           CASE WHEN p_sort = 'few_points' THEN MIN(v.points_in_box) END ASC,
           v.frame_id
  LIMIT p_limit OFFSET p_offset;
END //
DELIMITER ;

-- 검색 요약: 결과 수(장면 N개 · 객체 M개)와, 적용한 조건을 하나씩 풀었을 때의 장면 수.
-- 결과 집합 1: total_scenes, total_objects (sp_search_scenes_ex 전체 행 수·matched_objects 합과 같다)
-- 결과 집합 2: cond, scenes — 적용한 조건만, 풀었을 때 장면이 많은 순.
--   cond = class | dist(하한·상한 함께) | occ(기본 0~2로) | points(하한·상한 함께) | low_only | unreviewed
DROP PROCEDURE IF EXISTS sp_search_summary;
DELIMITER //
CREATE PROCEDURE sp_search_summary(IN p_classes JSON, IN p_min_dist DOUBLE, IN p_max_dist DOUBLE,
    IN p_occ JSON, IN p_min_points INT, IN p_max_points INT,
    IN p_low_only BOOLEAN, IN p_unreviewed BOOLEAN)
BEGIN
  DECLARE v_scenes, v_objects, r_class, r_dist, r_occ, r_points, r_low, r_unrev INT;
  -- 객체 → 장면 한 번만 묶는다(장면별 ‘조건을 만족하는 객체가 있나’ 플래그), 그다음 장면 수를 센다.
  SELECT COALESCE(SUM(f_all), 0), COALESCE(SUM(n_all), 0),
         COALESCE(SUM(f_class), 0), COALESCE(SUM(f_dist), 0), COALESCE(SUM(f_occ), 0),
         COALESCE(SUM(f_points), 0), COALESCE(SUM(f_low), 0), COALESCE(SUM(f_unrev), 0)
  INTO v_scenes, v_objects, r_class, r_dist, r_occ, r_points, r_low, r_unrev
  FROM (
    SELECT m.frame_id,
           MAX(c_class AND c_dist AND c_occ AND c_points AND c_low AND c_unrev) AS f_all,
           SUM(c_class AND c_dist AND c_occ AND c_points AND c_low AND c_unrev) AS n_all,
           MAX(c_dist AND c_occ AND c_points AND c_low AND c_unrev) AS f_class,
           MAX(c_class AND c_occ AND c_points AND c_low AND c_unrev) AS f_dist,
           MAX(c_class AND c_dist AND c_occ_default AND c_points AND c_low AND c_unrev) AS f_occ,
           MAX(c_class AND c_dist AND c_occ AND c_low AND c_unrev) AS f_points,
           MAX(c_class AND c_dist AND c_occ AND c_points AND c_unrev) AS f_low,
           MAX(c_class AND c_dist AND c_occ AND c_points AND c_low) AS f_unrev
    FROM (
      SELECT v.frame_id,
             (p_classes IS NULL OR JSON_LENGTH(p_classes) = 0 OR v.class_id MEMBER OF (p_classes)) AS c_class,
             ((p_min_dist IS NULL OR v.dist_m >= p_min_dist)
              AND (p_max_dist IS NULL OR v.dist_m <= p_max_dist)) AS c_dist,
             (CASE WHEN p_occ IS NULL OR JSON_LENGTH(p_occ) = 0 THEN v.occluded BETWEEN 0 AND 2
                   ELSE v.occluded MEMBER OF (p_occ) END) AS c_occ,
             (v.occluded BETWEEN 0 AND 2) AS c_occ_default,
             ((p_min_points IS NULL OR v.points_in_box >= p_min_points)
              AND (p_max_points IS NULL OR v.points_in_box <= p_max_points)) AS c_points,
             (NOT COALESCE(p_low_only, FALSE) OR COALESCE(v.is_low, FALSE)) AS c_low,
             (NOT COALESCE(p_unreviewed, FALSE) OR r.frame_id IS NULL) AS c_unrev
      FROM v_object_obs v
      LEFT JOIN v_object_review_summary r ON (r.frame_id, r.object_id) = (v.frame_id, v.object_id)
    ) m
    GROUP BY m.frame_id
  ) per_frame;

  SELECT v_scenes AS total_scenes, v_objects AS total_objects;

  SELECT cond, scenes FROM (
              SELECT 'class' AS cond, r_class AS scenes, 1 AS ord FROM DUAL
              WHERE p_classes IS NOT NULL AND JSON_LENGTH(p_classes) > 0
    UNION ALL SELECT 'dist', r_dist, 2 FROM DUAL WHERE p_min_dist IS NOT NULL OR p_max_dist IS NOT NULL
    UNION ALL SELECT 'occ', r_occ, 3 FROM DUAL WHERE p_occ IS NOT NULL AND JSON_LENGTH(p_occ) > 0
    UNION ALL SELECT 'points', r_points, 4 FROM DUAL WHERE p_min_points IS NOT NULL OR p_max_points IS NOT NULL
    UNION ALL SELECT 'low_only', r_low, 5 FROM DUAL WHERE COALESCE(p_low_only, FALSE)
    UNION ALL SELECT 'unreviewed', r_unrev, 6 FROM DUAL WHERE COALESCE(p_unreviewed, FALSE)
  ) x
  ORDER BY scenes DESC, ord;
END //
DELIMITER ;

-- 결과 카드 정보: 한 페이지의 장면 목록(JSON 배열)에 대해 일치 객체의 클래스·최근접 거리와
-- 장면 전체의 객체 수·관측 부족 객체 수를 돌려준다. matched_objects는 sp_search_scenes_ex와 같은 값이다.
DROP PROCEDURE IF EXISTS sp_scene_cards;
DELIMITER //
CREATE PROCEDURE sp_scene_cards(IN p_classes JSON, IN p_min_dist DOUBLE, IN p_max_dist DOUBLE,
    IN p_occ JSON, IN p_min_points INT, IN p_max_points INT,
    IN p_low_only BOOLEAN, IN p_unreviewed BOOLEAN, IN p_frames JSON)
BEGIN
  WITH pick AS (
    SELECT DISTINCT j.frame_id
    FROM JSON_TABLE(p_frames, '$[*]' COLUMNS (frame_id INT PATH '$')) j
  ),
  matched AS (
    SELECT v.frame_id, COUNT(*) AS matched_objects, MIN(v.dist_m) AS nearest_m,
           GROUP_CONCAT(DISTINCT c.class_name ORDER BY c.class_id SEPARATOR ',') AS match_classes
    FROM v_object_obs v
    JOIN pick p ON p.frame_id = v.frame_id
    JOIN object_class c ON c.class_id = v.class_id
    LEFT JOIN v_object_review_summary r ON (r.frame_id, r.object_id) = (v.frame_id, v.object_id)
    WHERE (p_classes IS NULL OR JSON_LENGTH(p_classes) = 0 OR v.class_id MEMBER OF (p_classes))
      AND (p_min_dist IS NULL OR v.dist_m >= p_min_dist)
      AND (p_max_dist IS NULL OR v.dist_m <= p_max_dist)
      AND (CASE WHEN p_occ IS NULL OR JSON_LENGTH(p_occ) = 0 THEN v.occluded BETWEEN 0 AND 2
                ELSE v.occluded MEMBER OF (p_occ) END)
      AND (p_min_points IS NULL OR v.points_in_box >= p_min_points)
      AND (p_max_points IS NULL OR v.points_in_box <= p_max_points)
      AND (NOT COALESCE(p_low_only, FALSE) OR v.is_low)
      AND (NOT COALESCE(p_unreviewed, FALSE) OR r.frame_id IS NULL)
    GROUP BY v.frame_id
  ),
  scene AS (
    SELECT o.frame_id, COUNT(*) AS n_objects
    FROM gt_object o
    JOIN pick p ON p.frame_id = o.frame_id
    JOIN object_class c ON c.class_id = o.class_id AND c.is_ignore = FALSE
    GROUP BY o.frame_id
  ),
  low AS (
    SELECT v.frame_id, SUM(v.is_low) AS n_low
    FROM v_object_obs v JOIN pick p ON p.frame_id = v.frame_id
    GROUP BY v.frame_id
  )
  SELECT p.frame_id, COALESCE(m.matched_objects, 0) AS matched_objects, m.nearest_m, m.match_classes,
         COALESCE(s.n_objects, 0) AS n_objects, COALESCE(l.n_low, 0) AS n_low
  FROM pick p
  LEFT JOIN matched m ON m.frame_id = p.frame_id
  LEFT JOIN scene s   ON s.frame_id = p.frame_id
  LEFT JOIN low l     ON l.frame_id = p.frame_id
  ORDER BY p.frame_id;
END //
DELIMITER ;

-- 장면 상세 (DB 과목 제안서의 sp_scene_detail).
-- 결과 집합 1: 장면 한 행(없으면 0행). threshold_at = 관측 부족 기준값 산출 일시.
-- 결과 집합 2: 정답 객체 전체(DontCare 포함, 원본 줄 번호 순). points_in_box가 NULL이면 계산 안 함(3D 박스 무효)
--   또는 DontCare(특징 행 없음). is_low = 포인트 수 <= 클래스 기준값 (v_object_obs와 같은 정의).
DROP PROCEDURE IF EXISTS sp_scene_detail;
DELIMITER //
CREATE PROCEDURE sp_scene_detail(IN p_frame SMALLINT)
BEGIN
  SELECT fr.frame_id, fr.image_w, fr.image_h, fr.n_points, fr.quality,
         (SELECT MAX(t.computed_at) FROM analysis_threshold t) AS threshold_at
  FROM frame fr
  WHERE fr.frame_id = p_frame;

  SELECT o.object_id, c.class_name, c.is_ignore, o.truncated, o.occluded,
         o.bbox_left, o.bbox_top, o.bbox_right, o.bbox_bottom,
         o.dim_h, o.dim_w, o.dim_l, o.loc_x, o.loc_y, o.loc_z, o.rotation_y,
         o.dist_m, o.diff_level, o.valid_3d,
         f.points_in_box, t.low_points,
         COALESCE(f.points_in_box <= t.low_points, FALSE) AS is_low
  FROM gt_object o
  JOIN object_class c ON c.class_id = o.class_id
  LEFT JOIN object_feature f ON (f.frame_id, f.object_id) = (o.frame_id, o.object_id)
  LEFT JOIN analysis_threshold t ON t.class_id = o.class_id
  WHERE o.frame_id = p_frame
  ORDER BY o.object_id;
END //
DELIMITER ;

-- 관측 부족 기준값과 산출 일시 (S6 정보 화면). 계산하지 않고 읽기만 한다.
DROP PROCEDURE IF EXISTS sp_thresholds;
DELIMITER //
CREATE PROCEDURE sp_thresholds()
BEGIN
  SELECT c.class_id, c.class_name, t.low_points, t.method, t.computed_at
  FROM analysis_threshold t JOIN object_class c USING (class_id)
  ORDER BY t.class_id;
END //
DELIMITER ;

-- 클래스 이름 목록(JSON 배열) → class_id 목록 (화면의 클래스 값을 sp_search_scenes_ex 인자로 바꾼다).
-- 무시 영역과 없는 이름은 빠진다.
DROP PROCEDURE IF EXISTS sp_class_id;
DROP PROCEDURE IF EXISTS sp_class_ids;
DELIMITER //
CREATE PROCEDURE sp_class_ids(IN p_names JSON)
BEGIN
  SELECT class_id, class_name FROM object_class
  WHERE class_name MEMBER OF (p_names) AND is_ignore = FALSE
  ORDER BY class_id;
END //
DELIMITER ;

-- 적재 직후 기준값을 계산한다(결과 집합은 출력만 한다).
CALL sp_refresh_threshold();
