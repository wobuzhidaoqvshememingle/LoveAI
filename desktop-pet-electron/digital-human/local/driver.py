# coding: utf-8
"""
LoveAI local digital-human driver (LivePortrait low-VRAM mode).

Frozen contract (must align byte-for-byte with Electron side):
  WS server: ws://127.0.0.1:9230 (backoff to 9231..9239 if occupied; report actual port in status).
  Incoming (text JSON):
    {"type":"amplitude","value":0.0~1.0}   # mouth-open coefficient, 0=closed, 1=~0.8 lip delta
    {"type":"hi"}                           # renderer online -> driver replies status
    {"type":"quit"}
  Outgoing (text JSON):
    {"type":"hello","proto":1}
    {"type":"status","state":"loading|ready|error","fps":0.0,"gpu":"...","msg":"..."}
    {"type":"frame","fmt":"jpeg|png","data":"<base64>","w":N,"h":N,"ts":ms}

CLI:
  python driver.py --photo <path> [--port 9230] [--chroma] [--fps-max N]
                   [--selftest] [--demo-out <mp4>] [--log <file>]

--selftest: no external audio. Programmatic blink (~every 3-5s, ~150ms closed)
  + sine-scan mouth amplitude. Runs >=60 frames, writes out/demo.mp4, out/preview.jpg
  (closed / half / open triptych), out/selftest.json.
"""

import argparse
import asyncio
import base64
import json
import logging
import math
import os
import os.path as osp
import signal
import sys
import time
import traceback

import cv2
import numpy as np
import torch

# ---- locate LivePortrait code ----
HERE = osp.dirname(osp.abspath(__file__))
LP_ROOT = osp.join(HERE, "LivePortrait")
if LP_ROOT not in sys.path:
    sys.path.insert(0, LP_ROOT)

# LivePortrait expects cv2 threads off
cv2.setNumThreads(0)
cv2.ocl.setUseOpenCL(False)
torch.backends.cudnn.benchmark = True

from src.config.inference_config import InferenceConfig  # noqa: E402
from src.config.crop_config import CropConfig  # noqa: E402
from src.live_portrait_wrapper import LivePortraitWrapper  # noqa: E402
from src.utils.cropper import Cropper  # noqa: E402
from src.utils.camera import get_rotation_matrix  # noqa: E402
from src.utils.crop import prepare_paste_back, paste_back  # noqa: E402
from src.utils.io import load_image_rgb, resize_to_limit  # noqa: E402
from src.utils.retargeting_utils import calc_eye_close_ratio  # noqa: E402


