#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_film.py — unattended shot rendering with ComfyUI crash recovery.

The 8 GB / 16 GB box can lose ComfyUI to an OOM kill part-way through a long
batch. This driver runs one shot at a time so that a crash costs at most one
shot, and it re-launches the server (headless, no Konsole/browser) before
retrying. It is idempotent: shots that already have output newer than their
first frame are skipped, so it can simply be re-run to resume.

  run_film.py --project tianlie --stage motion --via i2v --shots all
  run_film.py --project tianlie --stage motion --via i2v --shots 001-013
  run_film.py --project tianlie --stage motion --via i2v --shots all --dry-run
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# --- Windows: the console codepage (GBK/cp936) cannot encode '▶' or Chinese
# text, which every workflow name here contains. Force UTF-8 on stdout/stderr;
# errors="replace" so a report never dies half-printed.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
RENDER = HERE / "render_shots.py"
HOME = Path.home()


# --------------------------------------------------------------- 本机配置（部署时写入）
# 与 comfy.py 共用同一个 local.json；env 优先。
def _local_config() -> dict:
    f = HERE / "local.json"
    if f.is_file():
        try:
            # utf-8-sig：PowerShell 写出来的 JSON 可能带 BOM
            return json.loads(f.read_text("utf-8-sig"))
        except Exception:
            return {}
    return {}


LOCAL = _local_config()
PROJECTS = Path(os.environ.get("COMFY_PROJECTS", LOCAL.get("projects") or HOME / "comfy-projects"))
COMFY_DIR = Path(LOCAL.get("comfy_base") or HOME / "ComfyUI")
LAUNCHER = LOCAL.get("launcher")
HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
BOOT_TIMEOUT = 300          # seconds to wait for a cold start
BOOT_POLL = 3


