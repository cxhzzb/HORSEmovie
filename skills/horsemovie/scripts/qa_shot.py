#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qa_shot.py — per-shot acceptance check for HORSEmovie renders.

Checks what a machine can check:
  * container/video/audio spec (resolution, fps, frames = 5+17k, duration, 32 kHz stereo)
  * audio content: is there speech? is there ambient? is there background music?
  * writes a review sheet (frame strip + spectrogram) for the human checks
    (identity and ghosting must be judged by eye — see references/qa.md)

  qa_shot.py out/008_motion_008_motion_00004_.mp4
  qa_shot.py --project tianlie --shot 008
  qa_shot.py <file> --frames 1,3,5 --outdir /tmp/qa

Exit code 0 = all automated checks passed, 1 = something needs attention.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


# --- Windows: the console codepage (GBK/cp936) cannot encode '▶' or Chinese
# text, which every workflow name here contains. Force UTF-8 on stdout/stderr;
# errors="replace" so a report never dies half-printed.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECTS = Path.home() / "comfy-projects"

# --- audio analysis tuning -------------------------------------------------
FRAME = 512            # FFT window @16 kHz ≈ 32 ms
HOP = 128              # 75% overlap
LOW = (20, 300)        # rumble / wind / water pressure
MID = (300, 3400)      # speech formants live here
HIGH = (3400, 8000)
SPEECH_MARGIN_DB = 8   # mid-band above its own noise floor => "active"
BGM_ACTIVE_RATIO = 0.85   # near-continuous mid energy => likely score
MIN_ACTIVE_RATIO = 0.03   # essentially no mid energy => no speech at all
VOICED_FLATNESS = 0.30    # informational only — NOT used for the verdict (see verdict_sound)


def sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


def probe(path: Path) -> dict:
    raw = sh(["ffprobe", "-v", "error", "-show_streams", "-show_format",
              "-of", "json", str(path)])
    d = json.loads(raw)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    out = {"duration": round(float(d["format"]["duration"]), 3)}
    if v:
        num, _, den = v.get("r_frame_rate", "0/1").partition("/")
        out.update(width=v["width"], height=v["height"],
                   fps=round(float(num) / float(den or 1), 3),
                   frames=int(v.get("nb_frames") or 0))
    if a:
        out.update(sample_rate=int(a["sample_rate"]), channels=a["channels"],
                   audio_codec=a["codec_name"])
    return out


def decode_wav(path: Path, tmp: Path) -> Path:
    wav = tmp / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                    "-ac", "1", "-ar", "16000", "-f", "wav", str(wav), "-y"], check=True)
    return wav


def analyse_audio(wav: Path) -> dict:
    w = wave.open(str(wav))
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    if x.size < FRAME * 4:
        return {"error": "音轨太短"}

    frames = np.lib.stride_tricks.sliding_window_view(x, FRAME)[::HOP]
    win = frames * np.hanning(FRAME)
    spec = np.abs(np.fft.rfft(win, axis=1)) + 1e-10
    f = np.fft.rfftfreq(FRAME, 1 / sr)
    t = np.arange(len(frames)) * HOP / sr

    def band(a, b):
        return 20 * np.log10(spec[:, (f >= a) & (f < b)].mean(1) + 1e-10)

    low, mid, high = band(*LOW), band(*MID), band(*HIGH)
    flatness = np.exp(np.log(spec + 1e-12).mean(1)) / (spec.mean(1) + 1e-12)
    rms = np.sqrt((frames ** 2).mean(1) + 1e-12)
    floor = float(np.percentile(mid, 20))

    active = mid > floor + SPEECH_MARGIN_DB
    runs, start = [], None
    for i, a in enumerate(active):
        if a and start is None:
            start = i
        elif not a and start is not None:
            if (i - start) * HOP / sr > 0.12:
                runs.append((round(start * HOP / sr, 2), round(i * HOP / sr, 2)))
            start = None
    if start is not None:
        runs.append((round(start * HOP / sr, 2), round(len(active) * HOP / sr, 2)))

    voiced_flat = float(flatness[active].mean()) if active.any() else 1.0
    return {
        "sr": sr, "dur": round(len(x) / sr, 2),
        "active_ratio": float(active.mean()),
        "runs": runs,
        "voiced_flatness": voiced_flat,
        "low_present": float((low > floor - 25).mean()),
        "mean_rms_db": round(float(20 * np.log10(rms.mean() + 1e-9)), 1),
        "mid_floor_db": round(floor, 1),
        "_spec": spec, "_f": f, "t": t,
    }