# ---------------- logging ----------------
def setup_logger(log_path):
    logger = logging.getLogger("dh-local")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if log_path:
        os.makedirs(osp.dirname(log_path), exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


# ---------------- driver core ----------------
class DigitalHumanDriver:
    def __init__(self, photo_path, chroma=False, logger=None):
        self.logger = logger or logging.getLogger("dh-local")
        self.chroma = chroma
        self.photo_path = photo_path

        self.inf_cfg = InferenceConfig()
        self.inf_cfg.flag_use_half_precision = True          # fp16
        self.inf_cfg.flag_do_crop = True
        self.inf_cfg.flag_pasteback = True
        self.inf_cfg.flag_stitching = True
        self.inf_cfg.flag_eye_retargeting = True
        self.inf_cfg.flag_lip_retargeting = True
        self.inf_cfg.flag_normalize_lip = True
        self.inf_cfg.flag_relative_motion = True
        self.inf_cfg.flag_do_torch_compile = False          # torch.compile too heavy on 4GB
        self.inf_cfg.driving_multiplier = 1.0
        self.crop_cfg = CropConfig()

        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.wrapper = LivePortraitWrapper(inference_cfg=self.inf_cfg)
        self.cropper = Cropper(crop_cfg=self.crop_cfg)

        # source assets (extracted once)
        self.img_rgb = None
        self.source_lmk = None
        self.img_crop_256 = None
        self.I_s = None
        self.x_s_info = None
        self.x_c_s = None
        self.R_s = None
        self.f_s = None
        self.x_s = None
        self.lip_delta_before = None
        self.mask_ori_float = None
        self.crop_info = None
        self.source_size = None

        # live state
        self.amplitude = 0.0     # 0..1 incoming mouth-open coefficient
        self.eye_close = 0.0     # 0=open, 1=fully closed (blink envelope)
        self.running = True
        self.frames_emitted = 0

    # -- source setup (run once) --
    def setup_source(self):
        self.logger.info(f"Loading source image: {self.photo_path}")
        img_rgb = load_image_rgb(self.photo_path)
        self.source_size = img_rgb.shape  # H,W,3
        img_rgb = resize_to_limit(img_rgb, self.inf_cfg.source_max_dim, self.inf_cfg.source_division)
        self.img_rgb = img_rgb

        crop_info = self.cropper.crop_source_image(img_rgb, self.crop_cfg)
        if crop_info is None:
            raise RuntimeError("No face detected in source image!")
        self.crop_info = crop_info
        self.source_lmk = crop_info["lmk_crop"]
        self.img_crop_256 = crop_info["img_crop_256x256"]

        self.I_s = self.wrapper.prepare_source(self.img_crop_256)
        self.x_s_info = self.wrapper.get_kp_info(self.I_s)
        self.x_c_s = self.x_s_info["kp"]
        self.R_s = get_rotation_matrix(self.x_s_info["pitch"], self.x_s_info["yaw"], self.x_s_info["roll"])
        self.f_s = self.wrapper.extract_feature_3d(self.I_s)
        self.x_s = self.wrapper.transform_keypoint(self.x_s_info)

        # lip normalization: zero out source's natural lip opening
        c_d_lip_0 = [0.0]
        combined_lip_0 = self.wrapper.calc_combined_lip_ratio(c_d_lip_0, self.source_lmk)
        if combined_lip_0[0][0] >= self.inf_cfg.lip_normalize_threshold:
            self.lip_delta_before = self.wrapper.retarget_lip(self.x_s, combined_lip_0)
            self.logger.info(f"Lip normalize: source lip ratio={combined_lip_0[0][0]:.4f}, lip_delta applied.")
        else:
            self.lip_delta_before = None

        # pasteback mask
        self.mask_ori_float = prepare_paste_back(
            self.inf_cfg.mask_crop, crop_info["M_c2o"],
            dsize=(img_rgb.shape[1], img_rgb.shape[0]),
        )
        self.logger.info("Source setup done. Face cropped and keypoints extracted.")

    # -- per-frame render --
    def render_frame(self, amp, eye_close, t_seconds):
        """amp: 0..1 mouth open; eye_close: 0..1 (0 open, 1 closed); t_seconds for idle sway."""
        # lip retargeting: amp 0..1 -> target lip close ratio (0 closed -> ~0.8 open)
        c_d_lip = [float(np.clip(amp, 0.0, 1.0)) * 0.8]
        combined_lip = self.wrapper.calc_combined_lip_ratio(c_d_lip, self.source_lmk)
        lip_delta = self.wrapper.retarget_lip(self.x_s, combined_lip)

        # eye retargeting: natural open value from source, closed -> 0
        c_s_eye_mean = float(calc_eye_close_ratio(self.source_lmk[None])[0, :2].mean())
        eye_target = c_s_eye_mean * (1.0 - float(np.clip(eye_close, 0.0, 1.0)))
        c_d_eye = [[eye_target]]
        combined_eye = self.wrapper.calc_combined_eye_ratio(c_d_eye, self.source_lmk)
        eye_delta = self.wrapper.retarget_eye(self.x_s, combined_eye)

        # compose on source keypoints (relative motion)
        x_d_i_new = self.x_s + eye_delta + lip_delta
        if self.lip_delta_before is not None:
            x_d_i_new = x_d_i_new - self.lip_delta_before

        # idle sway: subtle xy translation + tiny breathing on cheek kps
        sway_x = 0.006 * math.sin(t_seconds * 0.7)
        sway_y = 0.004 * math.sin(t_seconds * 1.1 + 1.3)
        x_d_i_new = x_d_i_new.clone()
        x_d_i_new[:, :, 0] += sway_x
        x_d_i_new[:, :, 1] += sway_y

        # stitching
        x_d_i_new = self.wrapper.stitching(self.x_s, x_d_i_new)

        out = self.wrapper.warp_decode(self.f_s, self.x_s, x_d_i_new)
        I_p_crop = self.wrapper.parse_output(out["out"])[0]  # 256x256 RGB

        # paste back to full frame
        I_p_full = paste_back(I_p_crop, self.crop_info["M_c2o"], self.img_rgb, self.mask_ori_float)
        return I_p_full  # RGB uint8, full size

    # -- chroma key --
    def chroma_key(self, frame_rgb):
        """Green-screen -> RGBA. frame_rgb: RGB uint8. Returns RGBA uint8."""
        hsv = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2HSV)
        # green threshold
        lo = np.array([35, 60, 40])
        hi = np.array([85, 255, 255])
        mask = cv2.inRange(hsv, lo, hi)
        # feather
        mask = cv2.GaussianBlur(mask, (7, 7), 0)
        alpha = mask
        rgba = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2RGBA)
        rgba[:, :, 3] = 255 - alpha  # green -> transparent
        return rgba


