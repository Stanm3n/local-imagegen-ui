"""Local ImageGen UI for Qwen-Image 2.1. Run: python server.py [--model turbo] [--no-model]."""

import gc
import hashlib
import io
import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Literal
from urllib.parse import quote

# Configure the allocator before importing torch; Windows lacks expandable segments.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:128")
os.environ.setdefault("HF_ENABLE_PARALLEL_LOADING", "YES")

# Redirected Windows logs otherwise may use cp1252 and fail on Unicode output.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from paths import safe_output_parts

LOGGER = logging.getLogger(__name__)

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.environ.get("QWEN_DATA_DIR", BASE))
os.makedirs(DATA_DIR, exist_ok=True)

MODEL_DIR = os.path.join(BASE, "Qwen-Image-2.1")

TURBO_MODEL_DIR = os.path.join(BASE, "Qwen-Image-2.1-Turbo")

MANIFEST_FILE = os.path.join(DATA_DIR, "art-manifest.json")
MANIFEST_LOCK = threading.Lock()

STATIC_DIR = os.path.join(BASE, "static")

MASTER_DIR = os.path.join(DATA_DIR, "output")

REFS_DIR = os.path.join(DATA_DIR, "refs")

EXPORT_DIR = os.path.abspath(
    os.environ.get("QWEN_EXPORT_DIR", os.path.join(DATA_DIR, "exports"))
)
os.makedirs(EXPORT_DIR, exist_ok=True)
MODEL_DIR = os.environ.get("QWEN_MODEL_DIR", MODEL_DIR)
TURBO_MODEL_DIR = os.environ.get("QWEN_TURBO_MODEL_DIR", TURBO_MODEL_DIR)
NO_MODEL = "--no-model" in sys.argv
BACKEND = os.environ.get("QWEN_BACKEND", "diffusers")
if BACKEND not in ("diffusers", "sdcpp"):
    raise SystemExit("QWEN_BACKEND must be diffusers or sdcpp")
DEVICE_MODE = os.environ.get("QWEN_DEVICE_MODE", "offload")
GPU_ID = int(os.environ.get("QWEN_GPU_ID", "0"))
if DEVICE_MODE not in ("dual", "offload"):
    raise SystemExit("QWEN_DEVICE_MODE must be dual or offload")

# Only the native dialog can register additional export roots.
EXPORT_ROOTS = {"local": EXPORT_DIR}
SELECTED_EXPORT_ID = "local"
SETTINGS_FILE = os.path.join(DATA_DIR, "ui-settings.json")
FOLDER_PICKER_LOCK = threading.Lock()
SAFE_IMAGE_PIXELS = 1536 * 1024


def register_export_folder(path):
    global SELECTED_EXPORT_ID
    resolved = str(Path(path).resolve(strict=True))
    if not Path(resolved).is_dir():
        raise ValueError("Please select an existing folder")
    key = hashlib.sha256(os.path.normcase(resolved).encode("utf-8")).hexdigest()[:20]
    EXPORT_ROOTS[key] = resolved
    SELECTED_EXPORT_ID = key
    return {"id": key, "path": resolved}


try:
    with open(SETTINGS_FILE, encoding="utf-8") as settings:
        register_export_folder(json.load(settings)["export_folder"])
except (OSError, ValueError, KeyError, TypeError):
    pass


