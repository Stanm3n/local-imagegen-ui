import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import launch


class ModelChoiceTests(unittest.TestCase):
    def test_q4_default_and_original_download_default(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(launch, "CHOICE", Path(tmp) / "choice.json"),
        ):
            self.assertEqual(launch.select_model(), "q4")
            self.assertEqual(launch.select_model(default="bf16"), "bf16")

    def test_saved_choice_survives_different_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            choice = Path(tmp) / "choice.json"
            choice.write_text(json.dumps({"model": "q4"}), encoding="utf-8")
            with mock.patch.object(launch, "CHOICE", choice):
                self.assertEqual(launch.select_model(default="bf16"), "q4")
                self.assertEqual(launch.select_model(requested="bf16"), "bf16")

    def test_user_can_choose_original(self):
        with mock.patch("builtins.input", return_value="2"):
            self.assertEqual(launch.select_model(choose=True), "bf16")
