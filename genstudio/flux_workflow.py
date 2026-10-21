"""Built-in ComfyUI v0.41.3+ nodes matching BFL's distilled Klein workflow."""
import math
from .flux_pipeline import expansion_prompt

NODES = ("UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "CLIPTextEncode", "TextGenerate",
         "ModelSamplingFlux", "EmptyFlux2LatentImage", "KSampler", "VAEEncode", "ReferenceLatent",
         "BatchImagesNode", "VAEDecode", "SaveImage")


def workflow(config, references, seed, prompt, width, height, object_info=None, *, constant_lora=False):
    if len(references) > 10:
        raise ValueError("FLUX 3 Klein supports up to 10 reference images.")
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}
    system, instruction = expansion_prompt(prompt, references, width, height)
    sampling = {"sampling_mode": "on", "temperature": 1.0, "top_k": 1, "top_p": 0.95,
                "min_p": 0.0, "repetition_penalty": 1.0, "presence_penalty": 0.0 if references else 1.5, "seed": seed}
    # V3 dynamic combos are flattened in the HTTP prompt protocol. Comfy's
    # schema reconstructs the execute() dictionary from these dotted keys.
    text_inputs = {"clip": ["5", 0], "prompt": instruction, "max_length": 3584,
                   "sampling_mode": "on", "thinking": False, "use_default_template": True,
                   "mtp": "off", "system_prompt": system}
    text_inputs.update({"sampling_mode." + key: value for key, value in sampling.items() if key != "sampling_mode"})
    graph = {
        "1": node("UNETLoader", unet_name=config["diffusion_model"], weight_dtype="default"),
        "2": node("CLIPLoader", clip_name=config["text_encoder"], type="flux3", device="default"),
        "3": node("VAELoader", vae_name=config["vae"]),
        "4": node("ModelSamplingFlux", model=["1", 0], max_shift=math.log(16), base_shift=math.log(16), width=width, height=height),
        "5": node("CLIPLoader", clip_name=config["prompt_upsampler"], type="flux3", device="default"),
        "6": node("TextGenerate", **text_inputs),
        "7": node("CLIPTextEncode", clip=["2", 0], text=["6", 0]),
        "9": node("EmptyFlux2LatentImage", width=width, height=height, batch_size=1),
        "12": node("KSampler", model=["4", 0], positive=["7", 0], negative=["7", 0],
                   latent_image=["9", 0], seed=seed, steps=16, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "13": node("VAEDecode", samples=["12", 0], vae=["3", 0]),
        "14": node("SaveImage", images=["13", 0], filename_prefix="PhotoCraft_GenStudio"),
    }
    previous = ["7", 0]
    batch = {}
    for index, filename in enumerate(references):
        load, encode, conditioning = map(str, (20 + index * 3, 21 + index * 3, 22 + index * 3))
        graph[load] = node("LoadImage", image=filename)
        graph[encode] = node("VAEEncode", pixels=[load, 0], vae=["3", 0])
        graph[conditioning] = node("ReferenceLatent", conditioning=previous, latent=[encode, 0])
        previous = [conditioning, 0]
        batch[f"images.image{index}"] = [load, 0]
    if references:
        graph["8"] = node("BatchImagesNode", **batch)
        graph["6"]["inputs"]["image"] = ["8", 0]
        graph["12"]["inputs"].update(positive=previous, negative=previous)
    if constant_lora:
        from .lora import graph as lora_graph
        model, clip = lora_graph(config, graph, ["1", 0], ["2", 0])
        graph["4"]["inputs"]["model"] = model
        graph["7"]["inputs"]["clip"] = clip
    return graph
