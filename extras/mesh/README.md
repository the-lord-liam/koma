# Local 3D backend

Koma's default 3D provider lifts one picture into a mesh on your machine. The weights are not in this repository.

TripoSR is the one Koma looks for. The code and the weights are MIT-licensed. A GPU with about 6 GB free is comfortable. CPU works; a character can take several minutes. Install a PyTorch build that matches the machine (CPU, or CUDA if you have an NVIDIA GPU).

```bash
git clone https://github.com/VAST-AI-Research/TripoSR.git ~/.local/share/koma/backends/triposr
python3 -m pip install --user torch --index-url https://download.pytorch.org/whl/cpu
python3 -m pip install --user -r ~/.local/share/koma/backends/triposr/requirements.txt
```

When that folder exists, Koma runs `extras/mesh/triposr_glb.py` for you. The first run downloads `stabilityai/TripoSR` into the Hugging Face cache. Nothing from the character is uploaded.

Another image-to-3D tool can be used instead. In Settings, set **Local 3D command** to a program that accepts:

```text
command image.png --output model.glb
```

An OBJ written to that path is converted to GLB. `KOMA_MESH_CMD` and `KOMA_TRIPOSR_DIR` override the defaults.

Meshy and Tripo are separate. They are hosted, they need your own key, and they are not the default.
