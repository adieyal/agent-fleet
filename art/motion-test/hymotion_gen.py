"""Generate the HY-Motion test clips (NPZ + FBX) for the motion test.

Runs inside the HY-Motion checkout's venv, from the checkout root (its code uses
relative paths for stats and the FBX template):

    cd ~/tools/HY-Motion-1.0 && USE_HF_MODELS=1 .venv/bin/python \
        <repo>/art/motion-test/hymotion_gen.py \
        --model ~/.cache/hymotion/tencent/HY-Motion-1.0 \
        --out ~/.local/state/fleet/renovation/hymotion/HY-Motion-1.0

Text encoding (Qwen3-8B + CLIP-L) runs first and the encoder is freed before the
DiT loads, so peak VRAM is max(encoder, DiT) rather than their sum.
--encoder-device offload keeps the 16 GB bf16 Qwen in CPU RAM and streams it
layer by layer to the GPU; use it when another process holds part of the 3090.
(cpu works but bf16 matmul on this CPU is impractically slow.)
Timings and peak VRAM per prompt go to <out>/results.json.
"""

import argparse
import json
import time
from pathlib import Path

import torch
import yaml
from accelerate import cpu_offload

from hymotion.utils.loaders import load_object
from hymotion.utils.smplh2woodfbx import SMPLH2WoodFBX
from hymotion.utils.visualize_mesh_web import save_visualization_data

# slug, prompt, seconds
PROMPTS = [
    ("a_typing", "a person sits at a desk typing on a keyboard", 5.0),
    ("b_stand_up", "a person stands up from a chair", 3.0),
    ("c_sit_down", "a person sits down on a chair", 3.0),
    ("d_walk", "a person walks forward at a normal relaxed pace", 4.0),
    ("e_nod_yes", "a seated person nods yes", 3.0),
    ("f_shake_no", "a seated person shakes their head no", 3.0),
    ("g_slump", "a seated person slumps, tired and stuck", 4.0),
    ("h_read_book", "a seated person reads a book held in both hands, head down", 5.0),
    ("i_thumbs_up", "a standing person gives a thumbs up", 3.0),
]
SEEDS = [11, 22, 33]


def encode_prompts(cfg: dict, device: str, takes: int) -> tuple[dict, dict]:
    args = cfg["train_pipeline_args"]
    t0 = time.perf_counter()
    encoder = load_object(args["text_encoder_module"], args["text_encoder_cfg"])
    if device == "offload":
        # Qwen weights stay in CPU RAM and stream to the GPU one layer at a time.
        encoder.sentence_emb_text_encoder.to("cuda:0")
        cpu_offload(encoder.llm_text_encoder, execution_device=torch.device("cuda:0"))
    else:
        encoder.to(device)
    load_s = time.perf_counter() - t0
    feats, secs = {}, {}
    with torch.no_grad():
        for slug, prompt, _ in PROMPTS:
            t0 = time.perf_counter()
            vtxt, ctxt, length = encoder.encode(text=[prompt] * takes)
            secs[slug] = time.perf_counter() - t0
            feats[slug] = {
                "text_vec_raw": vtxt.cpu(),
                "text_ctxt_raw": ctxt.cpu(),
                "text_ctxt_raw_length": length.cpu(),
            }
    peak_mb = torch.cuda.max_memory_allocated() / 2**20 if device != "cpu" else 0
    del encoder
    torch.cuda.empty_cache()
    return feats, {"load_s": load_s, "per_prompt_s": secs, "peak_vram_mb": round(peak_mb)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True, help="dir with config.yml and latest.ckpt")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--encoder-device", default="cpu")
    ap.add_argument("--takes", type=int, default=3)
    ap.add_argument("--only", nargs="*", help="slugs to run (default: all)")
    a = ap.parse_args()
    if a.only:
        PROMPTS[:] = [p for p in PROMPTS if p[0] in a.only]
    seeds = SEEDS[: a.takes]
    cfg = yaml.safe_load((a.model / "config.yml").read_text())
    a.out.mkdir(parents=True, exist_ok=True)

    feats, enc_timing = encode_prompts(cfg, a.encoder_device, a.takes)
    print(f">>> encoded {len(feats)} prompts on {a.encoder_device}: {enc_timing}")

    dev = torch.device("cuda:0")
    torch.cuda.reset_peak_memory_stats(dev)
    t0 = time.perf_counter()
    pipe = load_object(
        cfg["train_pipeline"],
        cfg["train_pipeline_args"],
        network_module=cfg["network_module"],
        network_module_args=cfg["network_module_args"],
    )
    pipe.load_in_demo(str(a.model / "latest.ckpt"), build_text_encoder=False)
    pipe.to(dev).eval()
    dit_load_s = time.perf_counter() - t0
    weights_mb = torch.cuda.memory_allocated(dev) / 2**20
    fbx = SMPLH2WoodFBX()

    clips = []
    for slug, prompt, seconds in PROMPTS:
        hidden = {k: v.to(dev) for k, v in feats[slug].items()}
        torch.cuda.reset_peak_memory_stats(dev)
        torch.cuda.synchronize(dev)
        t0 = time.perf_counter()
        out = pipe.generate(prompt, seeds, seconds, cfg_scale=5.0, hidden_state_dict=hidden)
        torch.cuda.synchronize(dev)
        gen_s = time.perf_counter() - t0
        peak_mb = torch.cuda.max_memory_allocated(dev) / 2**20
        data, _ = save_visualization_data(out, prompt, prompt, slug, str(a.out), slug)
        t0 = time.perf_counter()
        files = []
        for i, smpl in enumerate(data["smpl_data"]):
            path = a.out / f"{slug}_{i:03d}.fbx"
            if fbx.convert_npz_to_fbx(smpl, str(path)):
                files.append(path.name)
        clips.append({
            "slug": slug,
            "prompt": prompt,
            "seconds": seconds,
            "seeds": seeds,
            "generate_s": round(gen_s, 2),
            "encode_s": round(enc_timing["per_prompt_s"][slug], 2),
            "fbx_export_s": round(time.perf_counter() - t0, 2),
            "peak_vram_mb": round(peak_mb),
            "fbx": files,
        })
        print(f">>> {slug}: {gen_s:.1f}s for {len(seeds)} takes, peak {peak_mb:.0f} MB, {len(files)} fbx")

    (a.out / "results.json").write_text(json.dumps({
        "model": a.model.name,
        "encoder_device": a.encoder_device,
        "encoder_load_s": round(enc_timing["load_s"], 1),
        "encoder_peak_vram_mb": enc_timing["peak_vram_mb"],
        "dit_load_s": round(dit_load_s, 1),
        "dit_weights_vram_mb": round(weights_mb),
        "gpu": torch.cuda.get_device_name(dev),
        "clips": clips,
    }, indent=2))


if __name__ == "__main__":
    main()
