"""F-8 독립 점검: devkit 방식 8개 꼭짓점 + 면 반공간 판정으로 박스 내부 포인트 수를 다시 세어
kitti.features 결과(points_in_box)와 비교한다. scipy 없이 numpy만 쓴다.

kitti.geometry.points_in_box_mask(객체 좌표로 회전한 뒤 축별 범위 비교)와 다른 방법이다.
1. KITTI devkit compute_box_3d와 같게 꼭짓점을 만든다:
   R = roty(ry) = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
   x = [l/2, l/2, -l/2, -l/2, l/2, l/2, -l/2, -l/2], y = [0, 0, 0, 0, -h, -h, -h, -h],
   z = [w/2, -w/2, -w/2, w/2, w/2, -w/2, -w/2, w/2], corners = R·[x; y; z] + location
2. 6개 면마다 꼭짓점 세 개로 외적 법선을 구하고 박스 중심 반대쪽(바깥)을 향하게 맞춘다.
3. 점 p가 모든 면에 대해 n̂·(p − a) ≤ ε 이면 내부(ε = config.EPS, 단위 법선이라 m 단위 여유).

실행:
    python scripts/check_box_independent.py [--root ~/data/kitti] [--object-features DIR]
        [--frames-file samples/sample_100.txt] [--out results/f8_independent_check.json] 0 1 2 ...
    --object-features가 없으면 kitti.features.frame_features로 그 장면을 계산해 비교한다.
    --frames-file의 장면은 위치 인자 장면 뒤에 붙는다. --out을 주면 결과를 JSON으로 남긴다.
모든 객체 수가 같으면 PASS(종료 코드 0), 하나라도 다르면 FAIL(종료 코드 1).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitti import config  # noqa: E402

config.set_single_thread_blas()

import numpy as np  # noqa: E402

from kitti import features as kf  # noqa: E402
from kitti import geometry as g  # noqa: E402
from kitti import io as kio  # noqa: E402

# 꼭짓점 0-3 바닥(y=0), 4-7 윗면(y=-h). 각 면의 꼭짓점 세 개(같은 면 위).
FACES: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2),  # 바닥
    (4, 5, 6),  # 윗면
    (0, 1, 5),  # x = +l/2
    (2, 3, 7),  # x = -l/2
    (0, 3, 7),  # z = +w/2
    (1, 2, 6),  # z = -w/2
)


def box_corners(h: float, w: float, l: float, x: float, y: float, z: float, ry: float) -> np.ndarray:
    """devkit compute_box_3d와 같은 8개 꼭짓점 (8, 3), 카메라 rect 좌표."""
    c, s = np.cos(ry), np.sin(ry)
    rot = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    xc = np.array([l / 2, l / 2, -l / 2, -l / 2, l / 2, l / 2, -l / 2, -l / 2])
    yc = np.array([0.0, 0.0, 0.0, 0.0, -h, -h, -h, -h])
    zc = np.array([w / 2, -w / 2, -w / 2, w / 2, w / 2, -w / 2, -w / 2, w / 2])
    return (rot @ np.vstack([xc, yc, zc])).T + np.array([x, y, z])


def inside_by_faces(points: np.ndarray, corners: np.ndarray, eps: float = config.EPS) -> np.ndarray:
    """꼭짓점으로 만든 6개 면의 바깥 단위 법선 반공간 판정. 참 = 내부(경계 + eps 포함)."""
    center = corners.mean(axis=0)
    inside = np.ones(points.shape[0], dtype=bool)
    for i, j, k in FACES:
        a = corners[i]
        n = np.cross(corners[j] - a, corners[k] - a)
        n = n / np.linalg.norm(n)
        if np.dot(center - a, n) > 0:  # 중심 쪽이면 뒤집어 바깥을 향하게
            n = -n
        inside &= (points - a) @ n <= eps
    return inside


def independent_counts(points_rect: np.ndarray, objects: list[dict]) -> list[int | None]:
    """features와 같은 유효 3D 규칙으로 객체별 내부 포인트 수(무효 = None)."""
    out: list[int | None] = []
    for o in objects:
        if not g.is_valid_3d(o["class"], o["h"], o["w"], o["l"], o["z"]):
            out.append(None)
            continue
        corners = box_corners(o["h"], o["w"], o["l"], o["x"], o["y"], o["z"], o["ry"])
        out.append(int(np.count_nonzero(inside_by_faces(points_rect, corners))))
    return out


def features_counts(frame_id: int, root: str | None, feats_dir: str | None) -> dict[int, int | None]:
    if feats_dir:
        import pyarrow.dataset as ds

        t = ds.dataset(feats_dir, format="parquet").to_table(
            columns=["object_id", "points_in_box"], filter=ds.field("frame_id") == frame_id
        )
    else:
        t = kf.frame_features(frame_id, root)
    return dict(zip(t.column("object_id").to_pylist(), t.column("points_in_box").to_pylist()))


def check_frame(frame_id: int, root: str | None, feats_dir: str | None = None,
                stats: dict | None = None) -> list[str]:
    """한 장면을 점검해 불일치 설명 목록(빈 목록 = 일치)을 돌려준다.

    stats(dict)를 주면 n_objects(라벨 줄 수)·n_valid_3d(다시 센 객체 수)를 채운다.
    """
    objects = kio.parse_label(kio.frame_path(root, "label_2", frame_id))
    if stats is not None:
        stats.update(n_objects=len(objects), n_valid_3d=0)
    if not objects:
        return []
    calib = kio.parse_calib(kio.frame_path(root, "calib", frame_id))
    problem = kf.velodyne_problem(root, frame_id)
    if problem is not None:  # 점검할 포인트가 없으면 통과로 치지 않는다
        return [f"frame {frame_id}: velodyne {problem}; cannot check"]
    velo = kio.read_velodyne(kio.frame_path(root, "velodyne", frame_id))
    ref = independent_counts(g.velo_to_rect(velo[:, :3].astype(np.float64), calib), objects)
    got = features_counts(frame_id, root, feats_dir)
    if stats is not None:
        stats["n_valid_3d"] = sum(r is not None for r in ref)
    bad = []
    for o, r in zip(objects, ref):
        f = got.get(int(o["object_id"]), "missing")
        if f != r:
            bad.append(f"frame {frame_id} object {o['object_id']} {o['class']}: independent={r} features={f}")
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F-8 독립 점검 (devkit 꼭짓점 + 면 반공간)")
    parser.add_argument("frame_ids", nargs="*", type=int)
    parser.add_argument("--root", default=None, help="KITTI 루트 (기본 KITTI_ROOT)")
    parser.add_argument("--object-features", default=None, help="비교할 object_features Parquet 폴더")
    parser.add_argument("--frames-file", default=None, help="장면 목록 파일(한 줄 하나, # 주석)")
    parser.add_argument("--out", default=None, help="결과 JSON 경로 (예: results/f8_independent_check.json)")
    args = parser.parse_args(argv)
    frame_ids = list(args.frame_ids)
    if args.frames_file:
        frame_ids += kf.read_frame_ids(args.frames_file)
    if not frame_ids:
        parser.error("no frame ids (give frame ids or --frames-file)")
    all_bad: list[str] = []
    per_frame = []
    for fid in frame_ids:
        stats: dict = {}
        bad = check_frame(fid, args.root, args.object_features, stats)
        print(f"{'PASS' if not bad else 'FAIL'} frame {fid}")
        for line in bad:
            print("  " + line)
        all_bad += bad
        per_frame.append({"frame_id": fid, **stats, "pass": not bad, "mismatches": bad})
    print(f"overall={'PASS' if not all_bad else 'FAIL'} frames={len(frame_ids)} mismatches={len(all_bad)}")
    if args.out:
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "check": "F-8 points_in_box independent recount",
            "method": "KITTI devkit compute_box_3d corners + 6 face half-space test (outward unit normals, "
                      f"eps={config.EPS}); kitti.geometry.points_in_box_mask is not used",
            "compared_against": (config.display_path(args.object_features) if args.object_features
                                 else "kitti.features.frame_features (recomputed)"),
            "frames_file": args.frames_file,
            "n_frames": len(frame_ids),
            "n_objects": sum(f.get("n_objects", 0) for f in per_frame),
            "n_valid_3d_compared": sum(f.get("n_valid_3d", 0) for f in per_frame),
            "n_mismatches": len(all_bad),
            "pass": not all_bad,
            "frames": per_frame,
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0 if not all_bad else 1


if __name__ == "__main__":
    sys.exit(main())
