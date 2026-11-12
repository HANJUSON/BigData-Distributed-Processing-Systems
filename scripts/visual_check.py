"""표본 장면 시각 검증 그림 (README 7.1 F-8 시각 확인).

장면마다 두 칸을 그린다.
  왼쪽: 카메라 이미지 위 2D 박스(라벨 bbox)
  오른쪽: 위에서 본 rect 좌표(x 오른쪽, z 전방) 위 3D 박스 바닥 사각형과 박스 내부 점

내부 점은 kitti.geometry.points_in_box_mask로 다시 계산하고, 그 개수가
object_features Parquet의 points_in_box와 같은지 확인한다(다르면 exit 1).

사용:
  python scripts/visual_check.py --root ~/data/kitti --work ~/data/kitti/work \
      --frames 0 1000 5000 --out results/figures/f8_visual_check.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pyarrow.dataset as ds  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitti import geometry, io  # noqa: E402

CLASS_COLORS = {
    "Car": "#0072B2", "Van": "#56B4E9", "Truck": "#E69F00", "Pedestrian": "#D55E00",
    "Person_sitting": "#F0E442", "Cyclist": "#009E73", "Tram": "#CC79A7", "Misc": "#7F7F7F",
}
SOURCE = "Source: KITTI Vision Benchmark Suite, 3D object training set (CC BY-NC-SA 3.0)"


def footprint(o: dict) -> np.ndarray:
    """3D 박스 바닥 사각형 꼭짓점 (5,2) rect (x, z). 마지막 점은 첫 점과 같다."""
    c, s = np.cos(o["ry"]), np.sin(o["ry"])
    lx = np.array([1, 1, -1, -1, 1]) * o["l"] / 2
    lz = np.array([1, -1, -1, 1, 1]) * o["w"] / 2
    # points_in_box_mask의 (lx, lz) = R·(dx, dz)의 역변환
    dx = c * lx + s * lz
    dz = -s * lx + c * lz
    return np.stack([o["x"] + dx, o["z"] + dz], axis=1)


def stored_counts(work: Path, frame_id: int) -> dict[int, int | None]:
    t = ds.dataset(work / "parquet" / "object_features").to_table(
        columns=["object_id", "points_in_box"], filter=ds.field("frame_id") == frame_id
    ).to_pylist()
    return {r["object_id"]: r["points_in_box"] for r in t}


def draw_frame(ax_img, ax_bev, root: Path, work: Path, fid: int) -> list[str]:
    errors: list[str] = []
    img = plt.imread(io.frame_path(root, "image_2", fid))
    objs = io.parse_label(io.frame_path(root, "label_2", fid))
    calib = io.parse_calib(io.frame_path(root, "calib", fid))
    pts = io.read_velodyne(io.frame_path(root, "velodyne", fid))
    rect = geometry.velo_to_rect(pts[:, :3].astype(np.float64), calib)
    stored = stored_counts(work, fid)

    ax_img.imshow(img)
    ax_img.set_title(f"frame_id {fid:06d}: camera image + 2D boxes", fontsize=10)
    ax_img.axis("off")

    front = rect[:, 2] > 0
    ax_bev.scatter(rect[front, 0], rect[front, 2], s=0.05, c="#BBBBBB", rasterized=True)
    n_checked = 0
    for o in objs:
        col = CLASS_COLORS.get(o["class"], "#333333")
        if o["is_dontcare"]:
            continue
        x0, y0, x1, y1 = o["bbox_left"], o["bbox_top"], o["bbox_right"], o["bbox_bottom"]
        ax_img.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec=col, lw=1.2))
        if not geometry.is_valid_3d(o["class"], o["h"], o["w"], o["l"], o["z"]):
            continue
        mask = geometry.points_in_box_mask(rect, o["h"], o["w"], o["l"], o["x"], o["y"], o["z"], o["ry"])
        n_in = int(mask.sum())
        want = stored.get(o["object_id"])
        n_checked += 1
        if want != n_in:
            errors.append(f"frame {fid} object {o['object_id']}: recomputed {n_in} != parquet {want}")
        fp = footprint(o)
        ax_bev.plot(fp[:, 0], fp[:, 1], color=col, lw=1.0)
        ax_bev.scatter(rect[mask, 0], rect[mask, 2], s=1.5, c=col, rasterized=True)
        ax_bev.annotate(str(n_in), (o["x"], o["z"]), fontsize=7, ha="center", va="bottom",
                        xytext=(0, 4), textcoords="offset points")
        ax_img.annotate(f"{o['class']} {n_in}", (x0, y0), fontsize=6, color=col,
                        xytext=(0, -2), textcoords="offset points", va="bottom")

    ax_bev.set_xlim(-25, 25)
    ax_bev.set_ylim(0, 60)
    ax_bev.set_aspect("equal")
    ax_bev.set_xlabel("x_rect (m, right)")
    ax_bev.set_ylabel("z_rect (m, forward)")
    ax_bev.set_title(f"BEV: 3D box footprints + inside points ({n_checked} boxes, numbers = points_in_box)",
                     fontsize=9)
    return errors


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--frames", type=int, nargs="+", default=[0, 1000, 5000])
    p.add_argument("--out", default="results/figures/f8_visual_check.png")
    a = p.parse_args(argv)
    root, work = Path(a.root).expanduser(), Path(a.work).expanduser()

    n = len(a.frames)
    fig, axes = plt.subplots(n, 2, figsize=(16, 4.6 * n), gridspec_kw={"width_ratios": [2.4, 1]},
                             squeeze=False)
    errors: list[str] = []
    for i, fid in enumerate(a.frames):
        errors += draw_frame(axes[i, 0], axes[i, 1], root, work, fid)
    handles = [plt.Line2D([], [], color=c, lw=2, label=k) for k, c in CLASS_COLORS.items()]
    fig.legend(handles=handles, loc="lower center", ncol=8, fontsize=8, frameon=False,
               bbox_to_anchor=(0.5, 0.012))
    fig.text(0.01, 0.002, "Inside test: |lx| <= l/2+eps, |lz| <= w/2+eps, -h-eps <= ly <= eps (eps=1e-6 m). "
             + SOURCE, fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    for e in errors:
        print("MISMATCH", e)
    print(f"{'PASS' if not errors else 'FAIL'} frames={a.frames} -> {out}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
