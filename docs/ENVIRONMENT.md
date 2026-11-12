# 측정 환경과 조건 (N-3)

성능 결과(F-12 ~ F-14)를 얻은 장비와 측정 조건입니다. 값은 측정 실행이 기록한 `results/bench/environment.json`(기록 시각 2026-11-05T08:12:26Z)에서 가져왔고, 그 파일에 없는 값은 출처를 따로 적었습니다.

## 1. 하드웨어·운영체제

| 항목 | 값 | 출처 |
| --- | --- | --- |
| CPU | AMD Ryzen 7 7840HS with Radeon 780M Graphics | `environment.json` `cpu_model` |
| 코어 수 | 물리 8 / 논리 16 | `cpu_physical_cores`, `cpu_logical_cores` |
| RAM | 30,755.8 MiB (30.03 GiB) | `ram_total_mib`, `ram_total_gib` |
| 저장장치 (원본·측정 작업 폴더) | `/dev/nvme0n1p2`, ext4, imation M.2 PCIe 512GB SSD Z991 (SSD, `rotational: 0`), 여유 223.1 GiB | `storage.data_root`, `storage.work_dir` |
| 원본 위치 | `/home/hanjuson/data/kitti` | `storage.data_root.path` |
| 측정 작업 폴더 | `/home/hanjuson/data/kitti/work/bench` | `storage.work_dir.path` |
| OS | Ubuntu 26.04.1 LTS (Linux-7.0.0-38-generic-x86_64-with-glibc2.43) | `os` |
| 커널 | 7.0.0-38-generic | `kernel` |

- 코드와 가상환경(`.venv`)은 NTFS(ntfs3) 파티션의 프로젝트 폴더에 있습니다(`python_executable` 경로, `.omc/autopilot/spec.md`). 측정 중 읽고 쓰는 데이터는 모두 위 ext4 SSD에 있습니다.

## 2. 소프트웨어

| 항목 | 버전 | 출처 |
| --- | --- | --- |
| Python | 3.12.14 (`finalproject/.venv`, uv로 생성) | `environment.json` `python` |
| PySpark | 4.3.0 | `versions.pyspark` |
| pyarrow | 26.0.1 | `versions.pyarrow` |
| numpy | 2.5.4 | `versions.numpy` |
| pandas | 2.3.3 | `versions.pandas` |
| psutil | 7.2.2 | `versions.psutil` |
| matplotlib | 3.11.2 | `versions.matplotlib` |
| Java | OpenJDK 17.0.20.1 (build 17.0.20.1+1-1-26.04-Ubuntu), `JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64` | `java_version`, `java_home` |
| MySQL (적재·웹 검색, 측정 중에는 중지) | 8.4.11 (Ubuntu 패키지 8.4.11-0ubuntu0.26.04.1) | `mysql --version`, `.omc/autopilot/spec.md` |
| 패키지 고정 목록 | `requirements.txt` (`uv pip compile`로 생성) | N-1 |

- Java 17은 프로젝트 코드가 프로세스 안에서만 지정합니다. 셸의 기본 Java(11)는 바꾸지 않았습니다([RUNBOOK.md](RUNBOOK.md) 14절 문제 1).

## 3. 측정 조건

| 조건 | 값 | 출처 |
| --- | --- | --- |
| 실행 방식 | 순차(`kitti.sequential`, pyspark import 없음), Spark `local[1]`·`local[4]`·`local[8]` (`kitti.bench_job`) | `results/bench/bench_report.md` |
| 규모 | 100 · 1,000 · 7,481 장면 (`samples/sample_*.txt`, seed 42로 고정) | `samples/`, N-1 |
| 반복 | 조건마다 3회, 총 36회 | `n4_judgment.json` `expected_runs` |
| 새 프로세스 | 실행마다 새 프로세스로 시작 | `bench_report.md` 측정 방법 |
| 예열 | 규모마다 `local8` 1회(총 3회), 결과에서 제외 | `warmup.csv`, `n4_judgment.json` `excluded` |
| 실행 순서 | `numpy.random.default_rng(42)` 순열로 섞음 | `run_order.json` |
| BLAS 스레드 | `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1` (모든 방식) | `environment.json` `blas_threads` |
| 파티션 | 장면 목록을 연속 구간 32개로 나눔, `spark.sql.shuffle.partitions=32` | `spark_conf` |
| AQE | `spark.sql.adaptive.enabled=false` (파티션 수 고정) | `spark_conf` |
| 드라이버 메모리 | `spark.driver.memory=8g` | `spark_conf` |
| Spark 임시 폴더 | `spark.local.dir=/home/hanjuson/data/kitti/work/bench/spark-tmp` (ext4, `/tmp` tmpfs를 피함) | `spark_conf` |
| 캐시 | Spark `cache()` 없음. OS 페이지 캐시는 규모별 예열 실행으로 데운 상태에서 측정 | `bench_report.md` 측정 방법 |
| 다른 서비스 | MySQL 서버와 웹 앱은 측정 동안 중지 | README 7.1, [RUNBOOK.md](RUNBOOK.md) 13절 |
| 메모리 측정 | `psutil`로 100 ms마다 실행 프로세스 트리(Python·JVM·Python worker) RSS 합을 샘플링, 최댓값 = peak RSS | `bench_report.md`, `mem/*.csv` |
| 메모리 예산 | 물리 RAM의 80% = 24,604.6 MiB. 넘으면 그 실행을 중단하고 실패로 기록 | `n4_judgment.json` `budget_mib` |
| 정확성 | 실행마다 같은 규모의 기준 순차 결과와 `kitti.verify`(F-11)로 대조 | `bench_raw.csv` `verify_pass` |

