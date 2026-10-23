"""Exercise real NN Switch hotkeys in an isolated PhotoCraft; never run inference.

Windows only. NN Switch must already be running with English/Russian installed.
Uses its saved Last written word and Selected text bindings without changing them.
"""
from __future__ import annotations

import argparse
import base64
import ctypes as c
from ctypes import wintypes as w
import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from genstudio.settings import defaults
from photocraft_engine_smoke import MCP


class Keyboard(c.Structure):
    _fields_ = [("vk", w.WORD), ("scan", w.WORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("extra", c.c_size_t)]


class Mouse(c.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG), ("data", w.DWORD),
                ("flags", w.DWORD), ("time", w.DWORD), ("extra", c.c_size_t)]


class InputUnion(c.Union):
    _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]


class Input(c.Structure):
    _fields_ = [("type", w.DWORD), ("data", InputUnion)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=4)
    parser.add_argument("--nn-settings", type=Path,
                        default=Path(os.environ["APPDATA"]) / "NN Switch/settings.json")
    args = parser.parse_args()
    assert os.name == "nt" and 1 <= args.repeat <= 30
    nn = json.loads(args.nn_settings.read_text(encoding="utf-8-sig"))
    keys = nn["Hotkeys"]
    for name in ("LastWord", "SelectedText"):
        assert keys[name]["Key"], f"NN Switch {name} hotkey must be configured"
    user32 = c.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = w.HWND
    user32.SetForegroundWindow.argtypes = [w.HWND]
    user32.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user32.GetKeyboardLayout.argtypes = [w.DWORD]
    user32.GetKeyboardLayout.restype = w.HKL
    user32.SendInput.argtypes = [w.UINT, c.POINTER(Input), c.c_int]
    user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user32.IsWindowVisible.argtypes = [w.HWND]
    user32.GetWindowTextLengthW.argtypes = [w.HWND]
    user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
    original_foreground = user32.GetForegroundWindow()

    def chord(*virtual_keys):
        # Unmarked physical input intentionally passes through the actual NN hook.
        events = [Input(1, InputUnion(keyboard=Keyboard(v, 0, 0, 0, 0))) for v in virtual_keys]
        events += [Input(1, InputUnion(keyboard=Keyboard(v, 0, 2, 0, 0))) for v in reversed(virtual_keys)]
        assert user32.SendInput(len(events), (Input * len(events))(*events), c.sizeof(Input)) == len(events)

    def hotkey(binding):
        modifiers = binding["Modifiers"]
        chord(*([0x11] if modifiers & 2 else []), *([0x12] if modifiers & 1 else []),
              *([0x10] if modifiers & 4 else []), *([0x5B] if modifiers & 8 else []), binding["Key"])

    folder = ROOT / ".runtime" / ("layout-smoke-" + str(time.time_ns()))
    folder.mkdir(parents=True)
    settings = {**defaults(), "engine": "direct", "directStartup": "off", "comfyStartup": "off"}
    (folder / "preferences.json").write_text(json.dumps({"genStudio": settings,
        "interface": {"language": "en"}, "dialogs": {"photoshopShortcuts": {"imported": False}}}), encoding="utf-8")
    env = {**os.environ, "PHOTOCRAFT_CONFIG_DIR": str(folder), "GENSTUDIO_NO_PROCESS": "1",
           "GENGATE_NO_INFERENCE": "1", "CUDA_VISIBLE_DEVICES": ""}
    # Validate the app's automatic choice, rather than hiding the bug with an override.
    env.pop("WGPU_BACKEND", None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    token = folder / "control.token"
    process = mcp = None
    with args.exe.open("rb") as stream:
        executable_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"real_inference": False, "cases": [], "passed": False, "editorSha256": executable_hash}

    def save():
        (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (ROOT / ".runtime/layout-smoke-latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    try:
        with (folder / "app.log").open("wb") as log, (folder / "mcp.log").open("wb") as bridge:
            process = subprocess.Popen([str(args.exe), "--control", str(port), "--control-token-file", str(token),
                "--automation-read-root", str(folder), "--automation-write-root", str(folder)],
                cwd=args.exe.parent, env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            for _ in range(400):
                assert process.poll() is None, "PhotoCraft exited"
                if token.exists():
                    try:
                        with socket.create_connection(("127.0.0.1", port), .2): break
                    except OSError: pass
                time.sleep(.1)
            else: raise TimeoutError("PhotoCraft did not open its control port")
            report.update(pid=process.pid, folder=str(folder))
            mcp = MCP([str(args.cli), "mcp", "--bridge", f"127.0.0.1:{port}", "--control-token-file", str(token)], bridge)
            control = lambda name, params: mcp.tool("control_call", {"method": name, "params": params})
            mcp.run("file.new", {"width": 1024, "height": 768, "background": "white"})
            mcp.run("select.rect", {"x": 220, "y": 180, "width": 220, "height": 180})
            document = mcp.run("document.inspect")
            windows = []
            @c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
            def visit(window, _):
                pid = w.DWORD()
                user32.GetWindowThreadProcessId(window, c.byref(pid))
                if pid.value == process.pid and user32.IsWindowVisible(window):
                    title = c.create_unicode_buffer(256)
                    user32.GetWindowTextW(window, title, len(title))
                    if "PhotoCraft" in title.value: windows.append(window)
                return True
            user32.EnumWindows(visit, 0)
            assert len(windows) == 1, f"Expected one visible test editor, found {len(windows)}"
            window = windows[0]
            user32.SetForegroundWindow(window)
            time.sleep(.2)
            owner = w.DWORD()
            tid = user32.GetWindowThreadProcessId(window, c.byref(owner))
            assert owner.value == process.pid and user32.GetForegroundWindow() == window, "Test editor must be foreground; no keys sent to another app"
            def current():
                assert user32.GetForegroundWindow() == window, "Foreground window changed during test"
            def layout(): return user32.GetKeyboardLayout(tid) & 0xffffffff
            def wait_layout(expected):
                deadline = time.monotonic() + 3
                while layout() != expected and time.monotonic() < deadline:
                    current(); time.sleep(.02)
                assert layout() == expected, f"Input layout mismatch: {layout():x} != {expected:x}"
            def wait_prompt(expected):
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    value = mcp.tool("ui_inspect")["aiFillLayer"]["bar"]["prompt"]
                    if value == expected: return
                    time.sleep(.05)
                raise AssertionError(f"Prompt mismatch: {value!r} != {expected!r}")
            for iteration in range(args.repeat):
                for mode in ("LastWord", "SelectedText"):
                    bar = mcp.tool("ui_inspect")["aiFillLayer"]["bar"]
                    x, y, width, height = bar["promptRect"]
                    control("ui.click", {"x": x + width / 2, "y": y + height / 2})
                    current(); chord(0x11, 0x41); chord(0x08); time.sleep(.1)
                    current(); assert user32.PostMessageW(window, 0x50, 0, 0x4090409)
                    # This message alone deadlocked the original automatic GPU setup.
                    wait_layout(0x4090409)
                    mcp.tool("ui_inspect")
                    for key in "GHBDTN":
                        current(); chord(ord(key)); time.sleep(.04)
                    wait_prompt("ghbdtn")
                    control("ui.move", {"x": 700, "y": 200})
                    if mode == "SelectedText": chord(0x11, 0x41); time.sleep(.1)
                    current(); hotkey(keys[mode]); wait_prompt("привет")
                    wait_layout(0x4190419)
                    # Correct the same word back, including repeated use of both modes.
                    if mode == "SelectedText": chord(0x11, 0x41); time.sleep(.1)
                    current(); hotkey(keys[mode]); wait_prompt("ghbdtn")
                    wait_layout(0x4090409)
                    for _ in range(8): current(); chord(0x08); time.sleep(.025)
                    wait_prompt("")
                    assert mcp.run("document.inspect") == document, "Prompt editing changed the document"
                    report["cases"].append({"mode": mode, "iteration": iteration + 1, "both_directions": True,
                                            "backspace_outside_field": True, "document_unchanged": True})
                    save(); print(f"PASS {mode} {iteration + 1}", flush=True)
            for block in mcp.tool("ui_screenshot", {"max_side": 1400})["content"]:
                if block["type"] == "image": (folder / "result.png").write_bytes(base64.b64decode(block["data"]))
            report["passed"] = True
            save()
    finally:
        save()
        if mcp:
            mcp.process.terminate(); mcp.process.wait(5)
        if process and process.poll() is None:
            # Only our disposable profile/document is closed, even if a regression hangs.
            process.terminate(); process.wait(10)
        if original_foreground: user32.SetForegroundWindow(original_foreground)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__": main()