def log(msg: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    with open(PROJECTS / "_run_film.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def server_up(timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}/system_stats", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def comfy_pid() -> str | None:
    """PID of a running ComfyUI server, if we can tell (pgrep on POSIX)."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15).stdout
            for line in out.splitlines():
                if "main.py" in line and "--port" in line:
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        return parts[-1]
            return None
        out = subprocess.run(["pgrep", "-f", "main.py --listen"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10).stdout
    except Exception:
        return None
    pids = [p for p in out.split() if p.strip()]
    return pids[0] if pids else None


def start_server() -> bool:
    """Cold-start ComfyUI headless.

    优先级：local.json 的 `server_argv`（最准，能把 `--fast-disk`、模型路径配置等
    原样带上）> local.json 的 `launcher`（.bat）> POSIX 的 venv python + main.py。

    为什么非要 argv：H3 视频链路要 20GB 主模型 + 25GB 文本编码器，**必须**带 `--fast-disk`
    才能从 NVMe mmap；不带的话 ComfyUI 会把权重往内存里拷，内存不够时实测卡 2 小时都进不到采样。
    ComfyUI Desktop 自带的那个启动脚本里没有这个参数，所以只靠 .bat 会在"OOM 后自动拉起"
    这条路上把整晚的批量渲染毁掉 —— 看门狗必须用与正常运行完全一致的命令行拉起服务。
    """
    logf = open(PROJECTS / "_comfyui_autostart.log", "a", encoding="utf-8")
    logf.write(f"\n===== restart at {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")

    argv = LOCAL.get("server_argv") or []
    cwd = LOCAL.get("server_cwd") or str(COMFY_DIR)

    if argv:
        exe = Path(argv[0])
        if not exe.exists():
            log(f"!! cannot start: {exe} missing")
            logf.close()
            return False
        log("ComfyUI 启动中（local.json 的 server_argv）...")
        logf.write("launch: " + " ".join(argv) + f"\ncwd: {cwd}\n")
        logf.flush()
        subprocess.Popen([str(a) for a in argv], cwd=cwd,
                         stdout=logf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL,
                         creationflags=subprocess.DETACHED_PROCESS
                         | subprocess.CREATE_NEW_PROCESS_GROUP)
    elif os.name == "nt" and LAUNCHER:
        launcher = Path(LAUNCHER)
        if not launcher.exists():
            log(f"!! cannot start: {launcher} missing")
            logf.close()
            return False
        log(f"ComfyUI 启动中（{launcher.name}）...")
        logf.write(f"launch: {launcher}\n")
        logf.flush()
        subprocess.Popen(["cmd", "/c", "start", "", str(launcher)],
                         cwd=str(launcher.parent),
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL,
                         creationflags=subprocess.DETACHED_PROCESS
                         | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        py = COMFY_DIR / ".venv/bin/python"
        if not py.exists():
            log(f"!! cannot start: {py} missing")
            logf.close()
            return False
        env = dict(os.environ)
        ggml = COMFY_DIR / ".venv/lib/python3.12/site-packages/llama_cpp/lib"
        if ggml.is_dir():
            env["LD_LIBRARY_PATH"] = f"{ggml}{':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else ''}"
        log("ComfyUI 启动中 ...")
        subprocess.Popen([str(py), "main.py", "--listen", "127.0.0.1", "--port", "8188"],
                         cwd=str(COMFY_DIR), env=env, stdout=logf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)

    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        if server_up():
            log(f"ComfyUI 已就绪（{int(BOOT_TIMEOUT - (deadline - time.time()))}s）")
            logf.close()
            return True
        time.sleep(BOOT_POLL)
    log(f"!! ComfyUI 在 {BOOT_TIMEOUT}s 内没起来，见 {PROJECTS / '_comfyui_autostart.log'}")
    logf.close()
    return False


def ensure_server() -> bool:
    if server_up():
        return True
    log("ComfyUI 无响应（疑似被 OOM 杀掉）")
    if comfy_pid():
        log("仍有残留进程，等 20s 再看 ...")
        time.sleep(20)
        if server_up():
            return True
    return start_server()


def parse_shots(spec: str, plan: dict) -> list[dict]:
    shots = plan["shots"]
    if spec in ("all", "*"):
        return shots
    want = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            want.update(f"{i:03d}" for i in range(int(a), int(b) + 1))
        elif part:
            want.add(f"{int(part):03d}")
    return [s for s in shots if s["id"] in want]


def newest(pattern: str) -> Path | None:
    """Newest file matching a glob pattern, or None."""
    hits = sorted(glob.glob(pattern), key=lambda p: Path(p).stat().st_mtime)
    return Path(hits[-1]) if hits else None


def first_frame_for(sid: str, out_dir: Path, ref_dir: Path) -> Path | None:
    for pat in (f"{sid}_key_*.png", f"{sid}_still_*.png"):
        f = newest(str(out_dir / pat))
        if f:
            return f
    plate = ref_dir / f"{sid}.png"
    return plate if plate.exists() else None


def done_already(sid: str, out_dir: Path, frames: Path | None) -> bool:
    out = newest(str(out_dir / f"{sid}_motion_*.mp4"))
    if not out or not frames:
        return False
    return out.stat().st_mtime >= frames.stat().st_mtime


def run_one(project: str, sid: str, args, frames: Path) -> int:
    cmd = [sys.executable, str(RENDER), "--project", project, "--shots", sid,
           "--stage", args.stage, "--via", args.via,
           "--megapixels", str(args.megapixels), "--aspect", args.aspect,
           "--timeout", str(args.timeout)]
    if args.dry_run:
        cmd.append("--dry-run")
    return subprocess.call(cmd)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--stage", default="motion", choices=["motion"])
    ap.add_argument("--via", default="i2v")
    ap.add_argument("--shots", default="all")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--megapixels", type=float, default=0.4)
    ap.add_argument("--aspect", default="16:9 (Widescreen)")
    ap.add_argument("--timeout", type=float, default=2400)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_dir = PROJECTS / args.project
    out_dir, ref_dir = project_dir / "out", project_dir / "refs"
    plan = json.loads((project_dir / "shots.json").read_text("utf-8"))
    selected = parse_shots(args.shots, plan)

    log(f"=== 开始：{len(selected)} 镜  stage={args.stage} via={args.via} "
        f"dry_run={args.dry_run} ===")

    todo, skipped, no_frame = [], [], []
    for shot in selected:
        sid = shot["id"]
        frames = first_frame_for(sid, out_dir, ref_dir)
        if frames is None:
            no_frame.append(sid)
        elif done_already(sid, out_dir, frames):
            skipped.append(sid)
        else:
            todo.append((sid, frames))

    if skipped:
        log(f"跳过已完成 {len(skipped)} 镜：{','.join(skipped)}")
    if no_frame:
        log(f"!! 没有首帧、无法渲的 {len(no_frame)} 镜：{','.join(no_frame)}")
    log(f"待渲 {len(todo)} 镜")
    if args.dry_run:
        for sid, f in todo:
            log(f"  {sid} <- {f.name}")
        return 0

    ok, failed = [], []
    for i, (sid, frames) in enumerate(todo, 1):
        if not ensure_server():
            log(f"!! 服务器起不来，中止于 {sid}")
            break
        log(f"--- [{i}/{len(todo)}] {sid} 首帧={frames.name}")
        t0 = time.time()
        code = run_one(args.project, sid, args, frames) if not args.dry_run else 1
        for attempt in range(1, args.retries + 1):
            if code == 0:
                break
            log(f"!! {sid} 第 {attempt} 次失败（exit {code}）")
            if not ensure_server():
                break
            time.sleep(10)
            code = run_one(args.project, sid, args, frames)
        if code == 0:
            ok.append(sid)
            log(f"✓ {sid} 完成（{time.time() - t0:.0f}s）")
        else:
            failed.append(sid)
            log(f"✗ {sid} 放弃（重试 {args.retries} 次仍失败）")

    log(f"=== 收工：成功 {len(ok)} / 失败 {len(failed)} / 跳过 {len(skipped)} ===")
    if failed:
        log("失败清单：" + ",".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
