"""FIBO 2.0 Edit Turbo: native Comfy model library, no server or remote VGL service."""
import importlib.util
import importlib.metadata
from pathlib import Path
import sys

from genfill.comfy import Cancelled
from .core_pipeline import import_core, inpaint_references
from .models import FIBO, resolve_components
from .settings import ROOT, validate_components

CORE = ROOT / '.runtime/fibo-core'


class FiboPipeline:
    model_id = FIBO

    def __init__(self, models, text_encoder='', vae='', *, components=None):
        self.models = Path(models)
        self.components = validate_components(components if components is not None else {'textEncoder': text_encoder, 'vae': vae})
        self.text_encoder, self.vae = self.components['textEncoder'], self.components['vae']
        self.pipe = None

    def check(self):
        problems = [f'Missing dependency: {name}' for name in
                    ('torch', 'transformers', 'safetensors', 'comfy_kitchen', 'comfy_aimdo', 'einops', 'scipy')
                    if importlib.util.find_spec(name) is None]
        if sys.version_info < (3, 12):
            problems.append('FIBO 2 Direct requires Python 3.12 or newer.')
        if not (CORE / 'comfy/text_encoders/fibo2.py').is_file():
            problems.append('FIBO 2 Direct library is missing. Run scripts/Setup-Direct.ps1.')
        versions = {d.metadata['Name'].lower(): d.version for d in importlib.metadata.distributions(path=[str(CORE / 'deps')])}
        for name, minimum in (('comfy-kitchen', (0, 2, 40)), ('comfy-aimdo', (0, 6, 0))):
            try:
                version = versions.get(name) or importlib.metadata.version(name)
                if tuple(int(n) for n in version.split('.')[:3]) < minimum:
                    problems.append(f'FIBO 2 needs newer {name}. Run scripts/Setup-Direct.ps1.')
            except (importlib.metadata.PackageNotFoundError, ValueError):
                problems.append(f'FIBO 2 dependency {name} is missing. Run scripts/Setup-Direct.ps1.')
        try:
            resolve_components(self.models, self.components, FIBO)
        except (ValueError, OSError) as error:
            problems.append(str(error))
        return problems

    def load(self, progress):
        if self.pipe is not None:
            return self.pipe
        problems = self.check()
        if problems:
            raise RuntimeError('\n'.join(problems))
        files = resolve_components(self.models, self.components, FIBO)
        sd, _ = import_core(core=CORE)
        import comfy.utils
        progress('Loading FIBO 2.0 Edit Turbo components...')
        model = sd.load_diffusion_model(files['diffusion_model']['path'], disable_dynamic=True)
        if model is None:
            raise RuntimeError('The selected FIBO diffusion model is unsupported.')
        clip = sd.load_clip([files['text_encoder']['path']], clip_type=sd.CLIPType.FIBO2, disable_dynamic=True)
        vae = sd.VAE(sd=comfy.utils.load_torch_file(files['vae']['path'], safe_load=True))
        self.pipe = model, clip, vae
        return self.pipe

    def generate_studio(self, references, seed, prompt, width, height, cancel, progress, *, loras=None, _mask=None, _constant=False, negative_prompt=""):
        if cancel.is_set():
            raise Cancelled()
        maximum = 8192 if _mask is not None else 2048
        if not 1 <= len(references) <= 4 or width % 16 or height % 16 or not 16 <= width <= maximum or not 16 <= height <= maximum:
            raise ValueError('FIBO 2 Edit Turbo requires 1 to 4 images and dimensions on its 16-pixel grid.')
        from .lora import apply_studio, apply_constant
        model, clip, vae = self.load(progress)
        model, clip = apply_studio(model, clip, self.models, loras, progress)
        if _constant:
            model, clip = apply_constant(model, clip, self.models, self.components, progress)
        import numpy as np
        import torch
        import comfy.sample
        import comfy.samplers
        from PIL import Image

        def check():
            if cancel.is_set(): raise Cancelled()

        def step(index, denoised, noisy, total):
            check(); progress(f'FIBO 2.0 Edit Turbo · {index + 1}/{total}')

        with torch.inference_mode():
            images, latents = [], []
            for reference in references:
                check()
                if len(references) == 1 and reference.size != (width, height):
                    reference = reference.resize((width, height), Image.Resampling.LANCZOS)
                rgba = np.asarray(reference.convert('RGBA')).astype(np.float32) / 255.0
                rgb = torch.from_numpy(rgba[..., :3] * rgba[..., 3:] + 1 - rgba[..., 3:]).unsqueeze(0)
                images.append(rgb); latents.append(vae.encode(rgb))
            check(); progress('FIBO 2.0 · encoding edit instruction...')
            conditioning = clip.encode_from_tokens_scheduled(clip.tokenize(prompt, images=images))
            positive = [[embedding, {**metadata, 'reference_latents': latents}] for embedding, metadata in conditioning]
            from .conditioning import negative as encode_negative
            negative = encode_negative(clip, negative_prompt, positive, images=images)
            latent = torch.zeros((1, 128, height // 16, width // 16), dtype=torch.float32, device='cpu')
            noise_mask = None
            if _mask is not None:
                mask = torch.from_numpy(np.asarray(_mask.convert('L')).astype(np.float32) / 255).unsqueeze(0).unsqueeze(0)
                noise_mask = torch.nn.functional.adaptive_max_pool2d(mask, latent.shape[-2:])
                latent = latents[0].to(device='cpu')
            noise = comfy.sample.prepare_noise(latent, seed)
            sigmas = comfy.samplers.calculate_sigmas(model.get_model_object('model_sampling'), 'simple', (self.components.get('constantLoraSteps') or 4) if _constant else 4)
            check()
            samples = comfy.sample.sample_custom(model, noise, 1.0, comfy.samplers.sampler_object('euler'),
                sigmas, positive, negative, latent, noise_mask=noise_mask, callback=step, disable_pbar=True, seed=seed)
            check(); decoded = vae.decode(samples).detach().float().cpu().numpy()[0]
            return Image.fromarray((np.clip(decoded, 0, 1) * 255).round().astype(np.uint8)).convert('RGBA')

    def generate(self, image, mask, seed, prompt, cancel, progress):
        reference, coverage = inpaint_references(image, mask)
        instruction = ('Edit <image_1> using the black-and-white mask in <image_2>. '
                       'Replace only the white masked region. The gray area is missing content. ' + prompt +
                       ' Return the edited first image only, preserving framing and all other content.')
        return self.generate_studio([reference, coverage], seed, instruction, image.width, image.height,
                                    cancel, progress, _mask=mask, _constant=True)