def verdict_sound(a: dict, expect_dialogue: bool) -> tuple[list[str], list[str]]:
    """Judgement uses the mid-band *active ratio*, calibrated on real renders:

        001 no dialogue  ->  2%      008 with dialogue -> 21%
        002 no dialogue  ->  0%      030 with dialogue -> 50%

    Spectral flatness was tried as a "is it voiced?" test and rejected: on these
    samples it does not separate speech from noise (0.18-0.47 for speech, 0.01
    for a silent shot). It is reported as information only. The spectrogram in
    the review sheet is the real evidence — read it, or listen.
    """
    ok, warn = [], []
    ratio = a["active_ratio"]

    if ratio > BGM_ACTIVE_RATIO:
        warn.append(f"中频活跃占比 {ratio:.0%} ≥ {BGM_ACTIVE_RATIO:.0%}："
                    f"疑似有贯穿全片的背景音乐（要无 BGM 应写 non_diegetic_music: N/A）")
    elif ratio < MIN_ACTIVE_RATIO:
        if expect_dialogue:
            warn.append(f"中频活跃占比仅 {ratio:.1%}，但这一镜预期有台词 —— "
                        f"检查 <d>[Chinese] …</d> 写法，以及台词是否被写进了别的段落")
        else:
            ok.append(f"中频活跃占比 {ratio:.1%}：无台词镜头，符合预期")
    else:
        ok.append(f"检出 {len(a['runs'])} 段中频谐波活动，覆盖 {ratio:.0%} 时长"
                  + ("（与预期台词相符）" if expect_dialogue else "（本镜未预期台词，请抽听确认）"))

    if a["low_present"] > 0.6:
        ok.append(f"低频底噪覆盖 {a['low_present']:.0%} 时长 —— 有持续环境声")
    else:
        warn.append(f"低频底噪只覆盖 {a['low_present']:.0%}，环境声可能太薄")
    return ok, warn


def spec_checks(info: dict, dur: int | None, mp: float | None) -> tuple[list[str], list[str]]:
    ok, bad = [], []
    tbl = {0.4: (864, 480), 0.6: (1056, 608), 0.8: (1216, 672), 1.0: (1376, 768), 1.5: (1664, 928)}
    wh = (info.get("width"), info.get("height"))
    if mp and mp in tbl:
        (ok if wh == tbl[mp] else bad).append(
            f"分辨率 {wh[0]}×{wh[1]}" + (f"（符合 {mp} 档）" if wh == tbl[mp] else f"，期望 {tbl[mp][0]}×{tbl[mp][1]}"))
    if info.get("fps") == 24.0:
        ok.append("帧率 24 fps")
    else:
        bad.append(f"帧率 {info.get('fps')}，期望 24")
    fr = info.get("frames", 0)
    if fr and (fr - 5) % 17 == 0 and fr >= 5:
        ok.append(f"帧数 {fr} = 5+17×{(fr - 5) // 17}")
    elif fr:
        bad.append(f"帧数 {fr} 不是 5+17k")
    if info.get("sample_rate") == 32000 and info.get("channels") == 2:
        ok.append("音频 32 kHz 立体声")
    elif info.get("sample_rate"):
        bad.append(f"音频 {info.get('sample_rate')}Hz/{info.get('channels')}ch，期望 32000Hz/2ch")
    else:
        bad.append("没有音频流")
    if dur and abs(info["duration"] - dur) > 1.0:
        bad.append(f"时长 {info['duration']}s 与分镜表 {dur}s 偏差过大")
    return ok, bad


