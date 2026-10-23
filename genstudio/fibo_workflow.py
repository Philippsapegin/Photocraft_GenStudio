"""FIBO 2 core ComfyUI graph, matching the official four-step edit recipe."""
NODES = ('UNETLoader', 'CLIPLoader', 'VAELoader', 'TextEncodeFibo2', 'LoadImage', 'KSampler', 'VAEDecode', 'SaveImage')


def workflow(config, references, seed, prompt, width, height, *, loras=None, constant_lora=False):
    if not 1 <= len(references) <= 4:
        raise ValueError('FIBO 2 Edit Turbo requires 1 to 4 reference images.')
    def node(kind, **inputs): return {'class_type': kind, 'inputs': inputs}
    graph = {
        '1': node('UNETLoader', unet_name=config['diffusion_model'], weight_dtype='default'),
        '2': node('CLIPLoader', clip_name=config['text_encoder'], type='fibo2', device='default'),
        '3': node('VAELoader', vae_name=config['vae']),
        '4': node('TextEncodeFibo2', clip=['2', 0], vae=['3', 0], prompt=prompt,
                  negative_prompt='', width=width, height=height, batch_size=1),
        '12': node('KSampler', model=['1', 0], positive=['4', 0], negative=['4', 1], latent_image=['4', 2],
                   seed=seed, steps=4, cfg=1.0, sampler_name='euler', scheduler='simple', denoise=1.0),
        '13': node('VAEDecode', samples=['12', 0], vae=['3', 0]),
        '14': node('SaveImage', images=['13', 0], filename_prefix='PhotoCraft_GenGate'),
    }
    for i, filename in enumerate(references):
        key = str(20 + i)
        graph[key] = node('LoadImage', image=filename)
        graph['4']['inputs'][f'images.image_{i+1}'] = [key, 0]
    from .lora import graph as constant_graph, studio_graph
    model, clip = ['1', 0], ['2', 0]
    if constant_lora: model, clip = constant_graph(config, graph, model, clip)
    model, clip = studio_graph(graph, model, clip, loras)
    graph['12']['inputs']['model'] = model
    graph['4']['inputs']['clip'] = clip
    return graph
