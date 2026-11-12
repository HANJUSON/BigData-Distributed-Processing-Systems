"""KITTI 3D Object Detection 빅데이터 처리 패키지.

모듈 구성
- config: 경로·고정 상수·Java 17 설정·SparkSession 팩토리
- io: label_2 / calib / velodyne / image 파일 파서 (순수 함수)
- geometry: 좌표 변환, 3D 박스 포함 판정, 거리, 난이도, 격자 집계 (numpy float64)
- schema: Spark 테이블 스키마(StructType)
- ingest: 수집·정제(F-1~F-4, D-3) — 장면 연결, 품질 보고서, frames/objects/points Parquet
- features: object_features 계산 (F-7·F-8, Spark mapInArrow / 순차)
- sequential: Python 순차 기준 구현 (F-11, pyspark 없음)
- verify: Spark ↔ 순차 결과 대조 (F-11)

데이터 출처: KITTI Vision Benchmark Suite (CC BY-NC-SA 3.0).
"""

__all__ = ["config", "io", "geometry", "schema", "ingest", "features", "analysis", "sequential", "verify"]
