"""Studio-only user workflows and the widget-free Comfy bridge contract."""
from __future__ import annotations
import base64
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
from urllib.parse import urlencode
import uuid

from PIL import Image
from genfill.comfy import Cancelled
from genfill.image_ops import png_bytes
from .lora import validate_studio

PROTOCOL = "gengate-comfy-v2"
IN_NODE = "GenGateIN2"
PORT_NAMES = ["image", "mask", "prompt", "width", "height", "seed", "batch", "loras"] + [
    name for i in range(2, 11) for name in (f"reference_{i}", f"reference_{i}_mask")]
LEGACY_PORT_NAMES = ["image", "mask", "document", "layer", "selection", "prompt", "width", "height",
                     "seed", "batch", "source_prompt", "reference_count", "references", "reference_masks",
                     "preset", "model", "text_encoder", "vae", "prompt_upsampler", "loras", "metadata"] + PORT_NAMES[8:]
NODE_SOURCE = Path(__file__).resolve().parents[1] / "comfyui/GenGate"


def enabled(settings):
    return settings.get("engine") == "comfy"


def read_workflow(path):
    if not isinstance(path, str) or not path or "\0" in path:
        raise ValueError("Choose a ComfyUI API workflow JSON file in GenGate.")
    file = Path(path)
    if not file.is_file() or file.stat().st_size > 8 << 20:
        raise ValueError("The workflow file is missing or exceeds 8 MB. Choose it again in GenGate.")
    try:
        return parse_workflow(json.loads(file.read_text(encoding="utf-8-sig")))
    except RecursionError as error:
        raise ValueError("The workflow JSON is nested too deeply.") from error