def native_folder_picker(initial):
    completed = subprocess.run(
        [sys.executable, os.path.join(BASE, "folder_picker.py"), initial],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        raise RuntimeError("Der Ordnerdialog konnte nicht geöffnet werden.")
    return json.loads(completed.stdout)["path"]


PORT = 7311

# Select Base or Turbo; both use the same local port.

ACTIVE_MODEL = "base"

if "--model" in sys.argv:
    _i = sys.argv.index("--model")

    if len(sys.argv) > _i + 1:
        ACTIVE_MODEL = sys.argv[_i + 1]

if ACTIVE_MODEL not in ("base", "turbo"):
    raise SystemExit(f"Unbekanntes Modell: {ACTIVE_MODEL} (base | turbo)")

ACTIVE_MODEL_DIR = TURBO_MODEL_DIR if ACTIVE_MODEL == "turbo" else MODEL_DIR

# The installed Diffusers revision needs the checkpoint sigma schedule passed explicitly.
TURBO_SIGMAS = None

if ACTIVE_MODEL == "turbo":
    if BACKEND == "sdcpp":
        # The CLI preset supplies this exact official Turbo schedule.
        TURBO_SIGMAS = [
            1.0,
            0.978453,
            0.95418,
            0.926626,
            0.89508,
            0.845148,
            0.704534,
            0.414568,
        ]
    else:
        with open(
            os.path.join(TURBO_MODEL_DIR, "model_index.json"), encoding="utf-8"
        ) as f:
            TURBO_SIGMAS = json.load(f)["sample_sigmas"]

DEFAULT_SEED = 42
DEFAULT_SIZE = 1024
DEFAULT_STEPS = len(TURBO_SIGMAS) if TURBO_SIGMAS else 30

# Qwen-Image-2.1 uses guidance-free sampling.
DEFAULT_CFG = 1.0

DEFAULT_EXPORT_SIZE = 0


# Dual-GPU budgets leave decode headroom; the VAE is pinned to GPU 0 after loading.
GPU_MAX_MEMORY = {0: "21GiB", 1: "23GiB"}

ENABLE_VAE_SLICING = False
ENABLE_VAE_TILING = False  # Tiling introduced a magenta cast in earlier output.
ENABLE_ATTENTION_SLICING = False


MASTER_STYLE = "High-quality illustration, clear composition, consistent lighting."

NEGATIVE_PROMPT = (
    "text, watermark, signature, frame, border, solid background, "
    "black background, white background, low quality, blurry, cropped"
)

# Checkpoint prompt template for RGBA output.
RGBA_PREFIX = "This is an RGBA image with transparency."

RGBA_SUFFIX = "The image has alpha channel and the background is transparent."


def build_final_prompt(user_prompt: str, use_master: bool, use_rgba: bool) -> str:
    desc = f"{MASTER_STYLE} {user_prompt}" if use_master else user_prompt

    if use_rgba:
        return f"{RGBA_PREFIX} {desc} {RGBA_SUFFIX}"

    return desc


def load_manifest():
    """Load valid entries without rewriting the saved library."""
    with MANIFEST_LOCK:
        try:
            content = Path(MANIFEST_FILE).read_bytes()
        except FileNotFoundError:
            return []
    valid, _ = _parse_manifest(content)
    return valid


def _parse_manifest(content):
    try:
        entries = json.loads(content.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        LOGGER.warning("Saved manifest is not readable UTF-8 JSON; file left unchanged")
        raise HTTPException(
            422, "Saved library is not valid UTF-8 JSON. The file was left unchanged."
        ) from None

    if not isinstance(entries, list):
        LOGGER.warning("Saved manifest must contain a list; file left unchanged")
        raise HTTPException(
            422,
            "Saved library must contain a list of entries. The file was left unchanged.",
        )

    valid = []
    for index, entry in enumerate(entries, start=1):
        try:
            valid.append(ManifestEntry.model_validate(entry).model_dump())
        except ValidationError as exc:
            # Log field names, not prompts or the invalid input itself.
            fields = ", ".join(
                ".".join(map(str, error["loc"])) or "entry" for error in exc.errors()
            )
            LOGGER.warning(
                "Skipping invalid manifest entry %d (fields: %s); file left unchanged",
                index,
                fields,
            )
    return valid, len(valid) != len(entries)


def _next_free(path: str) -> str:
    """Add a version suffix instead of overwriting an existing image."""

    if not os.path.exists(path):
        return path

    base, ext = os.path.splitext(path)

    i = 2

    while os.path.exists(f"{base}_v{i}{ext}"):
        i += 1

    return f"{base}_v{i}{ext}"


def alpha_pct(img: Image.Image):
    """Percentage of pixels with alpha at most 2."""

    if img.mode != "RGBA":
        return None

    hist = img.getchannel("A").histogram()

    total = sum(hist)

    transparent = hist[0] + hist[1] + hist[2]

    return round(100.0 * transparent / total, 1)


def clean_transparent_rgb(img: Image.Image, thr: int = 2) -> Image.Image:
    """Clear hidden RGB values under transparent pixels without changing visible pixels."""

    if img.mode != "RGBA":
        return img

    a = np.array(img)
    a[a[..., 3] <= thr, :3] = 0

    return Image.fromarray(a, "RGBA")


_pipe = None
_pipe_error = None
_pipe_ready = threading.Event()


def _print_gpu_info():
    count = torch.cuda.device_count()

    print(f"CUDA GPUs sichtbar: {count}")

    for i in range(count):
        props = torch.cuda.get_device_properties(i)

        total_gb = props.total_memory / (1024**3)

        print(
            f"GPU{i}: "
            f"{props.name} | "
            f"{total_gb:.1f} GiB | "
            f"SM {props.major}.{props.minor}"
        )


def _load_pipeline():
    global _pipe
    global _pipe_error

    try:
        if BACKEND == "sdcpp":
            if ACTIVE_MODEL != "turbo":
                raise RuntimeError("The GGUF preset requires --model turbo")
            from sdcpp_backend import SdcppPipeline

            _pipe = SdcppPipeline(
                os.environ.get(
                    "QWEN_GGUF_DIR", os.path.join(BASE, "models", "gguf-turbo")
                ),
                gpu_id=GPU_ID,
                executable=os.environ.get("QWEN_SDCPP_EXE"),
            )
            print("GGUF Turbo ready: " + _pipe.quantization)
            return
        import accelerate
        import diffusers
        from diffusers import QwenImage21Pipeline

        print("=" * 70)

        print(f"diffusers={diffusers.__version__}")

        print(f"accelerate={accelerate.__version__}")

        print(f"torch={torch.__version__}")

        print(f"torch CUDA={torch.version.cuda}")

        _print_gpu_info()

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA ist nicht verfügbar.")

        if DEVICE_MODE == "dual" and torch.cuda.device_count() < 2:
            raise RuntimeError("Dieses Setup erwartet 2 sichtbare GPUs.")

        print("=" * 70)

        print(
            f"Lade "
            f"{'Qwen-Image-2.1-TURBO (8 Steps)' if ACTIVE_MODEL == 'turbo' else 'Qwen-Image-2.1 (30 Steps)'} "
            f"({DEVICE_MODE}, BF16)..."
        )

        if DEVICE_MODE == "offload":
            pipe = QwenImage21Pipeline.from_pretrained(
                ACTIVE_MODEL_DIR, torch_dtype=torch.bfloat16
            )
            pipe.enable_model_cpu_offload(gpu_id=GPU_ID)
            print(f"Single-GPU CPU offload -> cuda:{GPU_ID}; BF16, no quantization")
        else:
            pipe = QwenImage21Pipeline.from_pretrained(
                ACTIVE_MODEL_DIR,
                torch_dtype=torch.bfloat16,
                device_map="balanced",
                max_memory=GPU_MAX_MEMORY,
            )

        # Relocate VAE weights and inputs together to avoid cross-device encode/decode failures.
        if DEVICE_MODE == "dual":
            from accelerate.hooks import remove_hook_from_module

            remove_hook_from_module(pipe.vae, recurse=True)

            pipe.vae.to("cuda:0")

            _vae_decode = pipe.vae.decode
            _vae_dev = torch.device("cuda:0")

            def _decode_on_gpu0(*args, **kwargs):
                args = tuple(a.to(_vae_dev) if torch.is_tensor(a) else a for a in args)

                kwargs = {
                    k: (v.to(_vae_dev) if torch.is_tensor(v) else v)
                    for k, v in kwargs.items()
                }

                return _vae_decode(*args, **kwargs)

            pipe.vae.decode = _decode_on_gpu0

            # Reference images also pass through the VAE. The pipeline prepares
            # them on its execution device, which can differ from the relocated VAE.
            # Return encoded latents to that original device for concatenation.
            _encode_vae_image = pipe._encode_vae_image

            def _encode_reference_on_gpu0(image, generator):
                input_device = image.device
                encoded = _encode_vae_image(image.to(_vae_dev), generator)
                return encoded.to(input_device)

            pipe._encode_vae_image = _encode_reference_on_gpu0

            print(
                "VAE encode/decode -> GPU 0 (reference latents return to pipeline device)"
            )

        # Log the loader mapping; the VAE relocation above is reported separately.
        actual = getattr(pipe, "hf_device_map", {})

        print("hf_device_map =", actual)

        if ENABLE_VAE_SLICING:
            pipe.enable_vae_slicing()
            print("VAE slicing: ON")

        if ENABLE_VAE_TILING:
            if hasattr(pipe, "vae") and hasattr(pipe.vae, "enable_tiling"):
                pipe.vae.enable_tiling()

            print("VAE tiling: ON")

        if ENABLE_ATTENTION_SLICING:
            pipe.enable_attention_slicing()
            print("Attention slicing: ON")
        else:
            print("Attention slicing: OFF")

        print("=" * 70)
        print("Qwen-Image-2.1 Modell bereit.")
        print("=" * 70)

        _pipe = pipe

    except Exception as e:
        _pipe_error = str(e)

        print(f"Modell-Fehler: {e}")

    finally:
        _pipe_ready.set()


if NO_MODEL:
    _pipe_error = "UI-only mode: no model loaded."
    _pipe_ready.set()
else:
    threading.Thread(target=_load_pipeline, daemon=True).start()


JOBS: dict = {}
MAX_JOB_HISTORY = 100
HISTORY_LOCK = threading.Lock()
TERMINAL_STATUSES = {"done", "error", "cancelled"}


def prune_job_history():
    """Keep the newest terminal metadata; queued/running jobs and files are untouched."""
    with HISTORY_LOCK:
        finished = sorted(
            (
                (key, job)
                for key, job in list(JOBS.items())
                if job["status"] in TERMINAL_STATUSES
            ),
            key=lambda item: (item[1].get("finished_at") or item[1]["created_at"]),
            reverse=True,
        )
        for key, _ in finished[MAX_JOB_HISTORY:]:
            JOBS.pop(key, None)


JOB_QUEUE: "queue.Queue" = queue.Queue()


class CancelledError(Exception):
    pass


class GenerateReq(BaseModel):
    file: str

    prompt: str

    seed: int = DEFAULT_SEED

    width: int = Field(default=DEFAULT_SIZE, ge=256, le=3072)
    height: int = Field(default=DEFAULT_SIZE, ge=256, le=3072)

    steps: int = Field(default=DEFAULT_STEPS, ge=1, le=100)

    cfg: float = DEFAULT_CFG

    export_size: int | None = Field(
        default=DEFAULT_EXPORT_SIZE,
        ge=0,
        le=3072,
        validation_alias=AliasChoices("export_size", "game_size"),
    )

    use_master: bool = False
    use_rgba: bool = False

    dir: str = ""

    export_id: str | None = None
    allow_large: bool = False

    refs: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value):
        return safe_output_parts(value, "")[0]

    @field_validator("dir")
    @classmethod
    def validate_dir(cls, value):
        return safe_output_parts("image.png", value)[1]


def enqueue(req: GenerateReq) -> str:
    prune_job_history()
    if req.export_id is not None and req.export_id != SELECTED_EXPORT_ID:
        raise HTTPException(
            409, "Der Ausgabeordner wurde geändert. Bitte lade die Oberfläche neu."
        )
    if req.width * req.height > SAFE_IMAGE_PIXELS and not req.allow_large:
        raise HTTPException(
            422,
            "Hohe Auflösung: wähle ein kleineres Format oder aktiviere große Formate in den erweiterten Einstellungen.",
        )

    job_id = uuid.uuid4().hex[:8]

    JOBS[job_id] = {
        "id": job_id,
        "model": ACTIVE_MODEL,
        "backend": BACKEND,
        "quantization": getattr(_pipe, "quantization", "GGUF")
        if BACKEND == "sdcpp"
        else "BF16",
        "file": req.file,
        "status": "queued",
        "final_prompt": build_final_prompt(req.prompt, req.use_master, req.use_rgba),
        "seed": req.seed,
        "width": req.width,
        "height": req.height,
        "refs": req.refs,
        "steps": req.steps,
        "cfg": req.cfg,
        "export_size": req.export_size,
        "export_id": SELECTED_EXPORT_ID,
        "export_root": EXPORT_ROOTS[SELECTED_EXPORT_ID],
        "dir": ((req.dir or "").strip().strip("/\\")),
        "step": 0,
        "total_steps": req.steps,
        "cancel_requested": False,
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "took_s": None,
        "alpha_pct": None,
        "image_url": None,
        "export_path": None,
        "error": None,
    }

    JOB_QUEUE.put(job_id)

    return job_id


def _save_job_images(job, image):
    master_path = _next_free(os.path.join(MASTER_DIR, job["file"]))
    image.save(master_path)
    job["alpha_pct"] = alpha_pct(image)

    export_dir = os.path.join(job["export_root"], job["dir"])
    os.makedirs(export_dir, exist_ok=True)
    export_path = _next_free(os.path.join(export_dir, job["file"]))
    if job["export_size"]:
        width, height = image.size
        scale = job["export_size"] / max(width, height)
        with image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS
        ) as resized:
            resized.save(export_path)
    else:
        image.save(export_path)

    job["export_path"] = export_path
    relative = "/".join(filter(None, (job["dir"], os.path.basename(export_path))))
    job["image_url"] = (
        f"/api/exports/{job['export_id']}/{quote(relative, safe='/')}?t={time.time_ns()}"
    )


