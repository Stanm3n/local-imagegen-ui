"""Simple Windows launcher: choose a model, prepare it, open the local app."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHOICE = ROOT / "models" / "studio-choice.json"


def select_model(requested=None, choose=False, default="q4"):
    if requested:
        return requested
    if choose:
        print("\nWelches Modell möchtest du verwenden?")
        print("1 - Q4: kompakter Download (Standard)")
        print("2 - BF16: Originalmodell")
        answer = input("Auswahl [1]: ").strip()
        if answer not in ("", "1", "2"):
            raise ValueError("Bitte 1 oder 2 auswählen.")
        return "bf16" if answer == "2" else "q4"
    if CHOICE.is_file():
        choice = json.loads(CHOICE.read_text(encoding="utf-8")).get("model", "q4")
        if choice in ("q4", "q5", "bf16"):
            return choice
    return default


def ensure_environment():
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    requirements = ROOT / "requirements.txt"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = ROOT / ".venv" / ".studio-ready"
    if not python.is_file():
        print("Python-Umgebung wird vorbereitet ...", flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    if not stamp.is_file() or stamp.read_text(encoding="utf-8") != fingerprint:
        print(
            "Benötigte Software wird installiert. Das dauert beim ersten Start.",
            flush=True,
        )
        subprocess.check_call(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "torch==2.6.0",
                "torchvision==0.21.0",
                "--index-url",
                "https://download.pytorch.org/whl/cu124",
            ]
        )
        subprocess.check_call(
            [str(python), "-m", "pip", "install", "-r", str(requirements)]
        )
        stamp.write_text(fingerprint, encoding="utf-8")
    return python


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=("q4", "q5", "bf16"))
    parser.add_argument("--choose", action="store_true")
    parser.add_argument("--default-model", choices=("q4", "bf16"), default="q4")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    model = select_model(args.model, args.choose, args.default_model)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "model": model,
                    "automatic_download": True,
                    "backend": "diffusers" if model == "bf16" else "sdcpp",
                }
            )
        )
        return
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError(
            "Bitte Python 3.11 installieren und Start.bat erneut öffnen."
        )
    try:
        urllib.request.urlopen("http://127.0.0.1:7311/api/status", timeout=1).close()
    except OSError:
        pass
    else:
        raise RuntimeError(
            "Local ImageGen UI läuft bereits. Beende zuerst den laufenden Server, um das Modell zu wechseln."
        )
    os.chdir(ROOT)
    python = ensure_environment()
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", QWEN_GPU_ID="0")
    env.pop("CUDA_VISIBLE_DEVICES", None)
    if model == "bf16":
        print("Originalmodell wird bei Bedarf heruntergeladen ...", flush=True)
        model_dir = ROOT / "models" / "Qwen-Image-2.1"
        manifest = json.loads((ROOT / "model-options.json").read_text(encoding="utf-8"))
        code = (
            "from huggingface_hub import snapshot_download;"
            "snapshot_download(repo_id="
            + repr(manifest["bf16"]["repo"])
            + ",revision="
            + repr(manifest["bf16"]["revision"])
            + ",local_dir="
            + repr(str(model_dir))
            + ",allow_patterns=['model_index.json','processor/*','scheduler/*',"
            "'text_encoder/*','transformer/*','vae/*','LICENSE','NOTICE'],token=False)"
        )
        download_env = dict(env, HF_HUB_DISABLE_XET="1", HF_HUB_DOWNLOAD_TIMEOUT="120")
        subprocess.check_call([str(python), "-c", code], env=download_env)
        env.update(
            QWEN_BACKEND="diffusers",
            QWEN_DEVICE_MODE="offload",
            QWEN_MODEL_DIR=str(model_dir),
        )
        command = [str(python), "server.py"]
    else:
        print(
            "Das gewählte Modell und die benötigten Dateien werden vorbereitet ...",
            flush=True,
        )
        subprocess.check_call([str(python), "setup_gguf.py", "--quant", model], env=env)
        env.update(QWEN_BACKEND="sdcpp", QWEN_GGUF_QUANT=model)
        command = [str(python), "server.py", "--model", "turbo"]
    CHOICE.parent.mkdir(parents=True, exist_ok=True)
    CHOICE.write_text(json.dumps({"model": model}), encoding="utf-8")
    print("Modell bereit. Local ImageGen UI startet ...", flush=True)
    subprocess.check_call(command, env=env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print("\nStart nicht abgeschlossen: " + str(exc), file=sys.stderr)
        sys.exit(1)
