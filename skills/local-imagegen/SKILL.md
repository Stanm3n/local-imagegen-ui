---
name: local-imagegen
description: Generate or edit images through the local HTTP API of Local ImageGen UI. Use when an agent should submit image jobs and retrieve PNG results from an already running server.
---

# Local ImageGen UI

Use the existing local server at `http://127.0.0.1:7311`, or the user's specified app address. Read [the API reference](references/api.md) for request examples, fields and response handling. The GitHub Pages demo is a static showcase; it does not run this API.

## Workflow

1. Check `GET /api/status`. Continue when `model_ready` is true. Otherwise report `model_error` or that loading is still in progress. Model installation, downloads and server/model changes are outside an image request unless the user also requested them.
2. Read `GET /api/export-folder` and include its `id` as `export_id`. This snapshots the currently selected destination. Use a simple PNG filename and an optional relative `dir`; the API does not accept an arbitrary export-root path.
3. For editing, upload only the requested images with `POST /api/refs/upload?name=...`, using raw image bytes. Use distinct upload names: uploading an existing name replaces that file. Include the returned names in the generation request's `refs` array, preserving reference order. Uploaded images are not automatically selected; use `refs: []` for text-to-image.
4. Submit `POST /api/generate` with the user's prompt, filename and chosen dimensions. Use `default_steps` and `default_cfg` from status unless the user chose other Base settings. Turbo fixes its sampling settings. The active model is selected when starting the server, not by a per-request `model` field.
5. Retain the returned `job_id` and poll `GET /api/jobs` about every two seconds for that exact ID. On `done`, retrieve the relative `image_url` from the same server and report `export_path` to the user. On `error` or `cancelled`, report that state and its error message.

## Job handling

- Start with 1024 × 1024 when the user has not chosen a format. Resolutions above 1,572,864 pixels require `allow_large: true`; respect the user's chosen size and the server's validation response.
- Poll with a deadline, such as ten minutes, adjustable for the requested workload. If the deadline expires, report the job ID and last known state. An expired client wait does not cancel the job.
- `/api/jobs` returns only the newest 30 jobs. A missing ID is not proof of failure. Do not resubmit after an uncertain response or missing record; that can create duplicate work.
- Cancel with `DELETE /api/job/{job_id}` when the user asks to stop that job. A running job may take until a sampling callback to stop.
- Keep other saved entries, reference files and job history intact. Use `/api/batch` only for an explicit request to process the saved library; individual agent tasks can submit individual jobs.