def _run_job(job):
    job["status"] = "running"
    job["started_at"] = time.time()
    result = image = generator = None
    ref_images = []
    kwargs = {}
    print(
        f"Start {job['file']} (seed {job['seed']}, {job['steps']} steps, {len(job['refs'])} refs)"
    )
    try:
        for name in job["refs"]:
            path = os.path.join(REFS_DIR, os.path.basename(name))
            if os.path.exists(path):
                with Image.open(path) as reference:
                    ref_images.append(clean_transparent_rgb(reference.convert("RGBA")))

        generator = torch.Generator(device="cuda").manual_seed(job["seed"])

        def on_step(pipe, step_index, timestep, callback_kwargs):
            job["step"] = step_index + 1
            if job["cancel_requested"]:
                raise CancelledError()
            # Release cached denoising blocks before the VAE decode allocation.
            if step_index + 1 >= job["total_steps"]:
                torch.cuda.empty_cache()
            return callback_kwargs

        kwargs = dict(
            prompt=job["final_prompt"],
            height=job["height"],
            width=job["width"],
            num_inference_steps=job["steps"],
            true_cfg_scale=job["cfg"],
            generator=generator,
            callback_on_step_end=on_step,
        )
        if job["cfg"] > 1.0:
            kwargs["negative_prompt"] = NEGATIVE_PROMPT
        if ref_images:
            kwargs["image"] = ref_images
        if ACTIVE_MODEL == "turbo":
            job["steps"] = job["total_steps"] = len(TURBO_SIGMAS)
            job["cfg"] = 1.0
            kwargs.update(
                num_inference_steps=len(TURBO_SIGMAS),
                sigmas=TURBO_SIGMAS,
                true_cfg_scale=1.0,
            )
            kwargs.pop("negative_prompt", None)
        if BACKEND == "sdcpp":
            kwargs["cancel_check"] = lambda: job["cancel_requested"]
        result = _pipe(**kwargs)
        # CUDA work is asynchronous; wait before measuring the completed job.
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        image = clean_transparent_rgb(result.images[0])
        _save_job_images(job, image)
        job["status"] = "done"
        job["finished_at"] = time.time()
        job["took_s"] = round(job["finished_at"] - job["started_at"], 1)
        print(f"Done {job['file']} ({job['took_s']}s)")
    except CancelledError:
        job["status"] = "cancelled"
        print(f"Cancelled {job['file']}")
    except Exception as exc:
        job["status"] = "error"
        if (
            isinstance(exc, torch.cuda.OutOfMemoryError)
            or "out of memory" in str(exc).lower()
        ):
            job["error"] = (
                "GPU-Speicher reicht für diese Einstellung nicht. Versuche 768 × 768 oder 1024 × 1024, CFG 1 und weniger Referenzbilder. Es wurde kein automatischer neuer Versuch gestartet."
            )
            job["error_detail"] = str(exc)
        else:
            job["error"] = str(exc)
        print(f"Error {job['file']}: {exc}")
    finally:
        job["finished_at"] = job["finished_at"] or time.time()
        # Drop pipeline outputs and reference images on error and cancellation as well.
        if image is not None:
            image.close()
        for reference in ref_images:
            reference.close()
        kwargs.clear()
        result = image = generator = None
        ref_images.clear()
        reference = None
        gc.collect()
        prune_job_history()


