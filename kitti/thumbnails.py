"""결과 카드 썸네일 생성 (README U-3, 9.4 S1 결과 카드, 9.9 성능).

실행:
    python -m kitti.thumbnails [--root ~/data/kitti] [--out ~/data/kitti/work/thumbs]
                               [--workers 8] [--frames-file list.txt] [--force]

- 입력: <root>/training/image_2/<frame_id:06d>.png, label_2/<frame_id:06d>.txt
- 출력: <out>/<frame_id:06d>.webp — 폭 320 px(높이는 원본 비율), 정답 2D 박스를 미리 그린다.
  DontCare 영역은 그리지 않는다. 박스 색은 README 9.6 Okabe–Ito 클래스 색(kitti.analysis.CLASS_COLORS).
  Pillow에 WebP 지원이 없으면 JPEG(.jpg)로 쓴다.
- 장당 목표 30 KB 이하(README 9.9). 넘으면 품질을 낮춰 다시 저장한다.
- 멱등: 출력이 있고 원본 이미지·라벨보다 새로우면 건너뛴다(--force로 다시 생성).
  임시 파일에 쓴 뒤 이름을 바꿔, 중단돼도 반쯤 쓴 파일이 남지 않는다.
- 요약: <results>/u3_thumbnails.json (생성·건너뜀·실패 수, 크기 통계, 실패·누락 목록)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import multiprocessing
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, features

from . import config
from . import ingest
from . import io as kio
from .analysis import CLASS_COLORS

THUMB_WIDTH: int = 320
MAX_BYTES: int = 30 * 1024
QUALITY_STEPS: tuple[int, ...] = (80, 70, 60, 50, 40)
BOX_WIDTH: int = 2
FORMAT: str = "WEBP" if features.check("webp") else "JPEG"
EXT: str = ".webp" if FORMAT == "WEBP" else ".jpg"


def thumb_path(out: Path, frame_id: int) -> Path:
    return Path(out) / f"{frame_id:06d}{EXT}"


def is_up_to_date(target: Path, sources: Sequence[Path]) -> bool:
    """target이 있고 모든 원본보다 수정 시각이 같거나 늦으면 True."""
    if not target.exists():
        return False
    t = target.stat().st_mtime
    return all(t >= s.stat().st_mtime for s in sources if s.exists())


def render(image_path: Path, label_path: Path | None, width: int = THUMB_WIDTH) -> Image.Image:
    """원본 이미지를 폭 `width`로 줄이고 정답 2D 박스(DontCare 제외)를 그린다."""
    with Image.open(image_path) as src:
        img = src.convert("RGB")
    scale = width / img.width
    height = max(1, round(img.height * scale))
    img = img.resize((width, height), Image.Resampling.LANCZOS)
    if label_path is not None and label_path.exists():
        draw = ImageDraw.Draw(img)
        for obj in kio.parse_label(label_path):
            if obj["is_dontcare"]:
                continue
            box = [obj["bbox_left"] * scale, obj["bbox_top"] * scale,
                   obj["bbox_right"] * scale, obj["bbox_bottom"] * scale]
            draw.rectangle(box, outline=CLASS_COLORS.get(obj["class"], "#8C8C8C"), width=BOX_WIDTH)
    return img


def save(img: Image.Image, target: Path, max_bytes: int = MAX_BYTES) -> tuple[int, int]:
    """임시 파일에 저장한 뒤 이름을 바꾼다. 크기가 max_bytes를 넘으면 품질을 낮춘다.

    Returns:
        (바이트 수, 사용한 품질). 가장 낮은 품질로도 넘으면 그 결과를 그대로 둔다.
    """
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    quality = QUALITY_STEPS[0]
    for quality in QUALITY_STEPS:
        if FORMAT == "WEBP":
            img.save(tmp, FORMAT, quality=quality, method=6)
        else:
            img.save(tmp, FORMAT, quality=quality, optimize=True)
        if tmp.stat().st_size <= max_bytes:
            break
    size = tmp.stat().st_size
    os.replace(tmp, target)
    return size, quality


def make_thumbnail(args: tuple[int, str, str, bool]) -> dict[str, Any]:
    """프로세스 풀 작업 하나: (frame_id, root, out, force) → 결과 dict."""
    frame_id, root, out, force = args
    image = kio.frame_path(root, "image_2", frame_id)
    label = kio.frame_path(root, "label_2", frame_id)
    target = thumb_path(Path(out), frame_id)
    if not image.exists():
        return {"frame_id": frame_id, "status": "missing_image"}
    if not force and is_up_to_date(target, (image, label)):
        return {"frame_id": frame_id, "status": "skipped", "bytes": target.stat().st_size}
    try:
        img = render(image, label if label.exists() else None)
        size, quality = save(img, target)
    except Exception as exc:  # noqa: BLE001 - 장면 하나의 실패를 기록하고 계속한다
        return {"frame_id": frame_id, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return {"frame_id": frame_id, "status": "created", "bytes": size, "quality": quality,
            "size": [img.width, img.height]}


def generate(
    frame_ids: Sequence[int],
    root: Path | str,
    out: Path | str,
    workers: int = 8,
    force: bool = False,
) -> dict[str, Any]:
    """썸네일을 병렬로 만들고 요약 dict를 돌려준다."""
    out = Path(out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    root = str(Path(root).expanduser())
    jobs = [(int(f), root, str(out), force) for f in sorted(frame_ids)]
    start = time.perf_counter()
    if workers <= 1:
        results = [make_thumbnail(j) for j in jobs]
    else:
        # spawn: pyarrow 등 스레드가 있는 부모 프로세스를 fork하지 않는다.
        with multiprocessing.get_context("spawn").Pool(workers) as pool:
            results = pool.map(make_thumbnail, jobs, chunksize=16)
    elapsed = time.perf_counter() - start

    by_status: dict[str, list[int]] = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r["frame_id"])
    present = [thumb_path(out, f) for f in sorted(frame_ids)]
    sizes = [p.stat().st_size for p in present if p.exists()]
    missing = [f for f, p in zip(sorted(frame_ids), present) if not p.exists()]
    return {
        "format": FORMAT,
        "width": THUMB_WIDTH,
        "out": config.display_path(out),
        "n_frames": len(jobs),
        "n_created": len(by_status.get("created", [])),
        "n_skipped": len(by_status.get("skipped", [])),
        "n_failed": len(by_status.get("failed", [])),
        "n_missing_image": len(by_status.get("missing_image", [])),
        "n_present": len(sizes),
        "missing": missing,
        "failed": [r for r in results if r["status"] == "failed"],
        "bytes_total": sum(sizes),
        "bytes_max": max(sizes, default=0),
        "bytes_mean": round(sum(sizes) / len(sizes), 1) if sizes else 0,
        "n_over_max_bytes": sum(s > MAX_BYTES for s in sizes),
        "max_bytes": MAX_BYTES,
        "seconds": round(elapsed, 3),
        "workers": workers,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="결과 카드 썸네일 생성 (U-3)")
    parser.add_argument("--root", default=str(config.KITTI_ROOT), help="KITTI 루트 (기본 KITTI_ROOT)")
    parser.add_argument("--out", default=str(config.KITTI_WORK / "thumbs"), help="출력 폴더")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--frames-file", help="frame_id 목록 파일. 없으면 image_2의 모든 장면")
    parser.add_argument("--force", action="store_true", help="최신 썸네일도 다시 만든다")
    parser.add_argument("--results", default=str(ingest.RESULTS_DIR), help="요약 JSON 폴더")
    args = parser.parse_args(argv)

    if args.frames_file:
        from .features import read_frame_ids

        ids = read_frame_ids(args.frames_file)
    else:
        ids = kio.list_frame_ids(args.root)["image_2"]
    summary = generate(ids, args.root, args.out, args.workers, args.force)
    results = Path(args.results)
    results.mkdir(parents=True, exist_ok=True)
    (results / "u3_thumbnails.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"frames={summary['n_frames']} created={summary['n_created']} skipped={summary['n_skipped']} "
          f"failed={summary['n_failed']} missing={len(summary['missing'])} "
          f"bytes_total={summary['bytes_total']} bytes_max={summary['bytes_max']} "
          f"over_{MAX_BYTES}={summary['n_over_max_bytes']} seconds={summary['seconds']}")
    return 0 if not summary["missing"] and not summary["n_failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
