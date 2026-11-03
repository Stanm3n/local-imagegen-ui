"""Download the pinned optional Windows/CUDA GGUF preset. Standard library only."""

import argparse
import concurrent.futures
import hashlib
import json
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "models" / "gguf-turbo"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_download(url, path, size, sha256):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size == size and sha256_file(path) == sha256:
        print("Already verified: " + path.name, flush=True)
        return
    partial = path.with_suffix(path.suffix + ".part")
    retries = 0
    while not partial.exists() or partial.stat().st_size < size:
        offset = partial.stat().st_size if partial.exists() else 0
        end = min(offset + 64 * 1024 * 1024, size) - 1
        try:
            req = urllib.request.Request(
                url, headers={"Range": f"bytes={offset}-{end}"}
            )
            with urllib.request.urlopen(req, timeout=120) as response:
                if offset and (
                    response.status != 206
                    or not response.headers.get("Content-Range", "").startswith(
                        f"bytes {offset}-"
                    )
                ):
                    raise RuntimeError("Server did not honor the resume range")
                if response.status == 206 and not response.headers.get(
                    "Content-Range", ""
                ).startswith(f"bytes {offset}-"):
                    raise RuntimeError("Unexpected download range")
                with partial.open("ab" if offset else "wb") as target:
                    while chunk := response.read(1024 * 1024):
                        target.write(chunk)
            retries = 0
            print(path.name + f" {partial.stat().st_size / size:.0%}", flush=True)
        except Exception:
            retries += 1
            if retries >= 5:
                raise
            time.sleep(2)
    if partial.stat().st_size != size or sha256_file(partial) != sha256:
        raise RuntimeError(
            "Download failed verification: "
            + path.name
            + ". Remove its .part file before retrying."
        )
    partial.replace(path)
    print("Verified: " + path.name, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quant", choices=("q4", "q5"), default="q4")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "gguf-models.json").read_text(encoding="utf-8"))
    manifest["models"][0] = manifest["variants"][args.quant]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "quant": args.quant,
                    "model": manifest["models"][0]["file"],
                    "total_bytes": sum(m["size"] for m in manifest["models"])
                    + sum(a["size"] for a in manifest["runtime_assets"]),
                }
            )
        )
        return
    print("Modell wird vorbereitet: " + args.quant.upper())
    print(
        "Model license: https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo/blob/main/LICENSE"
    )
    DEST.mkdir(parents=True, exist_ok=True)
    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for model in manifest["models"]:
            tasks.append(
                pool.submit(
                    checked_download,
                    model["url"],
                    DEST / model["file"],
                    model["size"],
                    model["sha256"],
                )
            )
        for asset in manifest["runtime_assets"]:
            tasks.append(
                pool.submit(
                    checked_download,
                    asset["browser_download_url"],
                    DEST / asset["name"],
                    asset["size"],
                    asset["digest"].removeprefix("sha256:"),
                )
            )
        for task in concurrent.futures.as_completed(tasks):
            task.result()
    runtime = DEST / "runtime"
    runtime.mkdir(exist_ok=True)
    for asset in manifest["runtime_assets"]:
        with zipfile.ZipFile(DEST / asset["name"]) as archive:
            for entry in archive.infolist():
                if (
                    not (runtime / entry.filename)
                    .resolve()
                    .is_relative_to(runtime.resolve())
                ):
                    raise RuntimeError("Unsafe archive path")
            archive.extractall(runtime)
    (DEST / "preset.json").write_text(
        json.dumps({"quant": args.quant}), encoding="utf-8"
    )
    print("Modell bereit.")


if __name__ == "__main__":
    main()
