"""Smoke tests without loading weights or running inference."""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import weakref
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from paths import safe_output_parts


class OutputPaths(unittest.TestCase):
    def test_relative_subfolders(self):
        self.assertEqual(
            safe_output_parts("example", "icons/items"), ("example.png", "icons/items")
        )

    def test_rejects_escaping_and_windows_special_names(self):
        for filename, folder in [
            ("../outside.png", ""),
            ("C:\\outside.png", ""),
            ("a.png", "../private"),
            ("a.png", "/absolute"),
            ("a.png", "C:\\temp"),
            ("CON.png", ""),
            ("a:stream", ""),
            ("a.png", "folder/.."),
            ("a.png", "folder."),
        ]:
            with (
                self.subTest(filename=filename, folder=folder),
                self.assertRaises(ValueError),
            ):
                safe_output_parts(filename, folder)


class ApiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_data = os.environ.get("QWEN_DATA_DIR")
        cls.old_export = os.environ.pop("QWEN_EXPORT_DIR", None)
        os.environ["QWEN_DATA_DIR"] = cls.temp.name
        original_argv = sys.argv[:]
        sys.argv = ["server.py", "--no-model"]
        try:
            spec = importlib.util.spec_from_file_location(
                "qwen_test_server", ROOT / "server.py"
            )
            cls.server = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.server)
        finally:
            sys.argv = original_argv
        cls.client = TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.temp.cleanup()
        if cls.old_data is None:
            os.environ.pop("QWEN_DATA_DIR", None)
        else:
            os.environ["QWEN_DATA_DIR"] = cls.old_data
        if cls.old_export is not None:
            os.environ["QWEN_EXPORT_DIR"] = cls.old_export

    def test_ui_and_status_without_model(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertFalse(self.client.get("/api/status").json()["model_ready"])
        self.assertEqual(
            self.client.post(
                "/api/generate", json={"file": "a", "prompt": "test"}
            ).status_code,
            503,
        )

    def test_manifest_round_trip_and_rejection(self):
        payload = {
            "entries": [{"file": "example", "prompt": "A paper kite", "dir": "drafts"}]
        }
        self.assertEqual(
            self.client.post("/api/manifest", json=payload).status_code, 200
        )
        self.assertEqual(
            self.client.get("/api/manifest").json()["entries"][0]["file"], "example.png"
        )
        invalid = {"entries": [{"file": "../escape", "prompt": "test"}]}
        self.assertEqual(
            self.client.post("/api/manifest", json=invalid).status_code, 422
        )
        self.assertEqual(
            self.client.get("/api/manifest").json()["entries"][0]["file"], "example.png"
        )

    def test_completed_flag_and_zero_seed_survive_reload(self):
        entry = {"file": "completed", "prompt": "test", "done": True, "seed": 0}
        self.assertEqual(
            self.client.post("/api/manifest", json={"entries": [entry]}).status_code,
            200,
        )
        saved = self.client.get("/api/manifest").json()["entries"][0]
        self.assertTrue(saved["done"])
        self.assertEqual(saved["seed"], 0)
        saved["done"] = False
        self.client.post("/api/manifest", json={"entries": [saved]})
        self.assertFalse(self.client.get("/api/manifest").json()["entries"][0]["done"])

    def test_clear_list_preserves_generated_files(self):
        image_path = Path(self.server.MASTER_DIR) / "keep.png"
        export_path = Path(self.server.EXPORT_DIR) / "keep.png"
        reference_path = Path(self.server.REFS_DIR) / "keep.png"
        for path in (image_path, export_path, reference_path):
            Image.new("RGB", (4, 4), "blue").save(path)
        before = {
            path: path.read_bytes()
            for path in (image_path, export_path, reference_path)
        }
        self.client.post(
            "/api/manifest", json={"entries": [{"file": "keep", "prompt": "test"}]}
        )
        self.assertEqual(
            self.client.post("/api/manifest", json={"entries": []}).status_code, 200
        )
        self.assertEqual(self.client.get("/api/manifest").json()["entries"], [])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        reference_path.unlink()  # remove only this test fixture

    def test_clear_history_keeps_active_jobs_and_images(self):
        history_image = Path(self.server.MASTER_DIR) / "history-keep.png"
        Image.new("RGB", (4, 4), "red").save(history_image)
        previous = self.server.JOBS.copy()
        try:
            self.server.JOBS.clear()
            for status in ("done", "error", "cancelled", "running", "queued"):
                self.server.JOBS[status] = {
                    "id": status,
                    "status": status,
                    "created_at": 1,
                }
            response = self.client.delete("/api/jobs/history")
            self.assertEqual(response.json()["removed"], 3)
            self.assertEqual(set(self.server.JOBS), {"running", "queued"})
            self.assertEqual(self.client.get("/api/status").json()["queue_len"], 1)
            self.client.delete("/api/job/queued")
            self.assertEqual(self.client.get("/api/status").json()["queue_len"], 0)
            self.assertTrue(history_image.exists())
        finally:
            self.server.JOBS.clear()
            self.server.JOBS.update(previous)

    def test_folder_picker_cancel_selection_and_scoped_preview(self):
        original_id = self.server.SELECTED_EXPORT_ID
        original_roots = self.server.EXPORT_ROOTS.copy()
        try:
            current = self.client.get("/api/export-folder").json()
            with mock.patch.object(
                self.server, "native_folder_picker", return_value=""
            ):
                self.assertTrue(
                    self.client.post("/api/export-folder/pick").json()["cancelled"]
                )
            self.assertEqual(self.client.get("/api/export-folder").json(), current)
            destination = Path(self.temp.name) / "selected-export"
            destination.mkdir(exist_ok=True)
            with mock.patch.object(
                self.server, "native_folder_picker", return_value=str(destination)
            ):
                response = self.client.post("/api/export-folder/pick")
            self.assertEqual(response.status_code, 200)
            selected = response.json()
            self.assertEqual(Path(selected["path"]), destination)
            Image.new("RGB", (4, 4), "green").save(destination / "example.png")
            self.assertEqual(
                self.client.get(
                    "/api/exports/" + selected["id"] + "/example.png"
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.get(
                    "/api/exports/" + selected["id"] + "/%2e%2e%2Fui-settings.json"
                ).status_code,
                404,
            )
            self.assertEqual(
                self.client.get("/api/exports/unknown/example.png").status_code, 404
            )
            with mock.patch.object(self.server.JOB_QUEUE, "put"):
                job_id = self.server.enqueue(
                    self.server.GenerateReq(
                        file="snapshot", prompt="test", export_id=selected["id"]
                    )
                )
            self.server.SELECTED_EXPORT_ID = original_id
            self.assertEqual(
                self.server.JOBS[job_id]["export_root"], str(destination.resolve())
            )
            self.server.JOBS.pop(job_id)
        finally:
            self.server.SELECTED_EXPORT_ID = original_id
            self.server.EXPORT_ROOTS.clear()
            self.server.EXPORT_ROOTS.update(original_roots)

    def test_large_format_requires_explicit_opt_in(self):
        from fastapi import HTTPException

        request = self.server.GenerateReq(
            file="large", prompt="test", width=2048, height=2048
        )
        with self.assertRaises(HTTPException) as caught:
            self.server.enqueue(request)
        self.assertEqual(caught.exception.status_code, 422)
        request.allow_large = True
        with mock.patch.object(self.server.JOB_QUEUE, "put"):
            job_id = self.server.enqueue(request)
        self.server.JOBS.pop(job_id)

    def test_legacy_export_size_is_migrated_on_read_and_save(self):
        legacy = {"file": "legacy.png", "prompt": "test", "game_size": 512}
        Path(self.server.MANIFEST_FILE).write_text(
            json.dumps([legacy]), encoding="utf-8"
        )
        entry = self.client.get("/api/manifest").json()["entries"][0]
        self.assertEqual(entry["export_size"], 512)
        self.assertNotIn("game_size", entry)
        response = self.client.post("/api/manifest", json={"entries": [legacy]})
        self.assertEqual(response.status_code, 200)
        saved = json.loads(Path(self.server.MANIFEST_FILE).read_text(encoding="utf-8"))[
            0
        ]
        self.assertEqual(saved["export_size"], 512)
        self.assertNotIn("game_size", saved)
        explicit = self.server.GenerateReq(**legacy, export_size=256)
        self.assertEqual(explicit.export_size, 256)

    def test_invalid_manifest_entries_do_not_block_library_or_batch(self):
        entries = [
            {"file": "valid", "prompt": "Keep this", "game_size": 512, "seed": 0},
            {"file": "missing-prompt"},
            {"file": "../escape", "prompt": "private input must not appear in logs"},
            {"file": "oversized", "prompt": "test", "width": 9999},
            None,
            "not an entry",
        ]
        manifest = Path(self.server.MANIFEST_FILE)
        original = json.dumps(entries).encode("utf-8")
        manifest.write_bytes(original)
        with self.assertLogs(self.server.LOGGER, level="WARNING") as captured:
            response = self.client.get("/api/manifest")
        self.assertEqual(response.status_code, 200)
        saved = response.json()["entries"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["file"], "valid.png")
        self.assertEqual(saved[0]["export_size"], 512)
        self.assertEqual(saved[0]["seed"], 0)
        self.assertEqual(len(captured.output), 5)
        self.assertNotIn("private input", "\n".join(captured.output))
        with (
            self.assertLogs(self.server.LOGGER, level="WARNING"),
            mock.patch.object(self.server.JOB_QUEUE, "put"),
        ):
            batch = self.client.post("/api/batch", json={"mode": "all"})
        self.assertEqual(batch.status_code, 200)
        payload = batch.json()
        for job_id in payload["jobs"]:
            self.addCleanup(self.server.JOBS.pop, job_id, None)
        self.assertEqual(payload["queued"], 1)
        self.assertEqual(self.server.JOBS[payload["jobs"][0]]["export_size"], 512)
        self.assertEqual(manifest.read_bytes(), original)

    def test_saving_damaged_manifest_preserves_original_and_older_backup(self):
        payload = {"entries": [{"file": "keep", "prompt": "test"}]}
        first = b'[{"file":"keep","prompt":"test"},{"file":"missing-prompt"}]'
        second = b"{broken JSON"
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "art-manifest.json"
            backup = manifest.with_suffix(".bak")
            with mock.patch.object(self.server, "MANIFEST_FILE", str(manifest)):
                manifest.write_bytes(first)
                self.assertEqual(
                    self.client.get("/api/manifest").json()["entries"][0]["file"],
                    "keep.png",
                )
                self.assertEqual(manifest.read_bytes(), first)
                self.assertFalse(backup.exists())

                for damaged, expected_backup in (
                    (first, backup),
                    (second, manifest.with_name("art-manifest_v2.bak")),
                ):
                    manifest.write_bytes(damaged)
                    with self.assertLogs(self.server.LOGGER, level="WARNING"):
                        saved = self.client.post("/api/manifest", json=payload)
                    self.assertEqual(saved.status_code, 200)
                    self.assertEqual(expected_backup.read_bytes(), damaged)

                saved = self.client.post("/api/manifest", json={"entries": []})
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(json.loads(manifest.read_bytes()), [])
                self.assertEqual(backup.read_bytes(), first)
                self.assertEqual(len(list(manifest.parent.glob("*.bak"))), 2)

    def test_invalid_batch_mode_does_not_enqueue_jobs(self):
        with (
            mock.patch.object(self.server, "load_manifest") as load,
            mock.patch.object(self.server, "enqueue") as enqueue,
        ):
            response = self.client.post("/api/batch", json={"mode": "missng"})
        self.assertEqual(response.status_code, 422)
        load.assert_not_called()
        enqueue.assert_not_called()

    def test_invalid_manifest_document_returns_clear_error_without_overwrite(self):
        manifest = Path(self.server.MANIFEST_FILE)
        for content in (b"{broken JSON", b"{}", b"null", b"\xff"):
            with self.subTest(content=content):
                manifest.write_bytes(content)
                with (
                    self.assertLogs(self.server.LOGGER, level="WARNING"),
                    mock.patch.object(self.server.JOB_QUEUE, "put") as put,
                ):
                    loaded = self.client.get("/api/manifest")
                    batch = self.client.post("/api/batch", json={"mode": "all"})
                self.assertEqual(loaded.status_code, 422)
                self.assertEqual(batch.status_code, 422)
                self.assertIn("left unchanged", loaded.json()["detail"])
                self.assertEqual(manifest.read_bytes(), content)
                put.assert_not_called()

    def test_history_is_bounded_without_removing_active_jobs_or_files(self):
        previous = self.server.JOBS.copy()
        image = Path(self.server.MASTER_DIR) / "retained.png"
        Image.new("RGB", (4, 4)).save(image)
        try:
            self.server.JOBS.clear()
            for number in range(105):
                self.server.JOBS[str(number)] = {
                    "id": str(number),
                    "status": "done",
                    "created_at": number,
                    "finished_at": number + 1,
                }
            for status in ("queued", "running"):
                self.server.JOBS[status] = {
                    "id": status,
                    "status": status,
                    "created_at": 1,
                }
            self.client.get("/api/jobs")
            self.assertEqual(len(self.server.JOBS), 102)
            self.assertIn("queued", self.server.JOBS)
            self.assertIn("running", self.server.JOBS)
            self.assertNotIn("0", self.server.JOBS)
            self.assertIn("104", self.server.JOBS)
            self.assertTrue(image.exists())
        finally:
            self.server.JOBS.clear()
            self.server.JOBS.update(previous)

    def make_worker_job(self, **settings):
        request = self.server.GenerateReq(file="worker", prompt="test", **settings)
        with mock.patch.object(self.server.JOB_QUEUE, "put"):
            job_id = self.server.enqueue(request)
        self.addCleanup(self.server.JOBS.pop, job_id, None)
        return self.server.JOBS[job_id]

    def test_worker_success_versions_masters_and_resizes_exports(self):
        job = self.make_worker_job(export_size=4)
        master = Path(self.server.MASTER_DIR) / job["file"]
        Image.new("RGB", (8, 8), "red").save(master)
        original = master.read_bytes()

        def inference(**kwargs):
            return type("Result", (), {"images": [Image.new("RGB", (8, 4), "blue")]})()

        with (
            mock.patch.object(self.server, "_pipe", inference),
            mock.patch.object(self.server.torch, "Generator"),
            mock.patch.object(
                self.server.torch.cuda, "is_available", return_value=False
            ),
        ):
            self.server._run_job(job)
        self.assertEqual(job["status"], "done")
        self.assertEqual(master.read_bytes(), original)
        self.assertTrue(master.with_stem("worker_v2").exists())
        with Image.open(job["export_path"]) as image:
            self.assertEqual(image.size, (4, 2))
        self.assertTrue(job["image_url"].startswith("/api/exports/"))

    def test_worker_releases_output_and_references_after_export_failure(self):
        job = self.make_worker_job(refs=["worker-ref.png"])
        reference = Path(self.server.REFS_DIR) / "worker-ref.png"
        Image.new("RGB", (8, 8), "green").save(reference)
        self.addCleanup(reference.unlink, missing_ok=True)
        held = {}

        def inference(**kwargs):
            result = type("Result", (), {})()
            result.images = [Image.new("RGB", (8, 8))]
            held["result"] = weakref.ref(result)
            held["reference"] = weakref.ref(kwargs["image"][0])
            return result

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(self.server, "_pipe", inference))
            stack.enter_context(mock.patch.object(self.server.torch, "Generator"))
            stack.enter_context(
                mock.patch.object(
                    self.server.torch.cuda, "is_available", return_value=False
                )
            )
            stack.enter_context(
                mock.patch.object(
                    self.server, "_save_job_images", side_effect=OSError("disk full")
                )
            )
            self.server._run_job(job)
        self.assertEqual(job["status"], "error")
        self.assertIn("disk full", job["error"])
        self.assertIsNone(held["result"]())
        self.assertIsNone(held["reference"]())

    def test_worker_cancellation_keeps_existing_images(self):
        job = self.make_worker_job()
        job["cancel_requested"] = True

        def inference(**kwargs):
            kwargs["callback_on_step_end"](None, 0, None, {})
            self.fail("Cancelled callback returned")

        with (
            mock.patch.object(self.server, "_pipe", inference),
            mock.patch.object(self.server.torch, "Generator"),
            mock.patch.object(self.server, "_save_job_images") as save,
        ):
            self.server._run_job(job)
        self.assertEqual(job["status"], "cancelled")
        self.assertIsNotNone(job["finished_at"])
        save.assert_not_called()

    def test_references_validate_and_delete(self):
        self.assertEqual(
            self.client.post(
                "/api/refs/upload?name=bad.png", content=b"not an image"
            ).status_code,
            400,
        )
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
        self.assertEqual(
            self.client.post(
                "/api/refs/upload?name=sample.png", content=buf.getvalue()
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/refs").json()[0]["name"], "sample.png")
        self.assertTrue(self.client.delete("/api/refs/sample.png").json()["ok"])

    def test_foreign_browser_origin_and_host_are_rejected(self):
        self.assertEqual(
            self.client.post(
                "/api/manifest",
                json={"entries": []},
                headers={"Origin": "https://untrusted.example"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/status", headers={"Host": "untrusted.example"}
            ).status_code,
            400,
        )


if __name__ == "__main__":
    unittest.main()
