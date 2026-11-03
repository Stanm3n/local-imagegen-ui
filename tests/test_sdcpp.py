"""Boundary tests for the optional CLI backend; no weights or GPUs required."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from sdcpp_backend import SdcppPipeline


class CliBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in (
            "runtime/sd-cli.exe",
            "qwen-image-2.1-turbo-Q4_K_M.gguf",
            "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
            "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
            "vae/qwen_image_2.1_vae_bf16.safetensors",
        ):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        self.pipe = SdcppPipeline(self.root)

    def test_prompt_is_one_argument_and_single_gpu_is_explicit(self):
        prompt = 'A dog; $(echo no) & "quoted text"'
        args = self.pipe.command(prompt, "out.png", 1024, 832, 0, ["photo.png"])
        self.assertEqual(args[args.index("--prompt") + 1], prompt)
        self.assertEqual(args[args.index("--backend") + 1], "cuda0")
        self.assertEqual(args[args.index("--params-backend") + 1], "cuda0")
        self.assertEqual(args[args.index("--auto-fit") + 1], "off")
        self.assertIn("--llm_vision", args)
        self.assertEqual(args[args.index("-r") + 1], "photo.png")

    def test_default_is_q4(self):
        self.assertEqual(self.pipe.quantization, "Q4_K_M")
        self.assertTrue(str(self.pipe.files["diffusion"]).endswith("Q4_K_M.gguf"))

    def test_saved_q5_preset_is_respected(self):
        (self.root / "qwen-image-2.1-turbo-Q5_K_M.gguf").touch()
        (self.root / "preset.json").write_text('{"quant":"q5"}', encoding="utf-8")
        pipe = SdcppPipeline(self.root)
        self.assertEqual(pipe.quantization, "Q5_K_M")

    @mock.patch("sdcpp_backend.subprocess.Popen")
    def test_rgba_survives_cli_result_and_temporary_directory(self, popen):
        process = mock.Mock()
        process.stdout = io.StringIO("|=====>| 1/8 - ETA 00:01\n")
        process.poll.return_value = 0
        process.wait.return_value = 0
        output_paths = []

        def launch(args, **kwargs):
            out = Path(args[args.index("--output") + 1])
            output_paths.append(out)
            Image.new("RGBA", (16, 16), (40, 20, 10, 0)).save(out)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["env"]["CUDA_VISIBLE_DEVICES"], "0")
            return process

        popen.side_effect = launch
        generator = mock.Mock()
        generator.initial_seed.return_value = 0
        result = self.pipe(prompt="test", width=512, height=512, generator=generator)
        self.assertEqual(result.images[0].getchannel("A").getextrema(), (0, 0))
        self.assertFalse(output_paths[0].exists())

    @mock.patch("sdcpp_backend.subprocess.Popen")
    def test_cancellation_terminates_child_process(self, popen):
        process = mock.Mock()
        process.stdout = io.StringIO("")
        process.poll.return_value = None

        def terminate():
            process.poll.return_value = -15

        process.terminate.side_effect = terminate
        popen.return_value = process
        generator = mock.Mock()
        generator.initial_seed.return_value = 42

        class Cancelled(Exception):
            pass

        def stop(*args):
            raise Cancelled()

        with self.assertRaises(Cancelled):
            self.pipe(
                prompt="test",
                width=512,
                height=512,
                generator=generator,
                cancel_check=lambda: True,
                callback_on_step_end=stop,
            )
        process.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