def build_sheet(video: Path, a: dict, frames: list[float], tmp: Path, dest: Path) -> Path:
    tiles = []
    for t in frames:
        png = tmp / f"f{t}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video),
                        "-frames:v", "1", str(png), "-y"], check=True)
        if png.exists():
            im = Image.open(png).convert("RGB")
            im.thumbnail((430, 430))
            tiles.append((f"{t}s", im))
    if not tiles:
        raise SystemExit("抽帧失败")
    cw = max(i[1].width for i in tiles)
    ch = max(i[1].height for i in tiles)
    cols = min(3, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    spec_h = 240
    sheet = Image.new("RGB", (cw * cols + 8, rows * (ch + 22) + spec_h + 16), (250, 250, 252))
    dr = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(15)
    for k, (tag, im) in enumerate(tiles):
        r, c = divmod(k, cols)
        x, y = c * (cw + 8), r * (ch + 22)
        sheet.paste(im, (x, y))
        dr.text((x + 2, y + ch + 3), tag, fill=(20, 20, 24), font=font)

    S = 20 * np.log10(a["_spec"] + 1e-10)
    keep = a["_f"] <= 5000
    W = cw * cols + 8
    arr = np.clip((S[:, keep].T[::-1] - S.max() + 70) / 70, 0, 1)
    sp = Image.fromarray((arr * 255).astype(np.uint8)).convert("RGB").resize((W, spec_h))
    ds = ImageDraw.Draw(sp)
    for hz in (300, 1000, 2000, 3400):
        y = spec_h - int(hz / 5000 * spec_h)
        ds.line([(0, y), (W, y)], fill=(255, 80, 80))
        ds.text((3, y + 1), f"{hz}Hz", fill=(255, 130, 130))
    for r0, r1 in a["runs"]:
        x0, x1 = int(r0 / a["dur"] * W), int(r1 / a["dur"] * W)
        ds.rectangle([x0, 2, x1, 14], fill=(90, 220, 120))
    ds.text((4, 18), "绿色 = 检出的人声/谐波活动段", fill=(180, 255, 200))
    sheet.paste(sp, (0, rows * (ch + 22) + 6))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?", help="mp4 路径")
    ap.add_argument("--project")
    ap.add_argument("--shot")
    ap.add_argument("--frames", default="1,mid,last", help="抽帧时间点，mid/last 自动算")
    ap.add_argument("--outdir")
    ap.add_argument("--dialogue", choices=["yes", "no", "auto"], default="auto")
    ap.add_argument("--dur", type=int, help="分镜表里的时长（用于比对）")
    ap.add_argument("--megapixels", type=float, help="期望档位，如 0.8")
    args = ap.parse_args()

    if args.video:
        video = Path(args.video).expanduser()
        project_dir = video.parent.parent if video.parent.name == "out" else None
    else:
        if not (args.project and args.shot):
            ap.error("给一个 mp4 路径，或者 --project + --shot")
        project_dir = PROJECTS / args.project
        hits = sorted(glob.glob(str(project_dir / "out" / f"{args.shot}_motion_*.mp4")),
                      key=lambda p: Path(p).stat().st_mtime)
        if not hits:
            raise SystemExit(f"找不到 {args.shot} 的成片")
        video = Path(hits[-1])

    if not video.exists():
        raise SystemExit(f"文件不存在: {video}")

    # 从项目里补 时长 / 档位 / 是否预期有台词
    dur, mp, expect_dialogue = args.dur, args.megapixels, None
    if project_dir and (project_dir / "shots.json").exists():
        plan = json.loads((project_dir / "shots.json").read_text("utf-8"))
        m = next((s for s in plan["shots"] if video.name.startswith(s["id"] + "_")), None)
        if m:
            dur = dur or m.get("dur")
            txt = (m.get("motion") or "")
            expect_dialogue = "<d>" in txt
    if args.dialogue != "auto":
        expect_dialogue = args.dialogue == "yes"
    if expect_dialogue is None:
        expect_dialogue = True

    info = probe(video)
    print(f"=== {video.name}")
    print(f"规格: {info.get('width')}×{info.get('height')}  {info.get('fps')}fps  "
          f"{info.get('frames')} 帧  {info['duration']}s  "
          f"音频 {info.get('sample_rate')}Hz/{info.get('channels')}ch")

    tmp = Path(tempfile.mkdtemp())
    wav = decode_wav(video, tmp)
    a = analyse_audio(wav)
    if "error" in a:
        print("!!", a["error"])
        return 1

    ts = []
    for tok in args.frames.split(","):
        tok = tok.strip()
        if tok == "mid":
            ts.append(round(info["duration"] * 0.5, 2))
        elif tok == "last":
            ts.append(round(max(0.2, info["duration"] - 0.5), 2))
        else:
            ts.append(float(tok))
    outdir = Path(args.outdir).expanduser() if args.outdir else video.parent / "_qa"
    sheet = build_sheet(video, a, ts, tmp, outdir / f"{video.stem}_qa.png")
    shutil.rmtree(tmp, ignore_errors=True)

    ok_s, bad_s = spec_checks(info, dur, mp)
    ok_a, warn_a = verdict_sound(a, expect_dialogue)

    print("\n--- 规格 ---")
    for x in ok_s:   print("  ✓", x)
    for x in bad_s:  print("  ✗", x)
    print(f"\n--- 声音（预期台词：{'是' if expect_dialogue else '否'}）---")
    for x in ok_a:   print("  ✓", x)
    for x in warn_a: print("  ⚠", x)
    print(f"  谐波活动段: {a['runs'] if a['runs'] else '无'}")
    print(f"\n审阅图（人工看身份与重影）: {sheet}")

    bad = bool(bad_s) or bool(warn_a)
    print("\n结论:", "需要处理" if bad else "自动检查通过 —— 仍需人工看帧确认身份与重影")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