# ---------------- blink envelope ----------------
class BlinkController:
    """Emit eye_close 0..1. Blink every ~3-5s. At low fps (~1.2), a 150ms
    blink is shorter than one frame interval, so we hold closed for one frame
    on the scheduled tick (discrete blink, honest to measured fps)."""
    def __init__(self, rng_seed=0):
        self.t = 0.0
        self.next_blink_at = 2.0 + (rng_seed % 100) / 100.0 * 2.0  # 2..4s first blink
        self.frames_closed_remaining = 0

    def update(self, dt):
        self.t += dt
        if self.frames_closed_remaining <= 0 and self.t >= self.next_blink_at:
            self.frames_closed_remaining = 1  # hold closed for ~1 frame (~150ms-800ms wall)
            self.next_blink_at = self.t + 3.0 + (hash(self.t) % 1000) / 1000.0 * 2.0
        if self.frames_closed_remaining > 0:
            self.frames_closed_remaining -= 1
            return 1.0
        return 0.0


# ---------------- selftest ----------------
def run_selftest(driver, out_dir, demo_out, fps_max, logger):
    os.makedirs(out_dir, exist_ok=True)
    N = 75  # >=60 frames
    frames_rgb = []
    timings = []
    blink = BlinkController(rng_seed=42)
    prev = time.perf_counter()

    # capture preview frames at specific amp levels
    preview_idx = {"closed": None, "half": None, "open": None}
    # we'll render 3 dedicated preview frames first
    for label, a in [("closed", 0.0), ("half", 0.45), ("open", 0.9)]:
        fr = driver.render_frame(a, 0.0, 0.0)
        preview_idx[label] = fr

    for i in range(N):
        now = time.perf_counter()
        dt = now - prev
        prev = now
        # sine-wave amplitude: 0..1..0 over ~3 cycles
        amp = 0.5 + 0.5 * math.sin(2 * math.pi * (i / N) * 3.0)
        eye = blink.update(dt)
        t0 = time.perf_counter()
        frame = driver.render_frame(amp, eye, now)
        torch.cuda.synchronize() if torch.cuda.is_available() else None
        dt_frame = time.perf_counter() - t0
        timings.append(dt_frame)
        frames_rgb.append(frame)

        # respect fps_max
        if fps_max and fps_max > 0:
            target = 1.0 / fps_max
            if dt_frame < target:
                time.sleep(target - dt_frame)
        logger.info(f"selftest frame {i+1}/{N} amp={amp:.2f} eye={eye:.2f} render={dt_frame*1000:.0f}ms")

    # stats
    arr = np.array(timings)
    fps_each = 1.0 / np.maximum(arr, 1e-6)
    fps_mean = float(np.mean(fps_each))
    fps_p50 = float(np.percentile(fps_each, 50))
    fps_p95 = float(np.percentile(fps_each, 95))
    vram_mb = 0.0
    if torch.cuda.is_available():
        vram_mb = round(torch.cuda.max_memory_allocated() / 1024 / 1024, 1)

    # write demo.mp4 via imageio
    demo_path = demo_out or osp.join(out_dir, "demo.mp4")
    import imageio
    writer = imageio.get_writer(demo_path, fps=min(25, max(8, int(fps_mean))), codec="libx264",
                                quality=5, macro_block_size=None)
    for fr in frames_rgb:
        writer.append_data(fr)
    writer.close()

    # preview.jpg: triptych closed|half|open
    h, w = preview_idx["closed"].shape[:2]
    target_w = 512
    sc = target_w / w
    target_h = int(h * sc)
    strip = np.concatenate([
        cv2.resize(preview_idx["closed"], (target_w, target_h)),
        cv2.resize(preview_idx["half"], (target_w, target_h)),
        cv2.resize(preview_idx["open"], (target_w, target_h)),
    ], axis=1)
    preview_path = osp.join(out_dir, "preview.jpg")
    cv2.imwrite(preview_path, cv2.cvtColor(strip, cv2.COLOR_RGB2BGR))

    selftest = {
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "fps_mean": round(fps_mean, 2),
        "fps_p50": round(fps_p50, 2),
        "fps_p95": round(fps_p95, 2),
        "vram_mb": vram_mb,
        "total_frames": N,
        "backend": f"torch {torch.__version__} {'fp16' if driver.inf_cfg.flag_use_half_precision else 'fp32'}",
        "source_image_size": list(driver.source_size),
        "input_size": list(driver.inf_cfg.input_shape),
        "crop_size": driver.crop_cfg.dsize,
        "frame_render_ms_mean": round(float(np.mean(arr)) * 1000, 1),
        "demo_mp4": demo_path,
        "preview_jpg": preview_path,
    }
    with open(osp.join(out_dir, "selftest.json"), "w", encoding="utf-8") as fh:
        json.dump(selftest, fh, indent=2, ensure_ascii=False)
    logger.info("SELFTEST RESULT: " + json.dumps(selftest, indent=2))
    return selftest


