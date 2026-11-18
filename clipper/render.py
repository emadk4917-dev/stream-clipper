"""Cut a moment out of the VOD and turn it into a finished vertical Short (9:16 + captions)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

W, H = 1080, 1920


# ---------------------------------------------------------------- alignment
def _pcm(path: Path, start: float, dur: float, sr: int = 4000) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, start):.2f}", "-t", f"{dur:.2f}", "-i", str(path),
                          "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32)


def find_offset(section: Path, audio: Path, guess: float, audio_offset: float = 0.0, sr: int = 4000) -> float:
    """Where (in VOD seconds) the downloaded section really starts.

    Downloads snap to keyframes/segments, so we line up the section's audio against the
    full-stream audio we already have. Accurate to ~1/4000 s, which keeps captions in sync.
    """
    probe = _pcm(section, 1.0, 12, sr)
    search_from = guess - 25
    ref = _pcm(audio, search_from - audio_offset, 60, sr)
    if len(probe) < sr * 3 or len(ref) < len(probe):
        return guess
    n = 1 << int(np.ceil(np.log2(len(ref) + len(probe))))
    corr = np.fft.irfft(np.fft.rfft(ref, n) * np.conj(np.fft.rfft(probe, n)), n)[: len(ref) - len(probe)]
    lag = int(np.argmax(corr))
    return search_from + lag / sr - 1.0


# ---------------------------------------------------------------- layout detection
def detect_layout(video: Path, t0: float, t1: float) -> dict:
    """Look for the streamer's face to decide how to frame the vertical video.

    - "face":  streamer fills the frame (Just Chatting / IRL) -> crop around their face
    - "split": small facecam over gameplay -> facecam on top, gameplay below
    - "blur":  no reliable face -> full frame centred on a blurred background
    """
    import cv2
    cap = cv2.VideoCapture(str(video))
    fw = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920
    fh = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = []
    samples = 12
    gx_acc = gy_acc = None
    for k in range(samples):
        cap.set(cv2.CAP_PROP_POS_MSEC, 1000 * (t0 + (t1 - t0) * (k + 0.5) / samples))
        ok, frame = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # edges that stay put across frames = overlay borders (webcam frame), not moving content
        gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
        gx_acc = gx if gx_acc is None else np.minimum(gx_acc, gx)
        gy_acc = gy if gy_acc is None else np.minimum(gy_acc, gy)
        found = det.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(int(fw * 0.016), int(fw * 0.016)))
        if len(found):
            x, y, w, h = max(found, key=lambda f: f[2] * f[3])
            faces.append((x / fw, y / fh, w / fw, h / fh))
    cap.release()
    base = {"src_w": int(fw), "src_h": int(fh)}
    if len(faces) < samples * 0.4:
        return {**base, "layout": "blur"}
    f = np.median(np.array(faces), axis=0)
    centres = np.array([(a + c / 2, b + d / 2) for a, b, c, d in faces])
    if centres.std(axis=0).max() > 0.08:  # face jumping around = probably false detections
        return {**base, "layout": "blur"}
    x, y, w, h = f
    if w > 0.11:
        return {**base, "layout": "face", "face_cx": float(x + w / 2)}
    cam = _find_cam_box(gx_acc, gy_acc, x * fw, y * fh, w * fw, h * fh)
    return {**base, "layout": "split", "cam": [round(cam[0] / fw, 4), round(cam[1] / fh, 4),
                                              round(cam[2] / fw, 4), round(cam[3] / fh, 4)]}


def _find_cam_box(gx, gy, fx, fy, fwid, fhei):
    """Find the webcam overlay's borders around a detected face (pixel coords).

    gx/gy are per-pixel minimum edge strengths across sampled frames, so only edges that
    never move (overlay borders) survive. Falls back to a 4:3 box around the face.
    """
    H_, W_ = gx.shape

    def strongest(profile, lo, hi, fallback):
        lo, hi = int(max(0, lo)), int(min(len(profile) - 1, hi))
        if hi - lo < 3:
            return fallback
        seg = profile[lo:hi]
        i = int(np.argmax(seg))
        return lo + i if seg[i] > 4 * (np.median(seg) + 1) else fallback

    rows = slice(int(max(0, fy - fhei)), int(min(H_, fy + 2 * fhei)))
    cols = slice(int(max(0, fx - fwid)), int(min(W_, fx + 2 * fwid)))
    colp = gx[rows].mean(axis=0)
    rowp = gy[:, cols].mean(axis=1)
    dw, dh = fwid * 1.6, fhei * 1.4  # default margins if no border found
    left = strongest(colp, fx - 3 * fwid, fx - 0.15 * fwid, fx - dw)
    right = strongest(colp, fx + 1.15 * fwid, fx + 4 * fwid, fx + fwid + dw)
    top = strongest(rowp, fy - 3 * fhei, fy - 0.15 * fhei, fy - dh)
    bottom = strongest(rowp, fy + 1.3 * fhei, fy + 4 * fhei, fy + fhei + dh * 1.4)
    left, top = max(0, left + 2), max(0, top + 2)
    right, bottom = min(W_, right - 1), min(H_, bottom - 1)
    return [left, top, max(2, right - left), max(2, bottom - top)]


def _even(v: float) -> int:
    return int(v) // 2 * 2


def video_filter(lay: dict, ass_path: Path | None) -> str:
    sw, sh = lay["src_w"], lay["src_h"]
    kind = lay["layout"]
    if kind == "face":
        cw = _even(min(sw, sh * 9 / 16))
        cx = lay["face_cx"] * sw
        x = _even(min(max(0, cx - cw / 2), sw - cw))
        v = f"[0:v]crop={cw}:{_even(sh)}:{x}:0,scale={W}:{H},setsar=1[v0]"
    elif kind == "split":
        top_h = int(H * 0.40)
        bot_h = H - top_h
        cx, cy, cw, ch = lay["cam"]
        cam = f"crop={_even(cw * sw)}:{_even(ch * sh)}:{_even(cx * sw)}:{_even(cy * sh)}"
        gw = _even(min(sw, sh * W / bot_h))
        gx = _even((sw - gw) / 2)
        v = (f"[0:v]split=2[c][g];"
             f"[c]{cam},scale={W}:{top_h}:force_original_aspect_ratio=increase,crop={W}:{top_h}[top];"
             f"[g]crop={gw}:{_even(sh)}:{gx}:0,scale={W}:{bot_h}:force_original_aspect_ratio=increase,crop={W}:{bot_h}[bot];"
             f"[top][bot]vstack=inputs=2,setsar=1[v0]")
    else:  # blur
        v = (f"[0:v]split=2[a][b];"
             f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3,eq=brightness=-0.08[bg];"
             f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-80,setsar=1[v0]")
    if ass_path:
        esc = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        v += f";[v0]ass='{esc}'[v]"
    else:
        v += ";[v0]null[v]"
    return v


# ---------------------------------------------------------------- captions
def _ass_time(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _clean(w: str) -> str:
    w = w.replace("{", "").replace("}", "").replace("\\", "")
    return w.strip(",.;:\"“”").upper()


def build_ass(words: list[dict], clip_start: float, clip_end: float, hook: str, layout: str,
              out: Path, font: str = "Inter") -> Path:
    cap_y = int(H * 0.40) if layout == "split" else int(H * 0.68)
    hook_y = 150 if layout == "split" else 230
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1
Style: Hook,{font},62,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,8,70,70,{hook_y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    dur = clip_end - clip_start
    if hook:
        ev.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(min(3.5, dur))},Hook,,0,0,0,,"
                  f"{{\\fad(120,200)}}{hook.replace('{', '').replace('}', '')}")
    ws = [w for w in words if w["e"] > clip_start and w["s"] < clip_end and _clean(w["w"])]
    groups, cur = [], []
    for w in ws:
        if cur and (len(cur) >= 3 or w["s"] - cur[-1]["e"] > 0.5 or w["e"] - cur[0]["s"] > 1.5):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    for gi, g in enumerate(groups):
        g_end = g[-1]["e"] + 0.15
        if gi + 1 < len(groups):
            g_end = min(g_end + 0.3, groups[gi + 1][0]["s"])
        for wi, w in enumerate(g):
            s = w["s"]
            e = g[wi + 1]["s"] if wi + 1 < len(g) else g_end
            parts = []
            for wj, x in enumerate(g):
                t = _clean(x["w"])
                parts.append(f"{{\\c&H0000E6FF&}}{t}{{\\c&H00FFFFFF&}}" if wj == wi else t)
            pop = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" if wi == 0 else ""
            ev.append(f"Dialogue: 0,{_ass_time(s - clip_start)},{_ass_time(e - clip_start)},Cap,,0,0,0,,"
                      f"{{\\pos({W // 2},{cap_y})}}{pop}{' '.join(parts)}")
    out.write_text(header + "\n".join(ev) + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------- final encode
def render_clip(section: Path, local_start: float, local_end: float, words_abs: list[dict], abs_start: float,
                hook: str, out_mp4: Path, layout_override: str | None = None, cam: list[float] | None = None,
                captions: bool = True, known_cam: list[float] | None = None) -> dict:
    lay = detect_layout(section, local_start, local_end)
    if lay["layout"] == "blur" and known_cam and layout_override in (None, "", "auto"):
        # same stream, same overlay: reuse the facecam found in another clip
        lay["layout"], lay["cam"] = "split", known_cam
    if layout_override and layout_override != "auto":
        lay["layout"] = layout_override
        if layout_override == "face" and "face_cx" not in lay:
            lay["face_cx"] = 0.5
        if layout_override == "split" and "cam" not in lay and not cam:
            lay["cam"] = [0.0, 0.0, 0.25, 0.25]
    if cam:
        lay["layout"], lay["cam"] = "split", cam
    ass = None
    if captions or hook:
        shifted = [{**w, "s": w["s"] - abs_start + local_start, "e": w["e"] - abs_start + local_start}
                   for w in (words_abs if captions else [])]
        ass = build_ass(shifted, local_start, local_end, hook, lay["layout"], out_mp4.with_suffix(".ass"))
    vf = video_filter(lay, ass)
    # setpts/asetpts restart the clock at 0 so the subtitle times line up
    vf = vf.replace("[0:v]", "[0:v]setpts=PTS-STARTPTS,", 1)
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{local_start:.3f}", "-to", f"{local_end:.3f}", "-i", str(section),
           "-filter_complex", vf + ";[0:a]asetpts=PTS-STARTPTS,loudnorm=I=-14:TP=-1.5:LRA=11[a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
           "-movflags", "+faststart", str(out_mp4)]
    subprocess.run(cmd, check=True)
    return lay
