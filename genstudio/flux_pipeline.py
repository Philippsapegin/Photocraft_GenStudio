"""FLUX 3 Klein distilled INT8, using an isolated checkpoint-compatible core.

The prompt upsampler is released before loading the image transformer. No
weights or GPU modules are opened by construction or readiness checks.
"""
import gc
import hashlib
import importlib.util
import importlib.metadata
import json
import math
from pathlib import Path
import sys

from genfill.comfy import Cancelled
from .core_pipeline import import_core, inpaint_references
from .models import FLUX, resolve_components
from .settings import validate_components
from .settings import ROOT

CORE = ROOT / ".runtime/flux-core"


def caption_key(references, prompt, width, height):
    """Top-k=1 expansion is deterministic; reuse it across batch image seeds."""
    key = hashlib.sha256(json.dumps([prompt, width, height, len(references)]).encode())
    for image in references:
        key.update(str(image.size).encode())
        key.update(image.convert("RGBA").tobytes())
    return key.digest()


def expansion_prompt(prompt, references, width, height):
    prompts = json.loads(Path(__file__).with_name("flux3-prompts.json").read_text(encoding="utf-8"))
    boxes = '"bbox"' in prompt or '"src_bbox"' in prompt
    # The official annotation template understands the zero-based reference
    # tokens, including provenance across several differently-sized images.
    kind = "i2i_bbox" if references else "t2i_bbox" if boxes else "t2i"
    return prompts[kind], f"TARGET CANVAS: {width}:{height}\n{prompt}"


def masked_prompt(prompt):
    return ("Edit <ref_image_0> using the black-and-white mask in <ref_image_1>. "
            "White marks only the region to replace; black marks the region to preserve. "
            "The neutral gray patch is missing content, not an object. " + prompt +
            " Return the edited first image with natural opaque content in that region, "
            "without displaying the mask or the gray placeholder. Keep the framing identical.")