def parse_workflow(value):
    if isinstance(value, dict) and "prompt" in value and isinstance(value["prompt"], dict) and "class_type" not in value["prompt"]:
        value = value["prompt"]
    if isinstance(value, dict) and isinstance(value.get("nodes"), list):
        raise ValueError("This is a canvas workflow. In ComfyUI choose Export (API), then open that JSON in GenGate. No node settings need changing.")
    if not isinstance(value, dict) or not 2 <= len(value) <= 2048:
        raise ValueError("Choose an API workflow containing GenGate IN and GenGate OUT nodes.")
    try:
        graph = copy.deepcopy(value)
    except RecursionError as error:
        raise ValueError("The workflow JSON is nested too deeply.") from error
    for key, node in graph.items():
        if (not isinstance(key, str) or not key or len(key) > 128 or not isinstance(node, dict)
                or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict)):
            raise ValueError("Invalid ComfyUI API workflow node.")
    legacy = {key for key, node in graph.items() if node["class_type"] == "GenGateIN"}
    # API links use numeric socket positions. Migrate retained v1 sockets by name;
    # a different class id prevents old canvas links silently changing meaning.
    for node in graph.values():
        for item in node["inputs"].values():
            if isinstance(item, list) and len(item) == 2 and str(item[0]) in legacy and isinstance(item[1], int):
                name = LEGACY_PORT_NAMES[item[1]] if 0 <= item[1] < len(LEGACY_PORT_NAMES) else "unknown"
                if name == "source_prompt": name = "prompt"
                if name not in PORT_NAMES:
                    raise ValueError(f"The old GenGate IN socket '{name}' was removed. Recreate IN in ComfyUI, reconnect Studio values and Export (API) again.")
                item[1] = PORT_NAMES.index(name)
    for key in legacy:
        graph[key]["class_type"] = IN_NODE
    inputs = [key for key, node in graph.items() if node["class_type"] == IN_NODE]
    outputs = [key for key, node in graph.items() if node["class_type"] == "GenGateOUT"]
    if not inputs or not outputs:
        raise ValueError("Add GenGate IN and GenGate OUT to the workflow, then export its API JSON.")
    if len(outputs) > 8:
        raise ValueError("Use at most eight GenGate OUT nodes.")
    for key in inputs:
        if graph[key]["inputs"]:
            raise ValueError("GenGate IN has no editable inputs. Recreate the node and export again.")
    for key in outputs:
        image = graph[key]["inputs"].get("images")
        if not isinstance(image, list) or len(image) != 2 or str(image[0]) not in graph or not isinstance(image[1], int) or image[1] < 0:
            raise ValueError("Connect an IMAGE output to every GenGate OUT images input.")
    # Bound nesting and reject non-finite values before data enters HTTP or layer
    # metadata. Graph validation itself remains ComfyUI's responsibility.
    def bounded(item, depth=0):
        if depth > 64:
            raise ValueError("The workflow JSON is nested too deeply.")
        if isinstance(item, dict):
            for v in item.values(): bounded(v, depth + 1)
        elif isinstance(item, list):
            for v in item: bounded(v, depth + 1)
    bounded(graph)
    encoded = json.dumps(graph, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 8 << 20:
        raise ValueError("The workflow exceeds 8 MB.")
    return {"graph": graph, "inputs": inputs, "outputs": outputs, "sha256": hashlib.sha256(encoded).hexdigest()}


def install_nodes(comfy_dir):
    root = Path(comfy_dir).resolve()
    if not (root / "main.py").is_file():
        raise ValueError("Set the ComfyUI folder in Preferences before installing GenGate nodes.")
    target = root / "custom_nodes/GenGate"
    if target.is_symlink() or target.exists() and not target.is_dir():
        raise ValueError("custom_nodes/GenGate is not an ordinary folder; install the bridge there manually.")
    source = NODE_SOURCE
    files = ("__init__.py", "nodes.py", "README.md", "LICENSE", "examples/Connection-test.json", "examples/Connection-test-api.json")
    if not all((source / name).is_file() for name in files):
        raise ValueError("The GenGate node package is incomplete. Update the add-on.")
    current = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in files}
    marker = target / "gengate-nodes.json"
    previous = json.loads(marker.read_text()) if marker.is_file() else {}
    if target.exists() and any(target.iterdir()) and not previous:
        # A manual copy of these exact files is also safe to adopt.
        if any(not (target / name).is_file() or hashlib.sha256((target / name).read_bytes()).hexdigest() != digest for name, digest in current.items()):
            raise ValueError("custom_nodes/GenGate contains another installation. Keep or rename it before installing these nodes.")
    for name, digest in previous.get("files", {}).items():
        if name not in files:
            continue
        path = target / name
        if path.is_symlink() or path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() not in (digest, current[name]):
            raise ValueError("A GenGate node file was edited locally. Keep that copy before updating: " + name)
    changed = any(not (target / name).is_file() or hashlib.sha256((target / name).read_bytes()).hexdigest() != digest for name, digest in current.items())
    target.mkdir(parents=True, exist_ok=True)
    for name in files:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != current[name]:
            temporary = target / (name + ".gengate-tmp")
            shutil.copyfile(source / name, temporary)
            temporary.replace(destination)
    marker.write_text(json.dumps({"protocol": PROTOCOL, "files": current}, indent=2), encoding="utf-8")
    return {"installed": True, "changed": changed, "folder": str(target),
            "message": "GenGate IN / OUT installed. Restart an already running ComfyUI to load updated nodes." if changed else "GenGate IN / OUT are up to date."}


def inspect(client, definition):
    info = client.request("/object_info", timeout=30)
    missing = sorted({node["class_type"] for node in definition["graph"].values()} - info.keys())
    if missing:
        raise ValueError("ComfyUI is missing workflow nodes: " + ", ".join(missing)
                         + ". Install them and restart ComfyUI. GenGate IN / OUT are installed from GenGate Settings.")
    return info


def checked_image(data, grayscale=False):
    if len(data) > 64 << 20:
        raise ValueError("The workflow returned an oversized image file.")
    with Image.open(BytesIO(data)) as image:
        if min(image.size) < 1 or max(image.size) > 8192 or image.width * image.height > 32 << 20:
            raise ValueError("Workflow images support up to 8192 px per side and 32 million pixels each.")
        if image.format != "PNG":
            raise ValueError("GenGate OUT must return PNG images.")
        image.load()
        return image.convert("L" if grayscale else "RGBA")


