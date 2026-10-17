#!/usr/bin/env python3
"""Turn one image into a GLB with a local TripoSR checkout.

Koma does not ship the weights. This script is the adapter the app runs when
it finds TripoSR under ~/.local/share/koma/backends/triposr (or KOMA_TRIPOSR_DIR).

    python3 extras/mesh/triposr_glb.py image.png --output model.glb

The first run lets TripoSR download stabilityai/TripoSR into the Hugging Face
cache. A GPU with about 6 GB free is comfortable. CPU works and is slow.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def checkout() -> Path | None:
    candidates = []
    override = os.environ.get("KOMA_TRIPOSR_DIR", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(Path.home() / ".local" / "share" / "koma" / "backends" / "triposr")
    for path in candidates:
        if (path / "run.py").is_file() or (path / "tsr").is_dir():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Image to GLB via TripoSR")
    parser.add_argument("image")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = checkout()
    if root is None:
        print(
            "TripoSR is not installed. Clone https://github.com/VAST-AI-Research/TripoSR "
            "into ~/.local/share/koma/backends/triposr and install its requirements. "
            "Weights are not part of Koma.",
            file=sys.stderr,
        )
        return 2
    sys.path.insert(0, str(root))
    try:
        import torch
        from PIL import Image
        from tsr.system import TSR
    except Exception as error:
        print(f"TripoSR's Python packages are not installed ({error}).", file=sys.stderr)
        print("Install the requirements.txt from the TripoSR checkout. CPU PyTorch is enough to start.", file=sys.stderr)
        return 2
    image_path = Path(args.image)
    if not image_path.is_file():
        print("The reference image is missing.", file=sys.stderr)
        return 2
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("TripoSR is running on CPU. This can take several minutes.", file=sys.stderr)
    model = TSR.from_pretrained(
        "stabilityai/TripoSR",
        config_name="config.yaml",
        weight_name="model.ckpt",
    )
    model.renderer.set_chunk_size(8192)
    model.to(device)
    image = Image.open(image_path).convert("RGB")
    with torch.no_grad():
        scene = model([image], device=device)
    meshes = model.extract_mesh(scene, has_vertex_color=False, resolution=256)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    meshes[0].export(str(target))
    if not target.is_file():
        print("TripoSR finished without a model file.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        print(f"TripoSR failed ({error}).", file=sys.stderr)
        raise SystemExit(1) from error
