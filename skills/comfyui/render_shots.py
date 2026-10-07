#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_shots.py — batch-render a shot plan through ComfyUI, one job at a time.

Reads ~/comfy-projects/<project>/shots.json and drives comfy.py.

  render_shots.py --project tianlie --shots 001-003 --stage still
  render_shots.py --project tianlie --shots 001 --stage motion
  render_shots.py --project tianlie --shots all --dry-run --stage still

stage=still  : Qwen-Image 2.1 text-to-image keyframe per shot
stage=motion : MiniMax H3 image-to-video using the rendered still as first frame
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMFY = HERE / "comfy.py"
SKILL_PIPELINES = HERE / "pipelines.json"
PROJECTS = Path.home() / "comfy-projects"

# Scene plates are fed to the video model as <Picture 2>. Any person in them
# leaks into the shot as a second, uncontrolled character, so keep them empty.
STILL_PIPELINE = "txt2img-qwen" if "txt2img-qwen" in json.loads(SKILL_PIPELINES.read_text("utf-8")) else "quick-txt2img"

SCENE_NEGATIVE = ("人物，人，人脸，人体，人像，人群，人影，剪影，手，people, person, human, "
                  "human face, human body, portrait, crowd, silhouette of a person, hands, "
                  "文字，水印，标签, text, watermark, logo")

# Keyframe stage: drive the image pipeline's EDIT branch with the character
# portrait as the input image. reference-to-video alone drifts the face (three
# renders of one shot gave three different men), but the edit branch holds
# identity well — it is the same branch that produces the four-view sheets.
# The result is then fed to I2V as the first frame.
KEYFRAME_PROMPT = (
    "参考图的左侧是一个人物的定妆照。以这个人物为主角，生成一张{aspect}的电影关键帧："
    "严格保持他的长相、脸型、发型、肤色、服装、道具与体型和参考图一致，不要换脸、不要改年龄；"
    "背景替换成下面的场景描述。场景描述里提到的其他人物可以按描述出现，"
    "但主角必须是参考图里的这个人。不要拼图、不要分屏。"
    "整张画面必须完整处在这个场景环境里，四个角都要是场景内容，"
    "不允许出现影棚灰底、纯色背景、空白区域或未绘制的画布。\n"
    "场景与动作：{still}"
)
KEYFRAME_PROMPT_MULTI = (
    "参考图的左侧并排着{n}个人物的定妆照。请把这{n}个人物全部放进同一个场景，"
    "生成一张{aspect}的电影关键帧：每个人都要严格保持各自的长相、脸型、发型、肤色、服装、道具与体型"
    "和参考图里对应的那个人一致，不要互换他们的脸或服装、不要换脸、不要改年龄；"
    "背景替换成下面的场景描述。不要拼图、不要分屏、不要出现多余的人。"
    "整张画面必须完整处在这个场景环境里，四个角都要是场景内容，"
    "不允许出现影棚灰底、纯色背景、空白区域或未绘制的画布。\n"
    "场景与动作：{still}"
)
KEYFRAME_NEGATIVE = ("拼图，分屏，换脸，不同的脸，年龄变化，文字，水印，标签，"
                     "影棚背景，灰色背景，白色背景，纯色背景，空白区域，未填充的画布，"
                     "collage, split screen, grid, different face, changed identity, "
                     "studio backdrop, plain grey background, white background, "
                     "flat colour background, blank area, empty canvas, "
                     "text, watermark, logo")


