from __future__ import annotations

# Official pipeline configuration, pinned on 2026-10-10. Final zero ends Euler sampling.
# https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo/blob/main/model_index.json
TURBO_SIGMAS = (1.0, 0.978453, 0.95418, 0.926626, 0.89508,
                0.845148, 0.704534, 0.414568, 0.0)

REQUIRED_NODES = (
    "UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "LoadImageMask",
    "JoinImageWithAlpha", "TextEncodeQwenImage21", "QwenImage21Cache",
    "LanPaint_ImageEncode", "LanPaint_SamplerCustom", "ManualSigmas",
    "KSamplerSelect", "VAEDecode", "SaveImage",
)
STUDIO_NODES = ("UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "JoinImageWithAlpha",
                "TextEncodeQwenImage21", "QwenImage21Cache", "ManualSigmas", "KSamplerSelect",
                "SamplerCustom", "LatentUpscale", "VAEDecode", "SaveImage")


def studio_workflow(config, references, seed, prompt, width, height, object_info):
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}
    schema = object_info["TextEncodeQwenImage21"]["input"]
    flat = "image_1" in {**schema.get("required", {}), **schema.get("optional", {})}
    inputs = {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt, "negative_prompt": "", "resolution": 0}
    workflow = {
        "1": node("UNETLoader", unet_name=config["diffusion_model"], weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=config["text_encoder"], type="qwen_image", device=config.get("clip_device", "default")),
        "3": node("VAELoader", vae_name=config["vae"]),
        "4": node("QwenImage21Cache", model=["1", 0], device=config.get("cache_device", "cpu"), dtype=config.get("cache_dtype", "int8")),
        "8": {"class_type": "TextEncodeQwenImage21", "inputs": inputs},
        # This generic node uses /8 pixel units; Qwen 2.1 has a /16 latent grid.
        # Resize only the empty target latent, retaining reference aspect ratios.
        "9": node("LatentUpscale", samples=["8", 2], upscale_method="nearest-exact", width=width // 2, height=height // 2, crop="disabled"),
        "10": node("ManualSigmas", sigmas=", ".join(str(x) for x in TURBO_SIGMAS)),
        "11": node("KSamplerSelect", sampler_name="euler"),
        "12": node("SamplerCustom", model=["4", 0], add_noise=True, noise_seed=seed, cfg=1.0,
                   positive=["8", 0], negative=["8", 1], sampler=["11", 0], sigmas=["10", 0], latent_image=["9", 0]),
        "13": node("VAEDecode", samples=["12", 0], vae=["3", 0]),
        "14": node("SaveImage", images=["13", 0], filename_prefix="PhotoCraft_GenStudio"),
    }
    for index, filename in enumerate(references):
        key = str(20 + index * 2)
        alpha = str(21 + index * 2)
        workflow[key] = node("LoadImage", image=filename)
        workflow[alpha] = node("JoinImageWithAlpha", image=[key, 0], alpha=[key, 1])
        inputs[("" if flat else "images.") + f"image_{index + 1}"] = [alpha, 0]
    return workflow


def edit_prompt(mode: str, prompt: str) -> str:
    if mode not in ("fill", "remove"):
        raise ValueError("Unknown generation mode.")
    if not isinstance(prompt, str) or len(prompt) > 6000:
        raise ValueError("The prompt must contain no more than 6000 characters.")
    if mode == "remove" or not prompt.strip():
        instruction = ("Remove the object in the masked region completely. Reconstruct the "
                       "background that would naturally be behind it, matching the surroundings, "
                       "perspective, illumination, texture and depth. Do not introduce new objects.")
        if prompt.strip():
            instruction += " Additional instructions: " + prompt.strip()
    else:
        instruction = "Edit the masked region of the input image: " + prompt.strip()
    return instruction + " Preserve the composition and all unmasked content of the input image."


def build_workflow(config: dict, image_name: str, mask_name: str, seed: int,
                   prompt: str, object_info: dict | None = None) -> dict:
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}

    # ComfyUI V3 autogrow uses dotted names; older versions expose image_1 directly.
    image_input = "images.image_1"
    if object_info:
        schema = object_info["TextEncodeQwenImage21"]["input"]
        if "image_1" in {**schema.get("required", {}), **schema.get("optional", {})}:
            image_input = "image_1"
    encode_inputs = {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt,
                     "negative_prompt": "", "resolution": 0, image_input: ["6", 0]}
    graph = {
        "1": node("UNETLoader", unet_name=config["diffusion_model"], weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=config["text_encoder"], type="qwen_image",
                  device=config.get("clip_device", "default")),
        "3": node("VAELoader", vae_name=config["vae"]),
        "4": node("QwenImage21Cache", model=["1", 0], device=config.get("cache_device", "cpu"),
                  dtype=config.get("cache_dtype", "int8")),
        "5": node("LoadImage", image=image_name),
        "6": node("JoinImageWithAlpha", image=["5", 0], alpha=["5", 1]),
        "7": node("LoadImageMask", image=mask_name, channel="red"),
        "8": {"class_type": "TextEncodeQwenImage21", "inputs": encode_inputs},
        "9": node("LanPaint_ImageEncode", image=["6", 0], vae=["3", 0], mask=["7", 0]),
        "10": node("ManualSigmas", sigmas=", ".join(str(x) for x in TURBO_SIGMAS)),
        "11": node("KSamplerSelect", sampler_name="euler"),
        "12": node("LanPaint_SamplerCustom", model=["4", 0], add_noise=True,
                   noise_seed=seed, cfg=1.0, positive=["8", 0], negative=["8", 1],
                   sampler=["11", 0], sigmas=["10", 0], latent_image=["9", 0],
                   LanPaint_NumSteps=config.get("lanpaint_steps", 2),
                   LanPaint_PromptMode="Image First", LanPaint_Info="PhotoCraft Gen Studio"),
        "13": node("VAEDecode", samples=["12", 0], vae=["3", 0]),
        "14": node("SaveImage", images=["13", 0], filename_prefix="PhotoCraft_GenStudio"),
    }
    from genstudio.lora import graph as lora_graph
    model, clip = lora_graph(config, graph, ["1", 0], ["2", 0])
    graph["4"]["inputs"]["model"] = model
    graph["8"]["inputs"]["clip"] = clip
    return graph
