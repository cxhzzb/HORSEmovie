# -*- coding: utf-8 -*-
"""把 HORSEmovie 仓库装成 DSH skill，并做 Windows/Linux 双平台适配。

    python port_and_install.py [--repo <仓库路径>] [--skills <skill根>] [--dry-run]

改动是可追溯的纯文本替换，幂等：已经在目标状态的文件不会被动第二次。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Windows 控制台默认 GBK，装中文/符号输出要先切到 UTF-8
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CONFIG_IMPORT = '''\
# --- _horse 配置层：平台相关路径集中在这里（Linux + Windows）-----------------
import sys as _sys
from pathlib import Path as _Path
for _p in (2, 1, 3):
    try:
        _cand = _Path(__file__).resolve().parents[_p] / "_horse"
    except IndexError:
        continue
    if (_cand / "config.py").is_file():
        _sys.path.insert(0, str(_cand))
        break
import config as _horse_config  # noqa: E402
FFMPEG = _horse_config.FFMPEG
FFPROBE = _horse_config.FFPROBE
'''


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8", newline="\n")


class Patcher:
    def __init__(self, dry: bool):
        self.dry = dry
        self.log: list[str] = []

    def sub(self, path: Path, old: str, new: str, *, required: bool = True,
            count: int = 1) -> bool:
        text = read(path)
        if old not in text:
            if required:
                self.log.append(f"  ✗ {path.name}: 没找到目标文本 -> {old.splitlines()[0][:70]}")
            return False
        if count == -1:
            text = text.replace(old, new)
            n = "all"
        else:
            text = text.replace(old, new, count)
            n = count
        if not self.dry:
            write(path, text)
        self.log.append(f"  ✓ {path.name}: 替换 {n} 处")
        return True

    def insert_config(self, path: Path) -> bool:
        """在 `from pathlib import Path` 之后插入配置层导入。"""
        text = read(path)
        if "_horse 配置层" in text:
            self.log.append(f"  · {path.name}: 配置层已存在，跳过")
            return False
        anchor = "from pathlib import Path\n"
        if anchor not in text:
            self.log.append(f"  ✗ {path.name}: 找不到 pathlib 导入锚点")
            return False
        text = text.replace(anchor, anchor + "\n" + CONFIG_IMPORT, 1)
        if not self.dry:
            write(path, text)
        self.log.append(f"  ✓ {path.name}: 注入配置层")
        return True


def port(comfy: Path, horse: Path, p: Patcher, repo_root: Path) -> None:
    cfg = {"PROJECTS": "_horse_config.PROJECTS",
           "COMFY_DIR": "_horse_config.COMFY_DIR",
           "HOST": "_horse_config.HOST"}

    # ---------------------------------------------------------------- comfy.py
    f = comfy / "comfy.py"
    p.insert_config(f)
    p.sub(f, '''HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
BASE = f"http://{HOST}"
HOME = Path.home()
CACHE = Path(os.environ.get("COMFY_BRIDGE_CACHE", HOME / ".cache" / "dsh-comfy"))
PROJECTS = Path(os.environ.get("COMFY_PROJECTS", HOME / "comfy-projects"))
WORKFLOW_DIRS = [Path(p) for p in os.environ.get(
    "COMFY_WORKFLOW_DIRS",
    f"{HOME}/ComfyUI/user/default/workflows:{HOME}/ComfyUI/user",
).split(":")]''',
          '''HOST = _horse_config.HOST
BASE = _horse_config.BASE
HOME = _horse_config.HOME
CACHE = _horse_config.CACHE
PROJECTS = _horse_config.PROJECTS
WORKFLOW_DIRS = list(_horse_config.WORKFLOW_DIRS)''')
    p.sub(f,
          'raise SystemExit(f"ComfyUI not reachable at {BASE} — start it with ~/ComfyUI/启动ComfyUI.sh")',
          'raise SystemExit(f"ComfyUI not reachable at {BASE} — 先启动 ComfyUI '
          '(Windows: C:\\\\COMFYUI\\\\启动ComfyUI.bat)")')

    # ------------------------------------------------------------ run_film.py
    f = comfy / "run_film.py"
    p.insert_config(f)
    p.sub(f, '''PROJECTS = Path.home() / "comfy-projects"
COMFY_DIR = Path.home() / "ComfyUI"
HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")''',
          '''PROJECTS = _horse_config.PROJECTS
COMFY_DIR = _horse_config.COMFY_DIR
HOST = _horse_config.HOST''')
    p.sub(f, '''def comfy_pid() -> str | None:
    try:
        out = subprocess.run(["pgrep", "-f", "main.py --listen"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    pids = [p for p in out.split() if p.strip()]
    return pids[0] if pids else None''',
          '''def comfy_pid() -> str | None:
    """找出正在跑的 ComfyUI 进程（pgrep / tasklist 各自可用就用哪个）。"""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
                                 capture_output=True, text=True, timeout=15).stdout
            pids = [ln.split(",")[1].strip('"') for ln in out.splitlines()[1:] if ln.strip('"')]
            return pids[0] if pids else None
        out = subprocess.run(["pgrep", "-f", "main.py --listen"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    pids = [p for p in out.split() if p.strip()]
    return pids[0] if pids else None''')
    p.sub(f, '''    py = COMFY_DIR / ".venv/bin/python"
    if not py.exists():
        log(f"!! cannot start: {py} missing")
        return False
    env = dict(os.environ)
    ggml = COMFY_DIR / ".venv/lib/python3.12/site-packages/llama_cpp/lib"
    if ggml.is_dir():
        env["LD_LIBRARY_PATH"] = f"{ggml}{':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else ''}"''',
          '''    py = _horse_config.venv_python(COMFY_DIR)
    if not py.exists():
        log(f"!! cannot start: {py} missing")
        return False
    env = dict(os.environ)
    if os.name != "nt":
        ggml = COMFY_DIR / ".venv/lib/python3.12/site-packages/llama_cpp/lib"
        if ggml.is_dir():
            env["LD_LIBRARY_PATH"] = (
                f"{ggml}{':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else ''}")''')
    p.sub(f, '''    subprocess.Popen([str(py), "main.py", "--listen", "127.0.0.1", "--port", "8188"],
                     cwd=str(COMFY_DIR), env=env, stdout=logf, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, start_new_session=True)''',
          '''    kwargs = {} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([str(py), "main.py", "--listen", "127.0.0.1", "--port", "8188"],
                     cwd=str(COMFY_DIR), env=env, stdout=logf, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, **kwargs)''')
    p.sub(f, 'log(f"!! ComfyUI 在 {BOOT_TIMEOUT}s 内没起来，见 ~/comfy-projects/_comfyui_autostart.log")',
          'log(f"!! ComfyUI 在 {BOOT_TIMEOUT}s 内没起来，见 {PROJECTS}/_comfyui_autostart.log")')

    # --------------------------------------------------- 统一 PROJECTS 的脚本
    for name in ("render_shots.py", "render_cast.py", "render_fourview.py",
                 "qa_frames.py", "assemble.py", "contact_sheet.py"):
        f = comfy / name
        p.insert_config(f)
        p.sub(f, 'PROJECTS = Path.home() / "comfy-projects"',
              'PROJECTS = _horse_config.PROJECTS')

    # -------------------------------------------------- HORSEmovie 侧两个脚本
    f = horse / "scripts" / "qa_shot.py"
    p.insert_config(f)
    p.sub(f, 'PROJECTS = Path.home() / "comfy-projects"',
          'PROJECTS = _horse_config.PROJECTS')
    text = read(f)
    if '"ffmpeg"' in text or '"ffprobe"' in text:
        for old, new in (('["ffprobe",', '[FFPROBE,'), ('["ffmpeg",', '[FFMPEG,'),
                         ('"ffmpeg", "-v"', 'FFMPEG, "-v"')):
            text = text.replace(old, new)
        if not p.dry:
            write(f, text)
        p.log.append("  ✓ qa_shot.py: ffmpeg/ffprobe 改为可配置路径")

    f = horse / "scripts" / "calibrate.py"
    p.insert_config(f)
    p.sub(f, '''COMFY_SKILL = Path.home() / ".dsh/skills/comfyui"
RENDER = COMFY_SKILL / "render_shots.py"
PROJECTS = Path.home() / "comfy-projects"
HOST = "127.0.0.1:8188"''',
          '''COMFY_SKILL = _horse_config.skill_dir("comfyui")
RENDER = COMFY_SKILL / "render_shots.py"
PROJECTS = _horse_config.PROJECTS
HOST = _horse_config.HOST''')

    # ------------------------------------ skill 名必须是 kebab-case
    # DSH 的 filesystem provider 用 ^[a-z0-9]+(?:-[a-z0-9]+)*$ 校验 name，
    # 不匹配的文件会被【静默忽略】（只写一条 warn 日志），skill 直接进不了目录。
    # 上游原来是 HORSEmovie（含大写）——在本机必须改成 horsemovie。
    f = horse / "SKILL.md"
    p.sub(f, "\nname: HORSEmovie\n", "\nname: horsemovie\n")
    p.sub(f, "~/.dsh/skills/HORSEmovie/", "~/.dsh/skills/horsemovie/")
    p.sub(f, "`~/.dsh/skills/HORSEmovie/scripts/calibrate.py`",
          "`~/.dsh/skills/horsemovie/scripts/calibrate.py`", required=False)

    # ------------------------------------ 仓库级脚本：doctor.py（安装自检）
    # 注意：补丁是就地改仓库源文件的，第二次运行时锚点已被替换掉，
    # 所以 doctor.py 的每条替换都允许"已打过就没找到"。
    f = repo_root / "doctor.py"
    if f.is_file():
        p.insert_config(f)
        for old, new in (
            ("HOME = Path.home()", "HOME = _horse_config.HOME"),
            ('''    root = HOME / ".dsh/skills"
    if not root.is_dir():
        rep.bad(f"没有 {root}", "这台机器可能没装 DSH；skill 必须在 DSH 里才能用")
        return''',
             '''    root = _horse_config.SKILLS
    if not root.is_dir():
        rep.bad(f"没有 {root}", "这台机器可能没装 DSH；skill 必须在 DSH 里才能用")
        return'''),
            ('''    mroot = comfy / "models"
    total = 0.0
    missing_required = 0
    for rel, gb, required in MODELS:
        p = mroot / rel
        if p.is_file():
            size = p.stat().st_size / 1024 ** 3
            total += size
            flag = "" if gb is None or abs(size - gb) < max(0.3, gb * 0.15) else f"  ⚠ 体积异常（预期 {gb} GB）"
            rep.ok(f"{size:5.1f} GB  {rel}{flag}")''',
             '''    total = 0.0
    missing_required = 0
    for rel, gb, required in MODELS:
        p = _horse_config.find_model(rel)
        if p is not None:
            size = p.stat().st_size / 1024 ** 3
            total += size
            flag = "" if gb is None or abs(size - gb) < max(0.3, gb * 0.15) else f"  ⚠ 体积异常（预期 {gb} GB）"
            where = "" if p.parent == comfy / "models" else f"   [{p.parent}]"
            rep.ok(f"{size:5.1f} GB  {rel}{flag}{where}")'''),
            ('''            rep.bad(f"缺失  {rel}", "见 models.txt")''',
             '''            rep.bad(f"缺失  {rel}",
                    "见 models.txt（也可能是量化版文件名不同，改工作流里的名字即可）")'''),
            ('''    ap.add_argument("--comfy", default=str(HOME / "ComfyUI"))''',
             '''    ap.add_argument("--comfy", default=str(_horse_config.COMFY_DIR))'''),
            ('''        rep.warn(f"{host} 没在跑", "出片前先 ~/ComfyUI/启动ComfyUI.sh；"
                                   "run_film.py 也能自动拉起（需 启动ComfyUI.sh 在场）")''',
             '''        rep.warn(f"{host} 没在跑", f"出片前先启动 ComfyUI（{_horse_config.COMFY_DIR}）；"
                                   "run_film.py 也能自动拉起")'''),
            ('launcher = comfy / "启动ComfyUI.sh"',
             'launcher = comfy / ("启动ComfyUI.bat" if os.name == "nt" else "启动ComfyUI.sh")'),
            ('rep.ok("启动脚本 启动ComfyUI.sh 在")', 'rep.ok(f"启动脚本 {launcher.name} 在")'),
            ('rep.warn("没找到 启动ComfyUI.sh", "run_film.py 的看门狗按这个脚本的逻辑拉起服务；"\n                                          "没有它就得自己保证服务器常开")',
             'rep.warn(f"没找到 {launcher.name}", "run_film.py 的看门狗按这个脚本的逻辑拉起服务；"\n                                          "没有它就得自己保证服务器常开")'),
        ):
            p.sub(f, old, new, required=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=r"C:\DEEPHARNESS\HORSEmovie")
    ap.add_argument("--skills", default=str(Path.home() / ".dsh" / "skills"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo)
    skills = Path(args.skills)
    if not (repo / "skills").is_dir():
        print(f"仓库路径不对：{repo}")
        return 2

    p = Patcher(args.dry_run)
    print("=" * 70)
    print(f"  仓库   : {repo}")
    print(f"  skill  : {skills}")
    print(f"  模式   : {'DRY-RUN' if args.dry_run else '实际写入'}")
    print("=" * 70)

    # 1) 拷贝两个 skill。目标目录名也必须是 kebab-case —— DSH 是按
    #    <root>/<name>/SKILL.md 找，且 name 要过正则，目录名同时就是 skill id。
    skills.mkdir(parents=True, exist_ok=True)
    for src_name, dst_name in (("HORSEmovie", "horsemovie"), ("comfyui", "comfyui")):
        src, dst = repo / "skills" / src_name, skills / dst_name
        if dst.exists() and not args.dry_run:
            shutil.rmtree(dst)
        if not args.dry_run:
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"  ✓ 安装 skill {src_name} -> {dst}")

    # dry-run 时也拿到一份可改的副本，否则替换无从验证
    if args.dry_run and not (skills / "comfyui" / "comfy.py").is_file():
        for src_name, dst_name in (("HORSEmovie", "horsemovie"), ("comfyui", "comfyui")):
            shutil.copytree(repo / "skills" / src_name, skills / dst_name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print("  · dry-run：已拷入副本以便验证替换")

    # 2) 平台适配
    print("\n[平台适配]")
    port(skills / "comfyui", skills / "horsemovie", p, repo)
    for line in p.log:
        print(line)

    # 2b) doctor.py 是仓库级自检脚本，skill 目录里没有；装到 _horse 旁边
    doc_src, doc_dst = repo / "doctor.py", skills / "_horse" / "doctor.py"
    if doc_src.is_file():
        if not args.dry_run:
            shutil.copy2(doc_src, doc_dst)
        print(f"  ✓ 自检脚本 -> {doc_dst}")

    # 3) 工作流
    print("\n[工作流]")
    wf_dir = Path(r"C:\COMFYUI\Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
                  r"\user\default\workflows")
    if wf_dir.is_dir():
        import time
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for src in sorted((repo / "comfyui-workflows").glob("*.json")):
            dst = wf_dir / src.name
            if dst.exists():
                bak = dst.with_name(dst.name + f".bak-{stamp}")
                if not args.dry_run:
                    shutil.copy2(dst, bak)
                print(f"  · {src.name} 已存在，原文件备份为 {bak.name}")
            if not args.dry_run:
                shutil.copy2(src, dst)
            print(f"  ✓ {src.name}")
    else:
        print(f"  ✗ 找不到工作流目录 {wf_dir}")

    # 4) 管线图
    print("\n[_pipelines]")
    proj = Path.home() / "comfy-projects" / "_pipelines"
    if not args.dry_run:
        proj.mkdir(parents=True, exist_ok=True)
    for src in sorted((repo / "pipelines").glob("*.json")):
        if not args.dry_run:
            shutil.copy2(src, proj / src.name)
        print(f"  ✓ {src.name} -> {proj}")

    # 5) 装完之后的本机补丁 —— 必须放在拷贝之后，因为安装会覆盖
    #    comfy.py 与 pipelines.json，手工改动会被冲掉。
    print("\n[本机补丁]")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import patch_local
    old_argv = sys.argv
    try:
        sys.argv = ["patch_local.py", "--skills", str(skills)] + \
                   (["--dry-run"] if args.dry_run else [])
        code = patch_local.main()
    finally:
        sys.argv = old_argv
    if code != 0:
        print("✗ 本机补丁失败")
        return code

    # 6) 同步到自定义 skill 根。
    #    DSH 宿主的 cwd 未必是会话工作区，所以 <cwd>/.dsh/skills 不可靠；
    #    profile 里用 customSkillDirs 显式声明 C:/DEEPHARNESS/skills，
    #    这里把装好的 skill（含 _horse 配置层）同步过去，保证两处一致。
    custom_root = Path(r"C:\DEEPHARNESS\skills")
    print(f"\n[同步到自定义 skill 根] {custom_root}")
    if not args.dry_run:
        custom_root.mkdir(parents=True, exist_ok=True)
    for name in ("horsemovie", "comfyui", "_horse"):
        src, dst = skills / name, custom_root / name
        if not src.is_dir():
            print(f"  · {name} 不存在，跳过")
            continue
        if dst.exists() and not args.dry_run:
            shutil.rmtree(dst)
        if not args.dry_run:
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"  ✓ {name} -> {dst}")

    print("\n装完了。下一步：doctor.py 自检，再起 ComfyUI 验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