def build_landscape_base(portraits, dest: Path, w: int, h: int,
                         height_ratio: float = 0.94, bg=(237, 237, 237)) -> Path:
    """16:9 canvas with one or more portraits laid out along the left.

    The edit branch's output size follows the INPUT image's aspect ratio, so a
    landscape base is what yields a landscape keyframe. Passing several
    portraits lets a keyframe carry more than one locked character.
    """
    from PIL import Image  # noqa: PLC0415  (only needed for the keyframe stage)

    if isinstance(portraits, (str, Path)):
        portraits = [portraits]
    ph = int(h * height_ratio)
    canvas = Image.new("RGB", (w, h), bg)
    x = int(w * 0.03)
    for path in portraits:
        img = Image.open(path).convert("RGB")
        pw = int(img.width * ph / img.height)
        canvas.paste(img.resize((pw, ph), Image.LANCZOS), (x, (h - ph) // 2))
        x += pw + int(w * 0.01)
    canvas.save(dest)
    return dest


def run_comfy(args, dry):
    cmd = [sys.executable, str(COMFY), *args]
    print("$ " + " ".join(cmd))
    if dry:
        return 0
    return subprocess.call(cmd)


def upload_reference(path: Path, subfolder: str, dry: bool):
    """Put a rendered still into ComfyUI's input dir; returns the LoadImage value."""
    if dry:
        print(f"$ upload {path.name} --subfolder {subfolder}")
        return f"{subfolder}/{path.name}"
    sys.path.insert(0, str(HERE))
    import comfy  # noqa: E402  (skill-local import)
    resp = comfy.upload_image(str(path), subfolder)
    sub = resp.get("subfolder") or subfolder
    return f"{sub}/{resp.get('name', path.name)}" if sub else resp.get("name", path.name)


def parse_shots(spec: str, shots):
    if spec in ("all", "*"):
        return shots
    wanted = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            wanted.update(f"{i:03d}" for i in range(lo, hi + 1))
        elif part:
            wanted.add(f"{int(part):03d}")
    return [s for s in shots if s["id"] in wanted]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--shots", default="all")
    ap.add_argument("--stage", choices=["still", "motion", "scene", "keyframe"], default="still")
    ap.add_argument("--via", choices=["main", "i2v"], default="main",
                    help="motion stage: 'main' = the user's main video pipeline (reference-to-video), "
                         "'i2v' = MiniMax_H3_I2V from the rendered still")
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=576)
    ap.add_argument("--aspect", default="16:9 (Widescreen)")
    ap.add_argument("--megapixels", type=float, default=0.4)
    ap.add_argument("--char-ref", help="character reference image (default: <project>/refs/character.png)")
    ap.add_argument("--no-char-ref", action="store_true", help="never attach <Picture 1> (A/B the identity lock)")
    ap.add_argument("--no-scene-ref", action="store_true", help="never attach <Picture 2> (A/B the identity lock)")
    ap.add_argument("--kf-megapixels", type=float, default=1.5,
                    help="keyframe stage: ImageScaleToTotalPixels value (edit branch output follows it)")
    ap.add_argument("--seed", type=int, default=None, help="base seed; shot index is added")
    ap.add_argument("--timeout", type=float, default=2400)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    project_dir = PROJECTS / args.project
    plan = json.loads((project_dir / "shots.json").read_text("utf-8"))
    pipes = json.loads(SKILL_PIPELINES.read_text("utf-8"))
    selected = parse_shots(args.shots, plan["shots"])
    if not selected:
        raise SystemExit("no shots selected")

    style = plan.get("style_bible", "")
    out_dir = project_dir / "out"
    ref_dir = project_dir / "refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for index, shot in enumerate(selected):
        sid = shot["id"]
        seed = (args.seed if args.seed is not None else 1000 + int(sid)) + index
        if args.stage in ("still", "scene"):
            p = pipes[STILL_PIPELINE]["patch"]
            if args.stage == "scene":
                base = shot.get("scene_prompt") or shot.get("still")
                prefix = f"dsh/{args.project}/scene_{sid}"
                label = f"scene_{sid}"
            else:
                base = shot.get("still")
                prefix = f"dsh/{args.project}/{sid}_still"
                label = f"{sid}_still"
            prompt = ", ".join(x for x in (base, style) if x)
            argv = [
                "run", pipes[STILL_PIPELINE]["workflow"],
                "--set", f"{p['prompt']}={json.dumps(prompt, ensure_ascii=False)}",
                "--set", f"{p['width']}={args.width}",
                "--set", f"{p['height']}={args.height}",
                "--set", f"{p['steps']}={args.steps}",
                "--set", f"{p['seed']}={seed}",
                "--set", f"{p['output']}={prefix}",
                "--wait", "--project", args.project, "--shot", label,
                "--timeout", str(args.timeout),
            ]
            if args.stage == "scene":
                argv += ["--set", f"{p['negative']}={json.dumps(SCENE_NEGATIVE, ensure_ascii=False)}"]
        elif args.stage == "keyframe":
            entry = pipes["image-main"]
            p = entry["patch"]
            # Who the keyframe must lock. kf_chars (list) > kf_char > char.
            # `char` is the owner of <Picture 1>, which is not always the shot's
            # subject — 013 is Xihe materialising while <Picture 1> is Changgeng.
            if args.char_ref:
                chars = [Path(args.char_ref).expanduser()]
            else:
                ids = shot.get("kf_chars") or ([shot["kf_char"]] if shot.get("kf_char")
                                               else ([shot["char"]] if shot.get("char") else []))
                chars = []
                for cid in ids:
                    cand = ref_dir / f"character_{cid}.png"
                    if cand.exists():
                        chars.append(cand)
                    else:
                        print(f"[{sid}] no reference for '{cid}'; skipped")
                if ids and not chars:
                    chars = [ref_dir / "character.png"]
                if not chars:
                    # No character in this shot: its scene plate already is a
                    # correct 16:9 frame, so let the I2V stage use that directly
                    # instead of editing an invented face into an empty shot.
                    print(f"[{sid}] no character in shot; keyframe skipped "
                          f"(refs/{sid}.png becomes the first frame)")
                    continue
            if not chars[0].exists():
                print(f"[{sid}] no character reference at {chars[0]}; skipping keyframe")
                continue
            base = out_dir / f"_kf_base_{sid}.png"
            if args.dry_run:
                name = f"dry/{base.name}"
            else:
                build_landscape_base(chars, base, args.width, args.height)
                name = upload_reference(base, f"dsh_{args.project}", False)
            aspect = "16:9 横版" if args.width >= args.height else "9:16 竖版"
            still = shot.get("still") or shot.get("cn")
            if len(chars) > 1:
                prompt = KEYFRAME_PROMPT_MULTI.format(n=len(chars), aspect=aspect, still=still)
            else:
                prompt = KEYFRAME_PROMPT.format(aspect=aspect, still=still)
            argv = [
                "run", entry["workflow"],
                "--set", f"{p['edit_image']}={json.dumps(name)}",
                "--set", f"{p['edit_prompt']}={json.dumps(prompt, ensure_ascii=False)}",
                "--set", f"{p['edit_steps']}={args.steps}",
                "--set", f"{p['edit_seed']}={seed}",
                "--set", f"{p['edit_output']}=dsh/{args.project}/{sid}_key",
                "--set", f"{p['edit_negative']}={json.dumps(KEYFRAME_NEGATIVE, ensure_ascii=False)}",
            ]
            # Mirror the verified four-view recipe for the two sizing knobs:
            # ImageScaleToTotalPixels (#532) normalises the input, and the edit
            # branch's output follows the input aspect. #13 is a different
            # resolution selector and is left at whatever the user saved.
            qv = pipes.get("qwen-fourview", {}).get("patch", {})
            if "megapixels" in qv:
                argv += ["--set", f"{qv['megapixels']}={args.kf_megapixels}"]
            if "resolution" in qv:
                argv += ["--set", f"{qv['resolution']}=1024"]
            for sel in entry.get("branch_drops", {}).get("只做编辑/图生图（跳过文生图分支）", []):
                argv += ["--drop", sel]
            argv += ["--drop", "Image Comparer (rgthree)"]
            argv += ["--wait", "--project", args.project, "--shot", f"{sid}_key",
                     "--timeout", str(args.timeout)]
        elif args.via == "main":
            entry = pipes["video-main"]
            p = entry["patch"]
            prompt = shot.get("final") or shot.get("motion") or shot["cn"]
            argv = [
                "run", entry["workflow"],
                "--set", f"{p['prompt_final']}={json.dumps(prompt, ensure_ascii=False)}",
                "--set", f"{p['duration_s']}={shot.get('dur', 5)}",
                "--set", f"{p['aspect_ratio']}={json.dumps(args.aspect)}",
                "--set", f"{p['megapixels']}={args.megapixels}",
                "--set", f"{p['seed']}={seed}",
                "--set", f"{p['output']}=dsh/{args.project}/{sid}_motion",
                "--wait", "--project", args.project, "--shot", f"{sid}_motion",
                "--timeout", str(args.timeout),
            ]
            # Reference slots are only wired for <Picture 1> (character) and
            # <Picture 2> (scene). Attach only the ones the prompt actually
            # mentions, otherwise an unrelated reference hijacks the shot.
            # A shot can name its own lead via shots[].char, which resolves to
            # refs/character_<id>.png before falling back to refs/character.png.
            wants_char = "<Picture 1>" in prompt and not args.no_char_ref
            wants_scene = "<Picture 2>" in prompt and not args.no_scene_ref
            if args.char_ref:
                char = Path(args.char_ref).expanduser()
            elif shot.get("char"):
                cand = ref_dir / f"character_{shot['char']}.png"
                char = cand if cand.exists() else ref_dir / "character.png"
            else:
                char = ref_dir / "character.png"
            scene = ref_dir / f"{sid}.png"
            if wants_char and char.exists():
                name = upload_reference(char, f"dsh_{args.project}", args.dry_run)
                if name:
                    argv += ["--set", f"{p['reference_1']}={json.dumps(name)}"]
            elif args.no_char_ref and "<Picture 1>" in prompt:
                print(f"[{sid}] <Picture 1> disabled by --no-char-ref; character reference skipped")
            elif wants_char:
                print(f"[{sid}] no character reference at {char}; running without <Picture 1>")
            else:
                print(f"[{sid}] prompt has no <Picture 1>; character reference skipped")
            if wants_scene and scene.exists():
                name = upload_reference(scene, f"dsh_{args.project}", args.dry_run)
                if name:
                    argv += ["--set", f"{p['reference_2']}={json.dumps(name)}"]
            elif args.no_scene_ref and "<Picture 2>" in prompt:
                print(f"[{sid}] <Picture 2> disabled by --no-scene-ref; scene reference skipped")
            elif wants_scene:
                print(f"[{sid}] no scene reference at {scene}; running without <Picture 2>")
            else:
                print(f"[{sid}] prompt has no <Picture 2>; scene reference skipped")
            argv = argv[:1] + argv[1:]  # keep order: options before --wait is fine
        else:
            # First frame, best available: an identity-locked keyframe, then a
            # plain still, then the shot's own scene plate (all that a
            # character-free shot needs).
            frames = (sorted(out_dir.glob(f"{sid}_key_*.png"))
                      or sorted(out_dir.glob(f"{sid}_still_*.png"))
                      or ([ref_dir / f"{sid}.png"] if (ref_dir / f"{sid}.png").exists() else []))
            if not frames:
                print(f"[{sid}] no keyframe/still/scene plate; render the keyframe stage first")
                continue
            name = upload_reference(frames[-1], f"dsh_{args.project}", args.dry_run)
            if not name:
                print(f"[{sid}] upload failed")
                continue
            p = pipes["i2v-minimax"]["patch"]
            # I2V carries identity in the first frame, so the prompt only needs
            # the action. `final` is written for reference-to-video and points at
            # <Picture N> refs that this graph has no slots for.
            prompt = shot.get("motion") or shot.get("still") or shot["cn"]
            entry = pipes["i2v-minimax"]
            argv = [
                "run", entry["workflow"],
                "--set", f"{p['first_frame']}={json.dumps(name)}",
                "--set", f"{p['prompt']}={json.dumps(prompt, ensure_ascii=False)}",
                "--set", f"{p['duration_s']}={shot.get('dur', 5)}",
                # The saved I2V workflow ships with 1:1 (Square); without these
                # two the render comes back 640x640 regardless of the first frame.
                "--set", f"{p['aspect_ratio']}={json.dumps(args.aspect)}",
                "--set", f"{p['megapixels']}={args.megapixels}",
                "--set", f"{p['seed']}={seed}",
                "--set", f"{p['output']}=dsh/{args.project}/{sid}_motion",
                "--wait", "--project", args.project, "--shot", f"{sid}_motion",
                "--timeout", str(args.timeout),
            ]
            # 质量档固定参数（steps / LoRA 强度），见 pipelines.json 的 _quality 说明
            for sel, val in (entry.get("fixed") or {}).items():
                argv += ["--set", f"{sel}={json.dumps(val)}"]
        print(f"\n=== {sid} {shot.get('scene','')} {shot.get('cn','')} [{args.stage}] seed={seed}")
        code = run_comfy(argv, args.dry_run)
        results.append({"id": sid, "stage": args.stage, "seed": seed, "exit": code})

    (project_dir / f"render-log-{args.stage}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
    failed = [r for r in results if r["exit"] != 0]
    print(f"\n{len(results) - len(failed)}/{len(results)} ok -> {out_dir}")
    if failed:
        print("failed:", ", ".join(r["id"] for r in failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
