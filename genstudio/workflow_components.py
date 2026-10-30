"""Explicit component bindings on a job's graph copy; no ComfyUI patches/nodes."""
from pathlib import Path

# Only literal filename inputs on these loaders are supported. Other loaders and
# multi-encoder combinations remain entirely owned by the workflow.
LOADERS = {
    'modelFile': {'UNETLoader':'unet_name', 'UnetLoaderGGUF':'unet_name', 'UnetLoaderGGUFAdvanced':'unet_name', 'CheckpointLoaderSimple':'ckpt_name'},
    'textEncoder': {'CLIPLoader':'clip_name', 'CLIPLoaderGGUF':'clip_name'},
    'promptUpsampler': {'CLIPLoader':'clip_name', 'CLIPLoaderGGUF':'clip_name'},
    'vae': {'VAELoader':'vae_name'},
}
COMPONENTS = (
    ('modelFile', 'Model', ('diffusion_models','unet','checkpoints')),
    ('textEncoder', 'Text encoder', ('text_encoders','clip')),
    ('promptUpsampler', 'Prompt upsampler', ('text_encoders','clip','prompt_upsamplers')),
    ('vae', 'VAE', ('vae',)),
)


def apply(definition, settings, info):
    from .settings import active_components
    from .models import selected_file
    from .comfy_workflow import parse_workflow
    import copy
    components = active_components(settings)
    selected = [(key,label,folders,components[key]) for key,label,folders in COMPONENTS if components[key]]
    if not selected:
        return definition
    graph = copy.deepcopy(definition['graph'])
    source_root = Path(settings['modelsDir']).expanduser().absolute()
    root = source_root.resolve()
    for key,label,folders,value in selected:
        title = 'GenGate: '+label
        nodes = [node for node in graph.values()
                 if isinstance(node.get('_meta'),dict) and isinstance(node['_meta'].get('title'),str)
                 and node['_meta']['title'].strip().casefold() == title.casefold()]
        if len(nodes) != 1:
            raise ValueError(f'{label} override needs exactly one loader titled "{title}". Rename the intended loader, or choose Automatic in GenGate Advanced.')
        node = nodes[0]
        field = LOADERS[key].get(node['class_type'])
        if not field:
            raise ValueError(f'{title}: {node["class_type"]} is not a supported single-component loader. Keep Automatic to use its workflow settings.')
        if not isinstance(node['inputs'].get(field),str):
            raise ValueError(f'{title}: {field} must be a filename widget, not a linked input. Keep Automatic to preserve the connection.')
        path = Path(value).expanduser()
        # Browse can return an absolute path through a Models-directory junction.
        # Preserve its relative name before canonicalizing the root.
        if path.is_absolute() and path.is_relative_to(source_root):
            value = path.relative_to(source_root).as_posix()
            path = Path(value)
        if not path.is_absolute():path=root/path
        if not any(path.is_relative_to(root/folder) for folder in folders):
            raise ValueError(f'{label}: ComfyUI overrides require a file inside the matching subfolder of the shared Models folder. Configure that folder in ComfyUI too.')
        filename = selected_file(root,value,folders)['name']
        inputs = info.get(node['class_type'],{}).get('input',{})
        schema = next((inputs.get(group,{}).get(field) for group in ('required','optional') if field in inputs.get(group,{})),None)
        choices = schema[0] if isinstance(schema,(list,tuple)) and schema else None
        normalized = filename.replace('\\','/').casefold()
        matches = [choice for choice in choices if isinstance(choice,str) and choice.replace('\\','/').casefold()==normalized] if isinstance(choices,(list,tuple)) else []
        if len(matches) != 1:
            raise ValueError(f'{title}: ComfyUI does not list the selected file "{filename}" unambiguously. Add the shared Models folder to ComfyUI model paths and restart it, or choose Automatic.')
        node['inputs'][field] = matches[0]
    return parse_workflow(graph)
