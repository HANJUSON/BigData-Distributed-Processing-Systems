# 실행 절차 (N-2 재현 절차)

데이터 배치 → 변환 → 분석 → 검증 → 적재 → 화면 → 성능 측정 순서로 처음부터 다시 실행하는 방법입니다. 기대 출력과 행 수, 걸린 시간은 2026-11-05 실제 실행에서 가져왔고, 각 값 옆에 근거 파일을 적었습니다. 측정 환경은 [ENVIRONMENT.md](ENVIRONMENT.md), 결과 요약은 [RESULTS.md](RESULTS.md)를 보세요.

- 모든 명령은 **프로젝트 폴더(`finalproject/`)에서** 실행합니다. `kitti` 패키지를 현재 폴더에서 import합니다.
- 경로의 기본값은 원본 `~/data/kitti`, 산출물 `~/data/kitti/work`입니다. 저장소에는 코드와 작은 결과(JSON·CSV·PNG)만 둡니다.

## 0. 한눈에 보기

| 단계 | 명령 | 관련 요구사항 | 실제 시간 |
| --- | --- | --- | ---: |
| 1 | `python -m kitti.ingest` | F-1 ~ F-4, D-3 | 33.6 s |
| 2 | `python -m kitti.features --mode spark` | F-7, F-8 | 40.1 s |
| 3 | `python -m kitti.analysis` | F-5, F-6, F-7, F-9 | 약 40 s |
| 4 | `scripts/visual_check.py` 등 점검 스크립트 | F-8 시각 확인 | 기록 없음 |
| 5 | `kitti.sequential` + `kitti.verify` (100 · 1,000 · 7,481) | F-11 | 순차 7,481: 117.9 s |
| 6 | `python -m kitti.export` | F-10 | 기록 없음 |
| 7 | `python -m kitti.db_load` | F-10, F-11 | 기록 없음 |
| 8 | `python -m kitti.thumbnails` | U-3 | 29.1 s |
| 9 | `flask --app webapp run` | F-10, U-4 ~ U-12 | — |
| 10 | `scripts/smoke_webapp.py` | U-4, U-7, U-11 | — |
| 11 | `python -m kitti.bench` (MySQL·웹 앱 중지) | F-12 ~ F-14, N-3, N-4 | 약 31분 |

시간 근거: 1 `~/data/kitti/work/ingest.log`, `results/quality_report.md` 6절 · 2 `~/data/kitti/work/features.log` 마지막 줄 · 3 `analysis.log` 첫·끝 시각(13:26:11 → 13:26:50) · 5 `~/data/kitti/work/seq/full/timings.json`의 `total_s` · 8 `results/u3_thumbnails.json`의 `seconds` · 11 `results/bench/environment.json` 기록 시각(08:12:26Z)부터 마지막 측정 시작(08:42:46Z, `bench_raw.csv` 36번째 행) + 그 실행 20.0 s.

## 1. 준비

### 1.1 Python 3.12 가상환경 (uv)

```bash
cd finalproject
uv venv --python 3.12 .venv
uv pip install -r requirements.txt     # 버전 고정 파일 (N-1)
source .venv/bin/activate
```

- `requirements.in`은 직접 고른 패키지 목록이고, `requirements.txt`는 `uv pip compile -p .venv/bin/python requirements.in -o requirements.txt`로 만든 고정 버전입니다. 주요 버전: PySpark 4.3.0, pandas 2.3.3, numpy 2.5.4, Flask 3.1.3, matplotlib 3.11.2.
- 실행에 쓴 버전: Python 3.12.14, pyarrow 26.0.1, psutil 7.2.2 (`results/bench/environment.json`).

### 1.2 Java 17

Spark 4는 Java 17 이상이 필요합니다. 패키지만 설치하면 됩니다.

```bash
sudo apt install openjdk-17-jdk-headless
ls /usr/lib/jvm/java-17-openjdk-amd64/bin/java     # 이 경로가 있어야 함
```

- 셸 설정(`~/.bashrc`의 `JAVA_HOME`)은 **바꾸지 않습니다**. `kitti.config.ensure_java17()`이 Spark를 띄우는 프로세스 안에서만 `JAVA_HOME`·`PATH`를 Java 17로 바꿉니다.
- Java 17이 다른 경로에 있으면 실행할 때 `KITTI_JAVA_HOME=/경로` 환경변수로 알려 줍니다.