# ---------------- WS live mode ----------------
async def ws_server(driver, host, port_start, fps_max, logger):
    import websockets
    connected = set()
    state_holder = {"port": None, "status_text": ""}

    async def on_connect(websocket):
        connected.add(websocket)
        try:
            await websocket.send(json.dumps({"type": "hello", "proto": 1}))
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("type")
                if t == "amplitude":
                    driver.amplitude = float(np.clip(msg.get("value", 0.0), 0.0, 1.0))
                elif t == "hi":
                    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
                    await websocket.send(json.dumps({
                        "type": "status", "state": "ready",
                        "fps": round(driver.last_fps, 2),
                        "gpu": gpu, "msg": f"listening on {state_holder['port']}",
                    }))
                elif t == "quit":
                    driver.running = False
                    break
        finally:
            connected.discard(websocket)

    actual_port = None
    for p in range(port_start, port_start + 10):
        try:
            server = await websockets.serve(on_connect, host, p, max_size=None)
            actual_port = p
            break
        except OSError:
            continue
    if actual_port is None:
        raise RuntimeError("No free WS port in 9230..9239")
    state_holder["port"] = actual_port
    logger.info(f"WS server listening on ws://{host}:{actual_port}")
    print(f"WS_PORT={actual_port}", flush=True)

    # render loop
    driver.last_fps = 0.0
    blink = BlinkController(rng_seed=7)
    prev = time.perf_counter()
    fps_ema = 0.0
    while driver.running:
        loop_now = time.perf_counter()
        dt = loop_now - prev
        prev = loop_now
        eye = blink.update(dt)
        t0 = time.perf_counter()
        # offload blocking GPU render to a thread so the event loop stays
        # free to handle WS handshakes / incoming messages.
        frame = await asyncio.to_thread(driver.render_frame, driver.amplitude, eye, loop_now)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        dt_frame = time.perf_counter() - t0
        inst_fps = 1.0 / max(dt_frame, 1e-6)
        fps_ema = 0.9 * fps_ema + 0.1 * inst_fps if fps_ema > 0 else inst_fps
        driver.last_fps = fps_ema
        driver.frames_emitted += 1

        # encode
        if driver.chroma:
            rgba = driver.chroma_key(frame)
            ok, buf = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
            fmt = "png"
        else:
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
            fmt = "jpeg"
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        h, w = frame.shape[:2]
        payload = json.dumps({
            "type": "frame", "fmt": fmt, "data": b64, "w": int(w), "h": int(h),
            "ts": int(time.time() * 1000),
        })
        if connected:
            await asyncio.gather(*[ws.send(payload) for ws in list(connected)], return_exceptions=True)

        if fps_max and fps_max > 0:
            target = 1.0 / fps_max
            if dt_frame < target:
                await asyncio.sleep(target - dt_frame)

    server.close()
    await server.wait_closed()


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", required=True)
    ap.add_argument("--port", type=int, default=9230)
    ap.add_argument("--chroma", action="store_true")
    ap.add_argument("--fps-max", type=int, default=0)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo-out", default=None)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    logger = setup_logger(args.log)
    logger.info(f"=== LoveAI local digital-human driver starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    logger.info(f"photo={args.photo} chroma={args.chroma} selftest={args.selftest} fps_max={args.fps_max}")

    if not osp.exists(args.photo):
        logger.error(f"photo not found: {args.photo}")
        sys.exit(2)

    try:
        driver = DigitalHumanDriver(args.photo, chroma=args.chroma, logger=logger)
        driver.setup_source()
    except Exception as e:
        logger.error(f"INIT FAILED: {e}")
        logger.error(traceback.format_exc())
        sys.exit(3)

    out_dir = osp.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)

    if args.selftest:
        try:
            selftest = run_selftest(driver, out_dir, args.demo_out, args.fps_max, logger)
            logger.info(f"selftest done. fps_mean={selftest['fps_mean']} vram_mb={selftest['vram_mb']}")
        except Exception as e:
            logger.error(f"SELFTEST FAILED: {e}")
            logger.error(traceback.format_exc())
            sys.exit(4)
    else:
        try:
            asyncio.run(ws_server(driver, "127.0.0.1", args.port, args.fps_max, logger))
        except KeyboardInterrupt:
            pass
        except Exception as e:
            logger.error(f"WS SERVER FAILED: {e}")
            logger.error(traceback.format_exc())
            sys.exit(5)

    logger.info(f"=== driver exiting at {time.strftime('%Y-%m-%d %H:%M:%S')} ===")


if __name__ == "__main__":
    main()