def _worker():
    _pipe_ready.wait()
    os.makedirs(MASTER_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    while True:
        job_id = JOB_QUEUE.get()
        try:
            job = JOBS.get(job_id)
            if job is None or job["status"] == "cancelled":
                continue
            if _pipe is None:
                job.update(
                    status="error",
                    error=_pipe_error or "Pipeline konnte nicht geladen werden.",
                    finished_at=time.time(),
                )
                prune_job_history()
                continue
            _run_job(job)
        finally:
            JOB_QUEUE.task_done()


threading.Thread(target=_worker, daemon=True).start()


app = FastAPI(title="Local ImageGen UI")
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)


@app.middleware("http")
async def restrict_browser_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and origin
        and origin != str(request.base_url).rstrip("/")
    ):
        return JSONResponse(
            {"detail": "Cross-origin writes are not allowed"}, status_code=403
        )
    return await call_next(request)


@app.get("/api/export-folder")
def api_export_folder():
    return {"id": SELECTED_EXPORT_ID, "path": EXPORT_ROOTS[SELECTED_EXPORT_ID]}


@app.post("/api/export-folder/pick")
def api_pick_export_folder():
    if not FOLDER_PICKER_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Ein Ordnerdialog ist bereits geöffnet.")
    try:
        selected = native_folder_picker(EXPORT_ROOTS[SELECTED_EXPORT_ID])
        if not selected:
            return {**api_export_folder(), "cancelled": True}
        result = register_export_folder(selected)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as settings:
            json.dump({"export_folder": result["path"]}, settings, ensure_ascii=False)
        return {**result, "cancelled": False}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(400, str(exc))
    finally:
        FOLDER_PICKER_LOCK.release()