### 1.3 MySQL 8.4 (F-10 적재·웹 검색용)

실행에 쓴 버전은 MySQL 8.4.11(Ubuntu 패키지)입니다. Ubuntu의 MySQL `root`는 비밀번호가 아니라 운영체제 계정으로 인증하므로, 관리 작업은 `sudo mysql`로 합니다(10절 문제 3).

```bash
sudo apt install mysql-server
sudo systemctl start mysql
sudo mysql
```

`sudo mysql` 안에서 DB와 전용 계정을 만듭니다. `<비밀번호>`는 직접 정한 값으로 바꾸고, 이 SQL이나 비밀번호를 저장소에 올리지 마세요.

```sql
CREATE DATABASE kitti CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'kitti'@'127.0.0.1' IDENTIFIED BY '<비밀번호>';
GRANT ALL PRIVILEGES ON kitti.* TO 'kitti'@'127.0.0.1';
SET PERSIST local_infile = ON;   -- LOAD DATA LOCAL INFILE 허용 (서버 쪽, 10절 문제 5)
```

접속 정보는 옵션 파일 `~/.my.cnf`에 둡니다. 적재 도구(`mysql` CLI)와 웹 앱(PyMySQL)이 모두 `[client]` 절을 읽습니다.

```ini
[client]
user=kitti
password=<비밀번호>
host=127.0.0.1
database=kitti
local-infile=1
```

```bash
chmod 600 ~/.my.cnf
mysql -e "SELECT VERSION(), DATABASE();"     # 8.4.x, kitti 가 나오면 준비 끝
```

- 다른 옵션 파일을 쓰려면 적재는 `--defaults-file`, 웹 앱은 `KITTI_MYSQL_CNF` 환경변수로 경로를 지정합니다.

## 2. KITTI 데이터 배치

1. [KITTI 3D Object Detection](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d) 페이지에서 등록 후 4개 압축 파일을 받습니다: 왼쪽 컬러 이미지(`data_object_image_2.zip`), Velodyne 점군(`data_object_velodyne.zip`), 학습 라벨(`data_object_label_2.zip`), 보정(`data_object_calib.zip`). 이용 조건은 CC BY-NC-SA 3.0입니다(D-4).
2. `~/data/kitti`에 풀어 아래 구조를 만듭니다. 압축 파일 안에 `training/`·`testing/`이 들어 있습니다. `testing/`은 정답이 없어 쓰지 않습니다(D-1).

```text
~/data/kitti/                     ← KITTI_ROOT
└── training/
    ├── image_2/  000000.png … 007480.png   (7,481개)
    ├── velodyne/ 000000.bin … 007480.bin   (7,481개)
    ├── label_2/  000000.txt … 007480.txt   (7,481개)
    └── calib/    000000.txt … 007480.txt   (7,481개)
~/data/kitti/work/                ← KITTI_WORK (산출물, 자동 생성)
```

3. 다른 위치를 쓰면 환경변수로 바꿉니다. 모든 명령이 이 값을 기본값으로 씁니다.

```bash
export KITTI_ROOT=/다른/경로/kitti
export KITTI_WORK=/다른/경로/kitti/work
```

- 실제로 처리한 용량: `training/` 4종 합계 20,507,768,620 B(19.10 GiB), 포인트 891,919,836개(`results/quality_report.md` 4절). 산출물 중 `points` Parquet이 8.52 GiB입니다(같은 파일 5절). `KITTI_WORK`가 있는 디스크에 30 GiB 이상 여유를 두세요.
- `KITTI_WORK`는 ext4 같은 리눅스 파일시스템에 두는 것을 권장합니다. 실제 실행에서는 원본·산출물 모두 ext4 NVMe(`/dev/nvme0n1p2`)에 있었습니다(`results/bench/environment.json`).

## 3. 고정 표본 (N-1, F-12)

`samples/sample_100.txt`, `sample_1000.txt`, `sample_7481.txt`가 저장소에 들어 있으므로 다시 만들 필요는 없습니다. 규칙(학습 `frame_id` 오름차순 → `numpy.random.default_rng(42).permutation` → 앞 100·1,000개, 7,481은 전체)은 각 파일 머리말에 적혀 있습니다. 다시 만들어 같은지 확인하려면:

```bash
python scripts/make_samples.py --out /tmp/samples_check && diff -r samples /tmp/samples_check
```

## 4. 수집·정제·Parquet 변환 (F-1 ~ F-4, D-3)

```bash
python -m kitti.ingest --root ~/data/kitti --work ~/data/kitti/work
```

기대 출력 (로그 마지막, `~/data/kitti/work/ingest.log`):

```text
frames=7481 linked=7481 unlinked=0 duplicates=0
[phase] total: 33.59 s
done: frames=7481 points=891919836 bytes=20507768620 -> /home/<사용자>/data/kitti/work/parquet
```

| 산출물 | 기대값 | 근거 |
| --- | --- | --- |
| `results/quality_report.md`, `.json` | 연결 7,481 / 미연결 0 / 중복 0, 객체 행 51,865, DontCare 11,295, 범위 오류 0, 3D 무효 8 | `quality_report.md` 1–3절 |
| `parquet/frames` | 7,481행 | 같은 파일 5절 |
| `parquet/objects` | 51,865행 | 같은 파일 5절 |
| `parquet/points` | 891,919,836행, 파일 32개, 8.52 GiB (zstd) | 같은 파일 5절 |

- 부분집합(`--frames-file`)을 정본 폴더에 쓰려 하거나 대상 장면에 누락 파일이 있으면 종료 코드 2로 거부하고 아무것도 쓰지 않습니다(`kitti/ingest.py` 머리말). 정본을 다시 만들 때만 `--force-replace`를 씁니다.

## 5. 객체 특징 계산 (F-7, F-8)

```bash
python -m kitti.features --mode spark
```

기대 출력 (`features.log` 마지막 줄):

```text
mode=spark frames=7481 rows=51865 out=/home/<사용자>/data/kitti/work/parquet/object_features seconds=40.117
```

## 6. Spark 분석 (F-5, F-6, F-7, F-9)

```bash
python -m kitti.analysis --work ~/data/kitti/work --results results --figures results/figures
```

- 로그에 `F-5 pass=True`가 나옵니다.
- 출력: `results/f5_dataframe_vs_sql.json`, `grid_cumulative.csv`, `grid_params.json`, `f7_*.csv`, `f9_*.csv`, `analysis_summary.json`, `results/figures/f6_*.png`·`f7_*.png`·`f9_points_distribution.png`, 그리고 `parquet/grid_stats`·`parquet/low_observation`.

| 항목 | 기대값 | 근거 |
| --- | --- | --- |
| `grid_stats` 행 수 | 9,746,109 | `analysis_summary.json` `f6.grid_rows` |
| `f9_low_objects.csv` | 4,298행 | `analysis_summary.json` `f9.n_low_objects` |
| `f9_low_scenes.csv` | 2,729행 | `analysis_summary.json` `f9.n_low_scenes` |
| F-5 두 질의 | `pass: true` | `f5_dataframe_vs_sql.json` |

## 7. F-8 점검 스크립트

```bash
# 표본 장면 시각 검증 그림 (points_in_box를 다시 세어 다르면 종료 코드 1)
python scripts/visual_check.py --root ~/data/kitti --work ~/data/kitti/work \
    --frames 0 1000 5000 --out results/figures/f8_visual_check.png

# devkit 방식(8개 꼭짓점 + 면 반공간)으로 독립 계산해 비교 (PASS면 종료 코드 0)
python scripts/check_box_independent.py --object-features ~/data/kitti/work/parquet/object_features 0 1000 5000

# 장면 0–9를 Spark와 순차로 각각 계산해 비교 (KITTI_WORK/check_features/ 에 덮어씀)
python scripts/check_features_real.py
```

이 세 스크립트의 실행 결과(통과 여부 출력)는 파일로 남지 않습니다. 남는 산출물은 `results/figures/f8_visual_check.png`뿐입니다.

## 8. 정확성 검증: 순차 처리 + 대조 (F-11)

규모마다 ① Spark 분석(부분집합) ② 순차 처리 ③ 대조를 실행합니다. 7,481은 6절의 정본 분석 결과를 그대로 씁니다.

