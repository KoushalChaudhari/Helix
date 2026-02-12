# helix_anim_frames.py
# Generates 36 perfectly-looping frames (3s @ 12fps) for a DNA-like twist
# + subtle diagonal 3D rotation of your logo, with transparent background.
# Outputs PNG frames and a ZIP.
from PIL import Image
import os, math, zipfile
import numpy as np
import cv2

# ----------------- CONFIG -----------------
INPUT_PATH = "Helix Logo.png"     
OUTPUT_DIR = "frames_36"
ZIP_NAME = "Helix_Animation_Frames_36.zip"

FINAL_SIZE = (768, 768)           # width, height
FPS = 12
TOTAL_FRAMES = 36                 # 3 seconds at 12 fps
# Motion amounts (tweak if you want stronger/weaker motion)
PERSPECTIVE_AMPL = 0.08           # 0.04–0.12 looks good
TWIST_PIXELS = 10                 # horizontal wave amplitude in pixels
Z_ROTATION_DEG = 4                # small in-plane wobble (0–6° is subtle)

# If your input has a white/near-white background, set this to True
REMOVE_WHITE_BG = True
WHITE_THRESH = 245                # 0..255 (higher = keep more whites)
# ------------------------------------------

def load_rgba(path, final_size):
    img = Image.open(path).convert("RGBA")
    img = img.resize(final_size, Image.Resampling.LANCZOS)
    return img

def remove_white_background(img_rgba, white_thresh=245):
    """Convert near-white pixels to transparent while preserving edges."""
    arr = np.array(img_rgba)  # HxWx4
    rgb = arr[:, :, :3].astype(np.uint8)
    a = arr[:, :, 3].astype(np.uint8)

    # Compute "whiteness" as min distance from 255 across channels
    whiteness = 255 - np.min(rgb, axis=2)
    mask = (rgb[...,0] > white_thresh) & (rgb[...,1] > white_thresh) & (rgb[...,2] > white_thresh)

    # Smooth mask edges with a slight blur -> soft alpha
    mask = mask.astype(np.uint8) * 255
    mask = cv2.GaussianBlur(mask, (5,5), 0)

    # Invert to become alpha "keep" map
    keep_alpha = 255 - mask
    # Combine with existing alpha (if any)
    new_alpha = (a.astype(np.int16) * (keep_alpha.astype(np.int16)/255.0)).astype(np.uint8)

    out = np.dstack([rgb, new_alpha]).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")

def warp_perspective_rgba(img_rgba, phase, ampl):
    """Simulate diagonal 3D rotation by a perspective keystone."""
    w, h = img_rgba.size
    dx = int(w * ampl * math.sin(phase))
    dy = int(h * ampl * math.cos(phase))

    src = np.float32([[0,0],[w,0],[w,h],[0,h]])
    dst = np.float32([
        [0+dx, 0+dy],    # top-left
        [w-dx, 0+dy],    # top-right
        [w+dx, h-dy],    # bottom-right
        [0-dx, h-dy]     # bottom-left
    ])

    arr = np.array(img_rgba)  # HxWx4
    # Warp each channel including alpha to preserve transparency
    out = []
    M = cv2.getPerspectiveTransform(src, dst)
    for c in range(4):
        warped = cv2.warpPerspective(arr[:,:,c], M, (w, h),
                                     flags=cv2.INTER_CUBIC,
                                     borderMode=cv2.BORDER_CONSTANT,
                                     borderValue=0)
        out.append(warped)
    out = np.dstack(out).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")

def twist_rgba(img_rgba, phase, amplitude=10):
    """Apply a horizontal sinusoidal displacement varying with y (DNA twist feel)."""
    w, h = img_rgba.size
    arr = np.array(img_rgba)  # HxWx4

    # Build remap grids
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32),
                         np.arange(w, dtype=np.float32),
                         indexing='ij')
    # Horizontal shift depends on vertical position and phase
    shift = amplitude * np.sin(2*math.pi * (yy / h) + phase)
    map_x = (xx + shift).astype(np.float32)
    map_y = yy.astype(np.float32)

    out = []
    for c in range(4):
        remapped = cv2.remap(arr[:,:,c], map_x, map_y,
                             interpolation=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT,
                             borderValue=0)
        out.append(remapped)
    out = np.dstack(out).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")

def rotate_inplane(img_rgba, angle_deg):
    """Small Z-rotation without expanding canvas."""
    return img_rgba.rotate(angle_deg, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(0,0,0,0))

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def main():
    ensure_dir(OUTPUT_DIR)
    base = load_rgba(INPUT_PATH, FINAL_SIZE)

    if REMOVE_WHITE_BG:
        base = remove_white_background(base, WHITE_THRESH)

    for i in range(TOTAL_FRAMES):
        # Phase runs 0..2π (but we don't output a duplicate last frame)
        phase = 2 * math.pi * (i / TOTAL_FRAMES)

        # 1) Perspective "diagonal" tilt
        img = warp_perspective_rgba(base, phase, PERSPECTIVE_AMPL)

        # 2) DNA-like twist
        img = twist_rgba(img, phase, TWIST_PIXELS)

        # 3) Gentle in-plane wobble for extra depth
        angle = Z_ROTATION_DEG * math.sin(phase)
        img = rotate_inplane(img, angle)

        # Save frame (1-indexed naming)
        out_name = f"frame_{i+1:04d}.png"
        img.save(os.path.join(OUTPUT_DIR, out_name), optimize=True)

    # Zip them
    with zipfile.ZipFile(ZIP_NAME, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(TOTAL_FRAMES):
            fn = f"frame_{i+1:04d}.png"
            zf.write(os.path.join(OUTPUT_DIR, fn), arcname=fn)

    print(f"Done! Frames in {OUTPUT_DIR}/ and ZIP: {ZIP_NAME}")
    print(f"Frames: {TOTAL_FRAMES} @ {FPS} fps — seamless loop (no duplicate last frame).")

if __name__ == "__main__":
    main()