@app.get("/api/exports/{export_id}/{image_path:path}")
def api_export_image(export_id: str, image_path: str):
    root = EXPORT_ROOTS.get(export_id)
    if root is None:
        raise HTTPException(404, "Unbekannter Exportordner")
    resolved_root = Path(root).resolve()
    target = (resolved_root / image_path).resolve()
    if (
        not target.is_relative_to(resolved_root)
        or target.suffix.lower() != ".png"
        or not target.is_file()
    ):
        raise HTTPException(404, "Bild nicht gefunden")
    return FileResponse(target)


@app.get("/api/status")
def api_status():
    running = any(j["status"] == "running" for j in list(JOBS.values()))

    return {
        "model": ACTIVE_MODEL,
        "backend": BACKEND,
        "quantization": getattr(_pipe, "quantization", "GGUF")
        if BACKEND == "sdcpp"
        else None,
        "device_mode": "single" if BACKEND == "sdcpp" else DEVICE_MODE,
        "gpu_id": GPU_ID if DEVICE_MODE == "offload" or BACKEND == "sdcpp" else None,
        "default_steps": DEFAULT_STEPS,
        "default_cfg": DEFAULT_CFG,
        "model_ready": _pipe is not None,
        "model_error": _pipe_error,
        "running": running,
        "queue_len": sum(j["status"] == "queued" for j in list(JOBS.values())),
        "gpu_map": (getattr(_pipe, "hf_device_map", {}) if _pipe is not None else {}),
    }