```bash
# 100 · 1,000 장면: 부분집합 Spark 분석 (결과는 results/subsets/, Parquet은 KITTI_WORK/subsets/)
for n in 100 1000; do
  python -m kitti.analysis --frames-file samples/sample_$n.txt --results results/subsets/sample_$n
  python -m kitti.sequential --root ~/data/kitti --frames-file samples/sample_$n.txt \
      --out ~/data/kitti/work/seq/sample_$n
  python -m kitti.verify --spark-work ~/data/kitti/work/subsets/sample_$n \
      --spark-results results/subsets/sample_$n --seq ~/data/kitti/work/seq/sample_$n \
      --out results/subsets/sample_$n --frames-file samples/sample_$n.txt
done

# 7,481 장면 (전체)
python -m kitti.sequential --root ~/data/kitti --out ~/data/kitti/work/seq/full
python -m kitti.verify --spark-work ~/data/kitti/work --spark-results results \
    --seq ~/data/kitti/work/seq/full --out results
```

- `kitti.verify`는 항목마다 `PASS …`를 찍고 마지막에 `overall=PASS -> results/verification.md`를 출력합니다. 하나라도 실패하면 종료 코드 1입니다.
- `kitti.sequential`은 출력 폴더가 Spark 결과 폴더처럼 보이면(`parquet/`나 `analysis_summary.json`이 있으면) 쓰지 않고 거부합니다.

| 규모 | 순차 처리 시간 (`total_s`) | 대조 결과 | 근거 |
| --- | ---: | --- | --- |
| 100 | 1.5 s | 13/13 통과, `object_features` 700행, `grid_stats` 141,543행 | `results/subsets/sample_100/verification.md` |
| 1,000 | 17.0 s | 13/13 통과, `object_features` 7,000행, `grid_stats` 1,319,166행 | `results/subsets/sample_1000/verification.md` |
| 7,481 | 117.9 s | 13/13 통과, `object_features` 51,865행, `grid_stats` 9,746,109행 | `results/verification.md` |

순차 처리 시간은 `~/data/kitti/work/seq/<이름>/timings.json`입니다. 1,000 규모 부분집합 분석은 약 30 s(`analysis_1000.log` 13:28:05 → 13:28:34)가 걸렸습니다.

## 9. MySQL 적재 (F-10)

```bash
sudo systemctl start mysql     # 멈춰 있었다면
python -m kitti.export --work ~/data/kitti/work --out ~/data/kitti/work/mysql_csv
python -m kitti.db_load --csv-dir ~/data/kitti/work/mysql_csv
```

- `kitti.export`는 `frame.csv`, `gt_object.csv`, `object_feature.csv`, `expected.json`을 만듭니다(NULL = `\N`).
- `kitti.db_load`는 `db/01_schema.sql` → `02_load.sql` → `03_views_procs.sql` → `04_checks.sql`을 차례로 실행하고, 결과를 `expected.json`과 비교해 `results/mysql_consistency.md`·`.json`에 씁니다. 종료 코드: 0 = 모두 일치, 1 = 불일치, 2 = SQL 실행 실패. 다시 실행해도 결과가 같습니다(스테이징은 비우고 다시 채움, 본 테이블은 UPSERT). 부분집합을 적재한 뒤처럼 본 테이블에 스테이징에 없는 행이 남아 있으면 `--replace`를 붙여 같은 트랜잭션 안에서 지웁니다.
- 이미 적재한 DB를 다시 대조만 하려면 `--checks-only`를 붙입니다.

| 테이블 | 기대 행 수 | 근거 |
| --- | ---: | --- |
| `frame` | 7,481 | `results/mysql_consistency.md` |
| `gt_object` | 51,865 | 같은 파일 |
| `object_feature` | 40,570 (DontCare 제외) | 같은 파일 |
| `object_class` | 9 | 같은 파일 |
| `analysis_threshold` | 8 | 같은 파일 |
| 판정 | **통과 (62 / 62 항목 일치)** | 같은 파일 |

## 10. 썸네일 (U-3)

```bash
python -m kitti.thumbnails --root ~/data/kitti --out ~/data/kitti/work/thumbs
```

기대값(`results/u3_thumbnails.json`): 생성 7,481, 실패 0, 누락 이미지 0, 장당 최대 13,194 B(목표 30,720 B 이하), 합계 58,745,674 B, 29.1 s(작업자 8개). 이미 있는 최신 썸네일은 건너뛰므로 다시 실행하면 `n_skipped`가 늘어납니다. 전부 다시 만들려면 `--force`.

