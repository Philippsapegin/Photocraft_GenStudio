from pathlib import Path
import tempfile
import unittest

from genstudio.models import FILES, resolve


class ModelSourceTests(unittest.TestCase):
    def test_standard_encoder_fallback_and_compressed_encoder_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for key, (name, folders) in FILES.items():
                if key == "text_encoder":
                    name = "qwen3vl_8b_bf16.safetensors"
                path = root / folders[0] / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic, not weights")
            self.assertEqual(resolve(root)["text_encoder"]["name"], "qwen3vl_8b_bf16.safetensors")
            (root / "text_encoders/qwen3vl_8b_w4a8.safetensors").write_bytes(b"synthetic")
            self.assertEqual(resolve(root)["text_encoder"]["name"], "qwen3vl_8b_w4a8.safetensors")


if __name__ == "__main__":
    unittest.main()