@app.get("/api/manifest")
def api_get_manifest():
    return {
        "entries": load_manifest(),
        "config": {
            "master_style": MASTER_STYLE,
            "negative_prompt": NEGATIVE_PROMPT,
            "rgba_prefix": RGBA_PREFIX,
            "rgba_suffix": RGBA_SUFFIX,
        },
    }


class ManifestEntry(GenerateReq):
    done: bool = False


class ManifestReq(BaseModel):
    entries: list[dict]


@app.post("/api/manifest")
def api_save_manifest(req: ManifestReq):
    clean = []

    for e in req.entries:
        f = str(e.get("file", "")).strip()

        if not f:
            continue

        if not f.lower().endswith(".png"):
            f += ".png"

        try:
            parsed = ManifestEntry(**{**e, "file": f})
        except ValueError:
            raise HTTPException(
                422,
                "Invalid manifest entry: check filename, folder and generation settings",
            )
        e = parsed.model_dump()
        e["file"] = parsed.file

        e["prompt"] = str(e.get("prompt", ""))

        clean.append(e)

    content = json.dumps(clean, ensure_ascii=False, indent=2).encode("utf-8")
    with MANIFEST_LOCK:
        _backup_damaged_manifest()
        _replace_manifest_file(MANIFEST_FILE, content)

    return {"ok": True, "entries": clean}