## 11. 웹 앱 실행 (F-10, S1–S6)

```bash
flask --app webapp run --port 5050
# 브라우저에서 http://127.0.0.1:5050/
```

| 환경변수 | 기본값 | 용도 |
| --- | --- | --- |
| `KITTI_MYSQL_CNF` | `~/.my.cnf` | MySQL 옵션 파일 (`[client]` 절) |
| `KITTI_ROOT` | `~/data/kitti` | S2 이미지·점군·보정 |
| `KITTI_THUMBS` | `$KITTI_WORK/thumbs` | S1 썸네일 |
| `KITTI_RESULTS` | `results/` | S5 결과 파일 |
| `KITTI_DEMO` | 꺼짐 | `1`이면 S1·S2·S6이 데모 데이터를 씀(MySQL 없이 화면 검토용) |

- MySQL 없이 화면 배치만 보려면 `KITTI_DEMO=1 flask --app webapp run --port 5050`.
- 개발 중에는 `flask --app webapp run --debug`로 실행하면 `/_components`(컴포넌트 갤러리)도 열립니다.
- 주소: `/`(S1 검색), `/scenes/<frame_id>`(S2 상세), `/pipeline`(S5 처리 결과), `/about`(S6 정보). `/review-sets`(S3)·`/dashboard`(S4)는 DB 과목 범위라 안내 화면만 있습니다.

## 12. 웹 앱 실데이터 점검

MySQL이 켜진 상태에서 실행합니다. 서버를 따로 띄우지 않고 Flask test client로 점검합니다.

```bash
python scripts/smoke_webapp.py --requests 30
```

- S1 결과가 같은 인자의 `CALL sp_search_scenes_ex(…)`와 같은지, S2 객체 표가 `CALL sp_scene_detail`과 같은지, S5 한 줄 결론이 결과 파일과 같은지, 응답 시간을 확인합니다.
- 종료 코드: 0 = 모두 통과, 1 = 실패 있음, 2 = DB에 연결하지 못함. UI QA 뒤 실행에서 종료 코드 0이었습니다(`results/ui_qa.md` 결함 표 아래). 이 스크립트의 출력은 `--json 경로`를 줄 때만 파일로 남습니다.
- 단위 시험은 `pytest -q`이며 UI QA 수정 뒤 275개 통과했습니다(`results/ui_qa.md`). 가짜 DB를 쓰므로 실제 연결의 증거는 위 스크립트입니다.

## 13. 성능 측정 (F-12 ~ F-14, N-3, N-4)

측정 중에는 다른 작업과 자원을 나누지 않도록 **MySQL과 웹 앱을 멈춥니다**(README 7.1).

```bash
# 1) 웹 앱 종료 (실행 중인 터미널에서 Ctrl+C), MySQL 중지
sudo systemctl stop mysql

# 2) 실행 순서만 먼저 확인 (선택)
python -m kitti.bench --dry-run

# 3) 측정: 예열 3회 + 4개 방식 × 3개 규모 × 3회 = 36회
python -m kitti.bench --root ~/data/kitti --work ~/data/kitti/work/bench --results results/bench

# 4) 끝나면 MySQL 다시 시작
sudo systemctl start mysql
# 측정을 다시 돌리지 않고 bench_raw.csv에서 요약표·그림·판정만 다시 만들 때
python -m kitti.bench --results results/bench --summarize-only
```

- 각 실행은 새 프로세스이고 순서는 seed 42로 섞습니다(`results/bench/run_order.json`). 규모마다 예열 1회(`local8`)는 결과에서 뺍니다(`results/bench/warmup.csv`).
- 실행마다 결과를 같은 규모의 순차 결과와 `kitti.verify`로 대조해 `bench_raw.csv`의 `verify_pass`에 남깁니다.
- 출력: `results/bench/bench_raw.csv`, `summary.csv`, `bench_report.md`, `environment.json`, `n4_judgment.json`, `mem/*.csv`(메모리 시계열 39개), `figures/*.png` 4개.
- 기대 결과: `n4_judgment.json`의 `"pass": true`, 기록 36/36, 정상 종료 36, 대조 통과 36, 최대 peak RSS 6,817.7 MiB(예산 24,604.6 MiB). 실행별 실행 로그는 `~/data/kitti/work/bench/logs/`에 있습니다.
- 백그라운드 앱(브라우저, 동기화 프로그램 등)도 가능하면 닫습니다. 실행 직전 가용 메모리와 load average가 실행마다 기록됩니다([ENVIRONMENT.md](ENVIRONMENT.md) 3절).

