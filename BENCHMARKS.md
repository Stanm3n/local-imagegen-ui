# Benchmarks

Recorded image-generation runs for Local ImageGen UI on an RTX 3090 workstation.

## Single-GPU reference · Turbo Q5

**15.1 seconds** for one **1024 × 1024** product poster on **one RTX 3090 24 GB**.

| Setting | Recorded configuration |
| --- | --- |
| Model | Qwen-Image-2.1 Turbo · Unsloth Q5_K_M |
| GPU | 1 × NVIDIA RTX 3090 · 24 GB |
| Sampling | 8 steps · CFG 1 · checkpoint sigma schedule |
| Engine | stable-diffusion.cpp `master-1031-c6dfa9d` |
| Attention | Flash Attention |
| Device placement | CUDA GPU 0 · automatic fallback disabled |
| Highest sampled board memory | 16,717 MiB · approximately 16.3 GiB |

The text encoder uses Qwen3-VL Q4_K_M with the matching F16 vision projector and BF16 VAE. Generation time includes per-job CLI/model startup and excludes downloads and environment setup.

Board memory was sampled approximately once per second and includes other use. It is not an exact CUDA allocation peak or a measurement on a 16-GB card.

## Gallery results · Original BF16

The current gallery contains **11 recorded results**: campaign images, a product-color edit and a two-reference outfit transfer. All use Qwen-Image-2.1 Base BF16 with CFG 1.

| Gallery settings | Recorded range |
| --- | --- |
| Image size | 1024 × 1024 or 832 × 1216 |
| Sampling | 30–40 steps |
| Generation time | 39.2–113.9 seconds per result |
| Workstation | 2 × RTX 3090 · 24 GB each |

The workstation has two GPUs, but some runs used single-GPU CPU offload. Recorded device modes are included in `docs/data.js`; the table does not represent a uniform dual-GPU benchmark.

<details open>
<summary><strong>All recorded gallery runs</strong></summary>

| Result | Resolution | Steps | CFG | Seed | Seconds |
| --- | --- | --- | --- | --- | --- |
| Kanzlei-Auftritt: Klarheit, wenn es zählt | 1024 × 1024 | 40 | 1 | 112201 | 53.7 |
| Kanzlei-Podcast: Recht einfach | 1024 × 1024 | 40 | 1 | 112202 | 54.7 |
| Kanzlei-Recruiting: Gute Argumente, gute Kollegen | 1024 × 1024 | 40 | 1 | 112203 | 62.5 |
| IT-Sicherheit: Dein Backup, dein Plan B | 1024 × 1024 | 40 | 1 | 112204 | 63.4 |
| KI-Workshop: Weniger Hype, mehr Workflow | 1024 × 1024 | 40 | 1 | 112211 | 63.1 |
| MORGENROT — ein guter Morgen | 1024 × 1024 | 30 | 1 | 11201 | 40.5 |
| MORA — weniger, aber besser | 1024 × 1024 | 30 | 1 | 11202 | 39.2 |
| NACHTSCHICHT — Schwarz und Orange | 1024 × 1024 | 40 | 1 | 112218 | 113.9 |
| RAUMWERK — Farbe macht Räume | 1024 × 1024 | 40 | 1 | 112217 | 74.3 |
| Produktfarbe ändern | 1024 × 1024 | 40 | 1 | 112207 | 76.3 |
| Outfit aus zwei Referenzen | 1216 × 864 | 40 | 1 | 112210 | 93.4 |

</details>

## Additional run · Turbo BF16

A separate single-GPU CPU-offload run took **30.8 seconds**, with approximately **23 GiB** highest sampled board memory. It used a different engine from the Q5 reference, so the timing difference does not isolate quantization effects.

## Measurement scope

These are individual local runs, not a controlled comparison of models or engines. Download and environment setup time are excluded. Reference edits may change more details than requested.

**Q4 is the default download.** Its selection and download plan are tested; a separate Q4 inference measurement and a fresh-machine installation remain pending. The measured quantized reference above uses Q5.
