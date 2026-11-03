# Local HTTP API

Base URL: `http://127.0.0.1:7311`. The application must be running with its selected model loaded. The GUI and agents share the same server, queue, references and export folder.

Interactive documentation is available at `/docs`, with the schema at `/openapi.json`, on the running local app. The public GitHub Pages gallery serves static files only.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | Model readiness, backend, model errors and sampling defaults |
| GET | `/api/export-folder` | Currently selected export folder: `id` and local `path` |
| POST | `/api/generate` | Enqueue one image; returns `job_id` and `queue_len` |
| GET | `/api/jobs` | `jobs` array containing the newest 30 records |
| DELETE | `/api/job/{job_id}` | Cancel a queued job or request cancellation of a running job |
| GET | `/api/refs` | List uploaded references with `name` and `url` |
| POST | `/api/refs/upload?name=reference.png` | Upload raw image bytes; returns the stored `name` |
| GET | `/api/exports/{export_id}/{image_path}` | Retrieve an exported PNG; use the returned job's `image_url` |

## Submit and retrieve one image

This standard-library Python example submits one job and downloads the completed PNG. Run it after starting the application. It performs an actual image-generation request when used with a loaded model.

```python
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:7311"


def api(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = Request(BASE + path, data=data, headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


status = api("/api/status")
if not status["model_ready"]:
    raise RuntimeError(status.get("model_error") or "Model is still loading")

folder = api("/api/export-folder")
submitted = api("/api/generate", {
    "file": "agent-poster.png",
    "prompt": "A minimalist coffee poster with warm morning light",
    "width": 1024,
    "height": 1024,
    "steps": status["default_steps"],
    "cfg": status["default_cfg"],
    "export_id": folder["id"],
    "refs": [],
})
job_id = submitted["job_id"]
print("Submitted:", job_id)

deadline = time.monotonic() + 600
while time.monotonic() < deadline:
    job = next((item for item in api("/api/jobs")["jobs"]
                if item["id"] == job_id), None)
    if job and job["status"] == "done":
        # Exclusive creation preserves an existing local download.
        with urlopen(BASE + job["image_url"], timeout=30) as response:
            with Path("agent-result.png").open("xb") as output:
                output.write(response.read())
        print("Server export:", job["export_path"])
        break
    if job and job["status"] in ("error", "cancelled"):
        raise RuntimeError(job.get("error") or job["status"])
    time.sleep(2)
else:
    raise TimeoutError(f"Still waiting for {job_id}; not cancelled or resubmitted")
```

`export_path` is a path on the server's computer. `agent-result.png` is an additional download in the client's current directory. If the download already exists, choose another filename. The generated server file remains available.

## Reference-image editing

Upload image bytes directly, without multipart form encoding:

```python
reference = Path("reference.png")
request = Request(
    BASE + "/api/refs/upload?name=agent-reference.png",
    data=reference.read_bytes(),
    headers={"Content-Type": "image/png"},
    method="POST",
)
with urlopen(request, timeout=30) as response:
    stored = json.load(response)
# Use [stored["name"]] as the next generation request's refs array.
```

Choose an unused upload name to preserve other references. Up to ten reference names can be included in `refs`; each upload is limited to 16 MiB and must be a readable image. Their array order is the reference order in the prompt.

## Generation fields

Required fields are `file` and `prompt`. Filenames are PNG names; `dir` is an optional relative subfolder. `export_id` should match the current folder ID. Other fields include `seed`, `width`, `height`, `steps`, `cfg`, `refs`, `export_size`, `use_master`, `use_rgba` and `allow_large`.

`export_size` resizes the exported copy after inference; `null` or `0` keeps its original size. It does not reduce generation VRAM. Width/height validation allows 256–3072 pixels per dimension; formats above 1,572,864 pixels additionally need `allow_large: true`. Practical memory limits depend on the model and hardware.

## Errors and polling

- `503`: the model is unavailable or still loading. Check `/api/status`.
- `409`: the selected output folder changed. Read `/api/export-folder` again and check the intended destination.
- `422`: invalid generation fields or large-format opt-in missing.
- `400` / `413` on reference upload: invalid image / upload exceeds 16 MiB.

Jobs pass through `queued`, `running` and then `done`, `error` or `cancelled`. Match the submitted `job_id`; the first item is not necessarily your job. Only the newest 30 records are returned. If a request times out, submission may already have succeeded; do not automatically duplicate it.