## 14. 문제 해결 (실제로 겪은 문제)

### 문제 1. `JAVA_HOME`이 Java 11인데 Spark 4를 실행함

- 증상: 셸의 `~/.bashrc`가 `JAVA_HOME`을 Java 11로 지정해 두어, 그대로 Spark 4를 띄우면 JVM이 시작되지 않습니다(Spark 4는 Java 17 이상 필요).
- 조치: 프로젝트 코드는 `kitti.config.ensure_java17()`이 프로세스 안에서만 `JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64`로 바꿉니다. `~/.bashrc`는 다른 과목에서 Java 11을 쓰므로 고치지 않습니다. 반드시 `python -m kitti.…` 명령으로 실행하고, `pyspark` 셸이나 `spark-submit`을 직접 부르지 마세요. Java 17 경로가 다르면 `KITTI_JAVA_HOME`으로 지정합니다.

### 문제 2. `/tmp`가 tmpfs(메모리)라 Spark 임시 파일이 RAM을 씀

- 증상: Spark 기본 임시 폴더 `spark.local.dir=/tmp`가 tmpfs라서, 셔플·스필 파일이 디스크가 아니라 메모리에 쌓입니다. 9억 행 집계에서 메모리를 잡아먹고 측정 메모리를 왜곡합니다.
- 조치: `kitti.config.get_spark()`가 `spark.local.dir = $KITTI_WORK/spark-tmp`(ext4)로 지정합니다. 성능 측정은 `$KITTI_WORK/bench/spark-tmp`를 씁니다(`results/bench/environment.json`). 로그의 `spark.local.dir will be overridden … SPARK_LOCAL_DIRS` 경고는 `SPARK_LOCAL_DIRS` 환경변수를 설정하지 않았다면 무시해도 됩니다. 이 변수를 설정하지 마세요.

### 문제 3. MySQL `root`에 비밀번호로 접속되지 않음

- 증상: `mysql -u root -p`가 접근 거부로 실패합니다. Ubuntu 패키지의 `root`는 운영체제 계정 인증(`auth_socket`)을 씁니다.
- 조치: 관리 작업은 `sudo mysql`로 하고, 앱용 계정 `kitti`를 따로 만들어 `~/.my.cnf`에 둡니다(1.3절). `root`에 비밀번호를 설정할 필요는 없습니다.

### 문제 4. MySQL을 멈춘 뒤 `/var/run/mysqld`가 사라짐

- 증상: 성능 측정을 위해 `systemctl stop mysql`로 멈추면 `/var/run/mysqld`(tmpfs `/run` 아래)가 지워집니다. 이 상태에서 `mysqld`를 직접 띄우거나 소켓으로 접속하면 소켓 디렉터리가 없어 실패합니다.
- 조치: 다시 켤 때는 `sudo systemctl start mysql`을 씁니다(systemd가 디렉터리를 다시 만듭니다). 직접 띄워야 한다면 먼저 `sudo install -d -o mysql -g mysql /var/run/mysqld`. 프로젝트 설정은 `host=127.0.0.1`(TCP)로 접속하므로 소켓 경로에 의존하지 않습니다.

### 문제 5. `LOAD DATA LOCAL INFILE`이 거부됨

- 증상: 적재(`db/02_load.sql`)에서 `Loading local data is disabled; this must be enabled on both the client and server sides` 오류가 납니다. MySQL 8은 기본으로 서버 쪽 `local_infile`이 꺼져 있습니다.
- 조치: 서버는 `sudo mysql -e "SET PERSIST local_infile = ON;"`(재시작 후에도 유지), 클라이언트는 `~/.my.cnf`의 `local-infile=1`. `kitti.db_load`는 `mysql --local-infile=1`로 호출하므로 클라이언트 쪽은 자동입니다. 확인: `mysql -e "SHOW GLOBAL VARIABLES LIKE 'local_infile';"` → `ON`.