def generate(client, definition, data, config, cancel, progress):
    inspect(client, definition)
    uid = uuid.uuid4().hex
    envelope = {"protocol": PROTOCOL, "request_id": uid, "prompt": data["prompt"],
                "source_prompt": data.get("sourcePrompt", data["prompt"]), "width": data["width"], "height": data["height"],
                "seed": data["seed"], "batch": data["batch"],
                "loras": data["loras"], "references": []}
    for index, image in enumerate(data["references"]):
        if cancel.is_set(): raise Cancelled()
        progress(f"Sending reference {index + 1} to ComfyUI…")
        envelope["references"].append(client.upload(f"GenGate-{uid}-ref-{index}.png", png_bytes(image), image_type="temp"))
    history = client.run_workflow(definition["graph"], cancel, progress, uid,
                                  extra_data={"extra_pnginfo": {"gengate": envelope}}, return_history=True)
    rows, pixels = [], 0
    for node in definition["outputs"]:
        output = history.get("outputs", {}).get(node, {})
        packets = output.get("gengate", [])
        if not isinstance(packets, list) or not packets:
            raise RuntimeError("GenGate OUT did not return a result. Check that its images input is connected.")
        for packet in packets:
            if not isinstance(packet, dict) or packet.get("protocol") != PROTOCOL or packet.get("request_id") != uid:
                raise RuntimeError("ComfyUI returned a cached result from another GenGate request.")
            records = packet.get("images")
            if not isinstance(records, list) or not records:
                raise RuntimeError("GenGate OUT returned no images.")
            for record in records:
                if not isinstance(record, dict) or "image" not in record:
                    raise ValueError("GenGate OUT returned invalid image metadata.")
                if len(rows) >= 8:
                    raise ValueError("A workflow may return at most eight images per request.")
                def fetch(descriptor, mask=False):
                    if (not isinstance(descriptor, dict) or descriptor.get("type") != "temp"
                            or descriptor.get("subfolder") != "gengate" or not isinstance(descriptor.get("filename"), str)
                            or not descriptor["filename"].startswith("GenGate-" + uid + "-")
                            or Path(descriptor["filename"]).name != descriptor["filename"]
                            or any(c in descriptor["filename"] for c in ("/", "\\", ":"))):
                        raise ValueError("The workflow returned an invalid or unrelated image location.")
                    return checked_image(client.request("/view?" + urlencode(descriptor), raw=True, timeout=90), mask)
                image = fetch(record["image"])
                pixels += image.width * image.height
                if pixels > 64 << 20:
                    raise ValueError("The workflow batch exceeds 64 million pixels.")
                prompt = record.get("prompt", data["prompt"])
                name = record.get("name", "GenGate")
                seed = record.get("seed", data["seed"])
                if not isinstance(prompt, str) or len(prompt.encode()) > 12000 or "\0" in prompt:
                    raise ValueError("Invalid returned workflow prompt.")
                if not isinstance(name, str) or len(name) > 120 or "\0" in name:
                    raise ValueError("Invalid returned workflow layer name.")
                if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**53:
                    raise ValueError("Invalid returned workflow seed.")
                row = {"image": base64.b64encode(png_bytes(image)).decode("ascii"), "width": image.width,
                       "height": image.height, "prompt": prompt, "name": name, "seed": seed,
                       "loras": validate_studio(record.get("loras", data["loras"]))}
                if record.get("mask"):
                    mask = fetch(record["mask"], True)
                    if mask.size != image.size: raise ValueError("The workflow mask does not match its image.")
                    row["mask"] = base64.b64encode(png_bytes(mask)).decode("ascii")
                rows.append(row)
    if not rows: raise RuntimeError("The workflow finished without a GenGate OUT image.")
    return {"images": rows, "prompt": data["prompt"],
            "workflow": {"path": data.get("workflowPath", ""), "sha256": definition["sha256"]}}