def _backup_damaged_manifest():
    manifest = Path(MANIFEST_FILE)
    try:
        original = manifest.read_bytes()
    except FileNotFoundError:
        return

    try:
        _, damaged = _parse_manifest(original)
    except HTTPException:
        damaged = True

    if damaged:
        backup = _next_free(str(manifest.with_suffix(".bak")))
        _replace_manifest_file(backup, original)


def _replace_manifest_file(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=os.path.dirname(path), suffix=".tmp", delete=False
        ) as fh:
            temporary = fh.name
            fh.write(content)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)


@app.post("/api/generate")
def api_generate(req: GenerateReq):
    if _pipe is None:
        raise HTTPException(503, _pipe_error or "Model is still loading")
    job_id = enqueue(req)

    return {
        "job_id": job_id,
        "queue_len": sum(j["status"] == "queued" for j in list(JOBS.values())),
    }


class BatchReq(BaseModel):
    mode: Literal["missing", "all"]


@app.post("/api/batch")
def api_batch(req: BatchReq):
    ids = []

    for e in load_manifest():
        e_dir = (e.get("dir", "") or "").strip().strip("/\\")

        if req.mode == "missing" and os.path.exists(
            os.path.join(EXPORT_ROOTS[SELECTED_EXPORT_ID], e_dir, e.get("file", ""))
        ):
            continue

        ids.append(
            enqueue(
                GenerateReq(
                    file=e.get("file", ""),
                    prompt=e.get("prompt", ""),
                    seed=e.get("seed", DEFAULT_SEED),
                    width=e.get("width", DEFAULT_SIZE),
                    height=e.get("height", DEFAULT_SIZE),
                    steps=e.get("steps", DEFAULT_STEPS),
                    cfg=e.get("cfg", DEFAULT_CFG),
                    export_size=e.get("export_size", DEFAULT_EXPORT_SIZE),
                    refs=e.get("refs", []),
                    use_master=e.get("use_master", False),
                    use_rgba=e.get("use_rgba", False),
                    allow_large=e.get("allow_large", False),
                    dir=e_dir,
                )
            )
        )

    return {"queued": len(ids), "jobs": ids}


