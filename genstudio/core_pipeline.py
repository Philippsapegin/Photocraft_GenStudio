"""Direct, in-process inference using Comfy's checkpoint-compatible GPL model library.

No ComfyUI server, node graph, frontend or custom nodes are started or imported.
The pinned library is kept separately from any user's ComfyUI installation.
"""
import importlib.util
from pathlib import Path
import sys

from genfill.comfy import Cancelled
from .models import resolve_components
from .settings import validate_components
from .settings import ROOT

CORE = ROOT / ".runtime/qwen-core"


def inpaint_references(image, mask):
    """Keep image transparency and the white-to-edit mask as different inputs."""
    from PIL import Image
    if image.size != mask.size:
        raise ValueError("Image and mask sizes differ.")
    coverage = mask.convert("L").point(lambda value: 255 if value else 0)
    if coverage.getbbox() is None:
        raise ValueError("The edit mask is empty.")
    # Alpha is image content for Qwen 2.1, not an inpainting mask. Erase hidden
    # source RGB and give the missing area an opaque, neutral placeholder.
    reference = Image.composite(Image.new("RGBA", image.size, (127, 127, 127, 255)),
                                image.convert("RGBA"), coverage)
    return reference, coverage.convert("RGBA")


def inpaint_prompt(prompt):
    return ("Edit image 1 using the black-and-white mask in image 2. "
            "White in image 2 marks the region to replace; black marks the region to preserve. "
            "The neutral gray area in image 1 is a placeholder for missing content, not an object. "
            + prompt + " Return only the edited image 1, without the mask or placeholder. "
            "Make the replaced region fully opaque and blend it naturally with its surroundings.")


def import_core(cpu=False, core=CORE):
    if not (core / "comfy/sd.py").is_file():
        raise RuntimeError("Direct model library is missing. Run scripts/Setup-Direct.ps1.")
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from comfy.cli_args import args
    args.cpu = cpu
    args.disable_dynamic_vram = True
    args.disable_async_offload = True
    args.disable_pinned_memory = True
    args.disable_comfy_compiler = True
    args.disable_cuda_graphs = True
    args.reserve_vram = 1.0
    import comfy.sd
    import comfy.sample
    return comfy.sd, comfy.sample