class FluxPipeline:
    model_id = FLUX

    def __init__(self, models, text_encoder="", vae="", *, components=None):
        self.models = Path(models)
        self.components = validate_components(components if components is not None else {"textEncoder": text_encoder, "vae": vae})
        self.text_encoder, self.vae = self.components["textEncoder"], self.components["vae"]
        self.pipe = None
        self.caption = None

    def check(self):
        problems = [f"Missing dependency: {name}" for name in
                    ("torch", "transformers", "safetensors", "comfy_kitchen", "comfy_aimdo", "einops", "scipy")
                    if importlib.util.find_spec(name) is None]
        if sys.version_info < (3, 12):
            problems.append("FLUX 3 Direct requires Python 3.12 or newer. Select a compatible GPU Python in Preferences.")
        if not (CORE / "comfy/sd.py").is_file():
            problems.append("FLUX 3 Direct library is missing. Run scripts/Setup-Direct.ps1.")
        overlay = {d.metadata["Name"].lower(): d.version
                   for d in importlib.metadata.distributions(path=[str(CORE / "deps")])}
        for name, minimum in (("comfy-kitchen", (0, 2, 37)), ("comfy-aimdo", (0, 5, 6))):
            try:
                version = overlay.get(name) or importlib.metadata.version(name)
                if tuple(int(n) for n in version.split(".")[:3]) < minimum:
                    problems.append(f"FLUX 3 needs newer {name}. Run scripts/Setup-Direct.ps1.")
            except (importlib.metadata.PackageNotFoundError, ValueError):
                problems.append(f"FLUX 3 dependency {name} is missing. Run scripts/Setup-Direct.ps1.")
        try:
            resolve_components(self.models, self.components, FLUX)
        except (ValueError, OSError) as error:
            problems.append(str(error))
        return problems

    def expand(self, references, seed, prompt, width, height, cancel, progress):
        sd, _ = import_core(core=CORE)
        import comfy.model_management as memory
        import comfy.utils
        # Avoid retaining a previous image pipeline alongside the 9B LLM in RAM.
        memory.unload_all_models()
        self.pipe = None
        gc.collect()
        memory.soft_empty_cache()
        progress("FLUX 3 · expanding prompt…")
        files = resolve_components(self.models, self.components, FLUX)
        llm = sd.load_clip([files["prompt_upsampler"]["path"]], clip_type=sd.CLIPType.FLUX3, disable_dynamic=True)
        previous = comfy.utils.PROGRESS_BAR_HOOK

        def token_progress(value, total, preview=None, **kwargs):
            if cancel.is_set():
                raise Cancelled()

        comfy.utils.set_progress_bar_global_hook(token_progress)
        try:
            if cancel.is_set():
                raise Cancelled()
            system, text = expansion_prompt(prompt, references, width, height)
            tokens = llm.tokenize(text, images=references, min_length=1, thinking=False, system_prompt=system)
            generated = llm.generate(tokens, max_length=3584, do_sample=True, temperature=1.0,
                top_k=1, top_p=0.95, min_p=0.0, repetition_penalty=1.0, presence_penalty=0.0 if references else 1.5,
                seed=seed, mtp=False)
            caption = llm.decode(generated).split("</think>")[-1].strip()
            if not caption:
                raise RuntimeError("FLUX 3 prompt expansion returned an empty caption.")
            if cancel.is_set():
                raise Cancelled()
            return caption
        finally:
            comfy.utils.set_progress_bar_global_hook(previous)
            memory.unload_all_models()
            del llm
            gc.collect()
            memory.soft_empty_cache()

    def load(self, progress):
        if self.pipe is not None:
            return self.pipe
        sd, _ = import_core(core=CORE)
        import comfy.utils
        files = resolve_components(self.models, self.components, FLUX)
        progress("Loading FLUX 3 components…")
        model = sd.load_diffusion_model(files["diffusion_model"]["path"], disable_dynamic=True)
        if model is None:
            raise RuntimeError("The selected FLUX 3 diffusion model is unsupported.")
        clip = sd.load_clip([files["text_encoder"]["path"]], clip_type=sd.CLIPType.FLUX3, disable_dynamic=True)
        vae = sd.VAE(sd=comfy.utils.load_torch_file(files["vae"]["path"], safe_load=True))
        self.pipe = model, clip, vae
        return self.pipe

    def generate_studio(self, references, seed, prompt, width, height, cancel, progress, *, _minimum=256, _constant_lora=False):
        if len(references) > 10 or width % 16 or height % 16 or not _minimum <= width <= 2048 or not _minimum <= height <= 2048:
            raise ValueError("Invalid FLUX 3 generation dimensions or reference count.")
        if cancel.is_set():
            raise Cancelled()
        problems = self.check()
        if problems:
            raise RuntimeError("\n".join(problems))
        # Optional private dependency overlay keeps the existing Qwen environment.
        deps = str(CORE / "deps")
        if (CORE / "deps").is_dir() and deps not in sys.path:
            sys.path.insert(0, deps)
        import_core(core=CORE)
        import numpy as np
        import torch
        import comfy.model_sampling
        import comfy.samplers
        import comfy.sample
        from PIL import Image

        def check():
            if cancel.is_set():
                raise Cancelled()

        def step(index, denoised, noisy, total):
            check()
            progress(f"FLUX 3 · {index + 1}/{total}")

        with torch.inference_mode():
            key = caption_key(references, prompt, width, height)
            images = []
            for image in references:
                rgba = torch.from_numpy(np.array(image.convert("RGBA")).astype(np.float32) / 255.0).unsqueeze(0)
                images.append(rgba[..., :3] * rgba[..., 3:] + 1.0 - rgba[..., 3:])
            if self.caption is not None and self.caption[0] == key:
                caption = self.caption[1]
            else:
                caption = self.expand(images, seed, prompt, width, height, cancel, progress)
                self.caption = key, caption
            check()
            model, clip, vae = self.load(progress)
            if _constant_lora:
                from .lora import apply_constant
                model, clip = apply_constant(model, clip, self.models, self.components, progress)
            latents = []
            for index, pixels in enumerate(images):
                check()
                progress(f"Encoding reference {index + 1}…")
                latents.append(vae.encode(pixels))
            conditioning = clip.encode_from_tokens_scheduled(clip.tokenize(caption))
            positive = [[embedding, {**metadata, **({"reference_latents": latents} if latents else {})}]
                        for embedding, metadata in conditioning]

            # Match BFL's distilled Comfy workflow: 16 steps, Euler, simple,
            # CFG 1, constant alpha=16 (mu=ln(alpha)), Flux2's 128-channel grid.
            class Sampling(comfy.model_sampling.ModelSamplingFlux, comfy.model_sampling.CONST):
                pass
            sampling = Sampling(model.model.model_config)
            sampling.set_parameters(shift=math.log(16.0))
            patched = model.clone()
            patched.add_object_patch("model_sampling", sampling)
            sigmas = comfy.samplers.calculate_sigmas(sampling, "simple", 16)
            latent = torch.zeros((1, 128, height // 16, width // 16), dtype=torch.float32, device="cpu")
            noise = comfy.sample.prepare_noise(latent, seed)
            check()
            samples = comfy.sample.sample_custom(patched, noise, 1.0, comfy.samplers.sampler_object("euler"),
                sigmas, positive, positive, latent, callback=step, disable_pbar=True, seed=seed)
            check()
            decoded = vae.decode(samples).detach().float().cpu().numpy()[0]
            return Image.fromarray((np.clip(decoded, 0, 1) * 255).round().astype(np.uint8)).convert("RGBA")

    def generate(self, image, mask, seed, prompt, cancel, progress):
        reference, coverage = inpaint_references(image, mask)
        # FLUX3 is an editing model. Pixel-exact masking is applied by the host
        # after rendering, shared with Remove Tool and selection Ai Fill.
        return self.generate_studio([reference, coverage], seed, masked_prompt(prompt),
                                    image.width, image.height, cancel, progress, _minimum=16, _constant_lora=True)
