"""Optional single-GPU GGUF runner using the official stable-diffusion.cpp CLI.

The CLI loads weights for each job; this is not a persistent inference process.
No shell is used. The existing queue/export code owns generated files.
"""

import json
import os
import queue
import re
import subprocess
import tempfile
import threading
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


class SdcppPipeline:
    def __init__(self, root, gpu_id=0, executable=None):
        self.root = Path(root).resolve()
        found = list((self.root / "runtime").rglob("sd-cli.exe"))
        self.executable = (
            Path(executable) if executable else (found[0] if len(found) == 1 else None)
        )
        preset_path = self.root / "preset.json"
        saved = (
            json.loads(preset_path.read_text(encoding="utf-8"))
            if preset_path.is_file()
            else {}
        )
        quant = os.environ.get("QWEN_GGUF_QUANT") or saved.get("quant", "q4")
        if quant not in ("q4", "q5"):
            raise ValueError("Unknown GGUF preset; choose q4 or q5")
        self.quantization = "Q4_K_M" if quant == "q4" else "Q5_K_M"
        self.files = {
            "diffusion": self.root
            / ("qwen-image-2.1-turbo-" + self.quantization + ".gguf"),
            "llm": self.root / "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
            "vision": self.root / "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
            "vae": self.root / "vae" / "qwen_image_2.1_vae_bf16.safetensors",
        }
        missing = [str(p) for p in self.files.values() if not p.is_file()]
        if self.executable is None or not self.executable.is_file():
            missing.append("official stable-diffusion.cpp CUDA sd-cli.exe")
        if missing:
            raise RuntimeError("GGUF setup incomplete: " + ", ".join(missing))
        self.gpu_id = gpu_id
        self.hf_device_map = {"sdcpp": gpu_id}

    def command(self, prompt, output, width, height, seed, references=()):
        args = [
            str(self.executable),
            "--diffusion-model",
            str(self.files["diffusion"]),
            "--llm",
            str(self.files["llm"]),
            "--vae",
            str(self.files["vae"]),
            "--prompt",
            prompt,
            "--width",
            str(width),
            "--height",
            str(height),
            "--seed",
            str(seed),
            "--steps",
            "8",
            "--cfg-scale",
            "1",
            "--sampling-method",
            "euler",
            "--sigmas",
            "qwen-image-2.1-turbo",
            "--backend",
            "cuda0",
            "--params-backend",
            "cuda0",
            "--auto-fit",
            "off",
            "--flash-attn",
            "on",
            "--output",
            str(output),
        ]
        if references:
            args += ["--llm_vision", str(self.files["vision"])]
            for ref in references:
                args += ["-r", str(ref)]
        return args

    def __call__(
        self,
        prompt,
        width,
        height,
        generator,
        image=None,
        callback_on_step_end=None,
        cancel_check=None,
        **kwargs,
    ):
        with tempfile.TemporaryDirectory(prefix="qwen-gguf-") as tmp:
            folder = Path(tmp)
            references = []
            for i, ref in enumerate(image or []):
                name = folder / f"reference-{i}.png"
                ref.save(name)
                references.append(name)
            output = folder / "result.png"
            args = self.command(
                prompt, output, width, height, generator.initial_seed(), references
            )
            env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(self.gpu_id))
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            events = queue.Queue()
            tail = deque(maxlen=35)

            def read_output():
                try:
                    for line in proc.stdout:
                        events.put(line.rstrip())
                finally:
                    events.put(None)

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            step = 0
            try:
                stream_done = False
                while not stream_done or proc.poll() is None:
                    if cancel_check and cancel_check() and callback_on_step_end:
                        # The server callback raises its CancelledError.
                        callback_on_step_end(None, max(0, step - 1), None, {})
                    try:
                        line = events.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if line is None:
                        stream_done = True
                        continue
                    tail.append(line)
                    print("[GGUF] " + line, flush=True)
                    match = re.search(r"(\d+)\s*/\s*8\b", line)
                    if match and "ETA" in line and int(match[1]) > step:
                        step = min(8, int(match[1]))
                        if callback_on_step_end:
                            callback_on_step_end(None, step - 1, None, {})
                if proc.wait() != 0 or not output.is_file():
                    raise RuntimeError(
                        "stable-diffusion.cpp failed:\n" + "\n".join(tail)
                    )
                with Image.open(output) as result:
                    return SimpleNamespace(images=[result.copy()])
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                proc.stdout.close()
                reader.join(timeout=2)