### 시간에 포함한 범위

- 벽시계 시간(`wall_s`)은 측정기가 자식 프로세스를 띄운 순간부터 끝날 때까지입니다. 인터프리터 시작, import, JVM·SparkSession 시작, `spark.stop()`이 모두 들어갑니다.
- 단계 시간은 프로세스 안에서 잽니다: `session`(pyspark import + JVM·세션 시작), `read_compute`(원본 `.bin` 읽기 + F-8 계산 + 포인트 전달·부분 집계), `aggregate`(최종 GROUP BY·`grid_stats` 쓰기 + F-5/F-6/F-7/F-9 질의), `write`(결과 CSV·JSON 쓰기), `other`(= 벽시계 − 단계 합). 순차 처리는 집계가 `read_compute`에 들어가고 `aggregate`는 0입니다(`bench_report.md`).
- 처리량: scenes/s = 장면 수 / 벽시계 시간, points/s = 입력 포인트 수 / 벽시계 시간(시작 비용 포함).

### 실행 직전 기준선

실행마다 시스템 가용 메모리와 load average를 `bench_raw.csv`의 `baseline_*` 열에 남겼습니다. 36회 실행에 대해:

| 항목 | 최소 | 중앙값 | 최대 |
| --- | ---: | ---: | ---: |
| 가용 메모리 (MiB) | 14,627.2 | 17,785.8 | 19,979.2 |
| 1분 load average | 2.70 | 8.18 | 17.19 |

- 값은 `bench_raw.csv`에서 계산했습니다(2026-11-05T08:14:40Z ~ 08:42:46Z 시작 실행).
- 1분 load average는 직전 실행(예열 포함)의 부하가 남은 값이라 백그라운드 부하만을 뜻하지 않습니다. 최댓값 17.19는 예열 3회 직후의 첫 실행(`local1`, 7,481, r1)에서 나왔습니다.
- 측정 중 데스크톱 세션의 다른 앱(브라우저 등)을 모두 닫았는지는 기록하지 않았습니다. 가용 메모리가 30 GiB 중 14.3–19.5 GiB였던 것은 다른 프로세스와 페이지 캐시가 메모리를 쓰고 있었다는 뜻입니다. 이 영향은 위 기준선으로만 확인할 수 있습니다.

## 4. 한계

- peak RSS는 공유 페이지(공유 라이브러리, fork 뒤 copy-on-write)를 프로세스마다 중복해 더하므로 실제 물리 사용량보다 클 수 있고, 100 ms보다 짧은 피크는 놓칠 수 있습니다(F-13).
- 페이지 캐시는 RSS에 들어가지 않습니다. 예열로 캐시를 데웠으므로 결과는 "원본이 이미 메모리 캐시에 있는" 조건의 값입니다. 처음 읽는(콜드) 디스크 성능은 측정하지 않았습니다.
- 데이터는 2026-11-04(image_2·label_2·calib)~2026-11-05(velodyne, zip 28,750,710,812 B, `unzip -t` 통과) KITTI 공식 배포 주소(`s3.eu-central-1.amazonaws.com/avg-kitti`)에서 받았습니다. 사용한 사본은 파일 수와 바이트 수로 식별합니다(`environment.json` `data.training`: 4종 각 7,481개, image_2 6,220,741,348 B, velodyne 14,270,717,376 B, label_2 4,239,487 B, calib 12,070,409 B).
- 결과는 이 단일 장비에 한정됩니다. 다중 노드 확장은 범위 밖입니다(README 6.2).