class DirectPipeline:
    def __init__(self, models, text_encoder="", vae="", *, components=None):
        self.models = Path(models)
        self.components = validate_components(components if components is not None else {"textEncoder": text_encoder, "vae": vae})
        self.text_encoder, self.vae = self.components["textEncoder"], self.components["vae"]
        self.pipe = None

    def check(self):
        problems = [f"Missing dependency: {name}" for name in
                    ("torch", "transformers", "safetensors", "comfy_kitchen", "einops", "scipy")
                    if importlib.util.find_spec(name) is None]
        if not (CORE / "comfy/sd.py").is_file():
            problems.append("Direct model library is missing. Run scripts/Setup-Direct.ps1.")
        try:
            resolve_components(self.models, self.components)
        except (ValueError, OSError) as error:
            problems.append(str(error))
        # No torch imports, CUDA initialization, weights loading or downloads here.
        return problems

    def load(self, progress):
        if self.pipe is not None:
            return self.pipe
        problems = self.check()
        if problems:
            raise RuntimeError("\n".join(problems))
        files = resolve_components(self.models, self.components)
        sd, _ = import_core()
        import comfy.utils
        progress("Loading Qwen Image 2.1 components…")
        model = sd.load_diffusion_model(files["diffusion_model"]["path"], disable_dynamic=True)
        if model is None:
            raise RuntimeError("The selected diffusion model is unsupported.")
        model.model_options.setdefault("transformer_options", {})["qwen_image21_cache"] = {"device": "cpu", "dtype": "int8"}
        clip = sd.load_clip([files["text_encoder"]["path"]], clip_type=sd.CLIPType.QWEN_IMAGE, disable_dynamic=True)
        vae = sd.VAE(sd=comfy.utils.load_torch_file(files["vae"]["path"], safe_load=True))
        self.pipe = model, clip, vae
        return self.pipe

    def generate_studio(self, references, seed, prompt, width, height, cancel, progress):
        """Qwen 2.1 generation: zero target latent and up to ten image slots."""
        if cancel.is_set():
            raise Cancelled()
        if len(references) > 10 or width % 32 or height % 32 or not 256 <= width <= 2048 or not 256 <= height <= 2048:
            raise ValueError("Invalid Qwen generation dimensions or reference count.")
        model, clip, vae = self.load(progress)
        import numpy as np
        import torch
        import comfy.sample
        import comfy.samplers
        from PIL import Image
        from genfill.workflow import TURBO_SIGMAS

        def check():
            if cancel.is_set():
                raise Cancelled()

        def step(index, denoised, noisy, total):
            check()
            progress(f"Qwen Image 2.1 · {index + 1}/{total}")

        with torch.inference_mode():
            images, latents = [], []
            for index, reference in enumerate(references):
                check()
                progress(f"Encoding image {index + 1}…")
                pixels = torch.from_numpy(np.array(reference.convert("RGBA")).astype(np.float32) / 255.0).unsqueeze(0)
                images.append(pixels[..., :3] * pixels[..., 3:] + 1.0 - pixels[..., 3:])
                latents.append(vae.encode(pixels))
            check()
            conditioning = clip.encode_from_tokens_scheduled(clip.tokenize(
                prompt, images=images, keep_vision=not latents, prevent_empty_text=True))
            positive = [[embedding, {**metadata, **({"reference_latents": latents} if latents else {})}]
                        for embedding, metadata in conditioning]
            latent = torch.zeros((1, 64, height // 16, width // 16), dtype=torch.float32, device="cpu")
            noise = comfy.sample.prepare_noise(latent, seed)
            check()
            samples = comfy.sample.sample_custom(model, noise, 1.0, comfy.samplers.sampler_object("euler"),
                torch.tensor(TURBO_SIGMAS, dtype=torch.float32), positive, positive, latent,
                callback=step, disable_pbar=True, seed=seed)
            check()
            decoded = vae.decode(samples).detach().float().cpu().numpy()[0]
            return Image.fromarray((np.clip(decoded, 0, 1) * 255).round().astype(np.uint8)).convert("RGBA")

    def generate(self, image, mask, seed, prompt, cancel, progress):
        if cancel.is_set():
            raise Cancelled()
        reference, mask_reference = inpaint_references(image, mask)
        model, clip, vae = self.load(progress)
        from .lora import apply_constant
        model, clip = apply_constant(model, clip, self.models, self.components, progress)
        import numpy as np
        import torch
        import comfy.sample
        import comfy.samplers
        import comfy.model_management
        from PIL import Image
        from genfill.workflow import TURBO_SIGMAS

        def cancelled():
            if cancel.is_set():
                raise Cancelled()

        def step(index, denoised, noisy, total):
            cancelled()
            progress(f"Qwen Image 2.1 · {index + 1}/{total}")

        with torch.inference_mode():
            pixels = torch.from_numpy(np.array(reference).astype(np.float32) / 255.0).unsqueeze(0)
            rgb = pixels[..., :3] * pixels[..., 3:] + 1.0 - pixels[..., 3:]
            mask_pixels = torch.from_numpy(np.array(mask_reference).astype(np.float32) / 255.0).unsqueeze(0)
            progress("Encoding reference image…")
            cancelled()
            reference_latent = vae.encode(pixels)
            cancelled()
            mask_latent = vae.encode(mask_pixels)
            cancelled()
            conditioning = clip.encode_from_tokens_scheduled(clip.tokenize(
                inpaint_prompt(prompt), images=[rgb, mask_pixels[..., :3]],
                keep_vision=False, prevent_empty_text=True))
            positive = [[embedding, {**metadata, "reference_latents": [reference_latent, mask_latent]}]
                        for embedding, metadata in conditioning]
            # CFG=1 never evaluates the negative branch.
            negative = positive
            # Real masked sampling: keep the image latent outside the edit mask,
            # regenerate white pixels with full noise. A max reduction keeps even
            # a one-pixel stroke when mapping the pixel mask onto the VAE grid.
            latent = reference_latent.to(device="cpu")
            noise_mask = torch.nn.functional.adaptive_max_pool2d(
                mask_pixels[..., :1].movedim(-1, 1), latent.shape[-2:])
            noise = comfy.sample.prepare_noise(latent, seed)
            progress("Qwen Image 2.1 · generating…")
            cancelled()
            samples = comfy.sample.sample_custom(model, noise, 1.0, comfy.samplers.sampler_object("euler"),
                       torch.tensor(TURBO_SIGMAS, dtype=torch.float32), positive, negative, latent,
                       noise_mask=noise_mask, callback=step, disable_pbar=True, seed=seed)
            cancelled()
            decoded = vae.decode(samples).detach().float().cpu().numpy()[0]
            output = Image.fromarray((np.clip(decoded, 0, 1) * 255).round().astype(np.uint8))
            output = output.convert("RGBA")
            # A fill restores pixels, rather than making an invisible generated
            # layer that merely reveals the unchanged original beneath it.
            output.putalpha(Image.composite(Image.new("L", output.size, 255),
                            output.getchannel("A"), mask_reference.getchannel("R")))
            return output