@app.get("/api/refs")
def api_refs():
    os.makedirs(REFS_DIR, exist_ok=True)

    names = sorted(
        f
        for f in os.listdir(REFS_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    )

    return [{"name": n, "url": f"/ref-images/{n}"} for n in names]


@app.post("/api/refs/upload")
async def api_ref_upload(request: Request, name: str = ""):
    upload = bytearray()
    async for chunk in request.stream():
        upload.extend(chunk)
        if len(upload) > 16 * 1024 * 1024:
            raise HTTPException(413, "Reference image exceeds 16 MiB")
    body = bytes(upload)
    try:
        with Image.open(io.BytesIO(body)) as image:
            image.verify()
    except Exception:
        raise HTTPException(400, "Invalid reference image")

    safe = os.path.basename(name or "ref.png")

    if not safe.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        safe += ".png"

    os.makedirs(REFS_DIR, exist_ok=True)

    with open(os.path.join(REFS_DIR, safe), "wb") as f:
        f.write(body)

    return {"ok": True, "name": safe, "url": f"/ref-images/{safe}"}


@app.delete("/api/refs/{name}")
def api_ref_delete(name: str):
    p = os.path.join(REFS_DIR, os.path.basename(name))

    if os.path.exists(p):
        os.remove(p)

        return {"ok": True}

    return {"ok": False}


@app.delete("/api/jobs/history")
def api_clear_job_history():
    """Discard terminal job metadata only. Never touches image files or active jobs."""
    finished = [
        key
        for key, job in list(JOBS.items())
        if job["status"] in ("done", "error", "cancelled")
    ]
    for key in finished:
        JOBS.pop(key, None)
    return {"ok": True, "removed": len(finished)}


@app.get("/api/jobs")
def api_jobs():
    prune_job_history()

    jobs = sorted(list(JOBS.values()), key=lambda j: j["created_at"], reverse=True)[:30]

    return {"jobs": jobs}


@app.delete("/api/job/{job_id}")
def api_cancel(job_id: str):
    job = JOBS.get(job_id)

    if not job:
        return {"ok": False, "error": "unbekannter Job"}

    if job["status"] == "queued":
        job["status"] = "cancelled"
        job["finished_at"] = time.time()

        return {"ok": True}

    if job["status"] == "running":
        job["cancel_requested"] = True

        return {"ok": True, "note": "Abbruch beim nächsten Step"}

    return {"ok": False, "error": "Job ist bereits fertig"}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


os.makedirs(MASTER_DIR, exist_ok=True)

os.makedirs(REFS_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.mount("/export-images", StaticFiles(directory=EXPORT_DIR), name="exports")

app.mount("/masters", StaticFiles(directory=MASTER_DIR), name="masters")

app.mount("/ref-images", StaticFiles(directory=REFS_DIR), name="refs")


if __name__ == "__main__":
    threading.Timer(2.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()

    print(f"Local ImageGen UI [{ACTIVE_MODEL}]: http://127.0.0.1:{PORT}")

    print("Modell lädt im Hintergrund...")

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
