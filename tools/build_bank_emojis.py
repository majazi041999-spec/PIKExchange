"""Development-only, deterministic emoji packaging from bank vectors and approved PIK art.

Requires pillow and imageio-ffmpeg. Run render_bank_vectors.cjs first (requires sharp).
Bank logos retain their source colors and proportions. Motion is one smooth breath.
"""
import math
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "assets/banks"
FRAMES = 48
FPS = 20


def prepare_bank(image):
    alpha = image.getchannel("A")
    white = Image.new("RGBA", image.size, "white")
    white.putalpha(alpha.filter(ImageFilter.MaxFilter(11)))
    shadow = Image.new("RGBA", image.size, "black")
    shadow.putalpha(alpha.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(5)).point(lambda a: int(a * .45)))
    return Image.alpha_composite(Image.alpha_composite(shadow, white), image)


def frame_at(image, index):
    scale = .97 + .03 * (.5 - .5 * math.cos(2 * math.pi * index / FRAMES))
    side = round(400 * scale)
    resized = image.resize((side, side), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (400, 400))
    canvas.alpha_composite(resized, ((400 - side) // 2, (400 - side) // 2))
    return canvas.resize((100, 100), Image.Resampling.LANCZOS)


def encode(image, path):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "rawvideo", "-pixel_format", "rgba",
               "-video_size", "100x100", "-framerate", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libvpx-vp9",
               "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "24", "-deadline", "good", "-cpu-used", "2", str(path)]
    result = subprocess.run(command, input=b"".join(frame_at(image, frame).tobytes() for frame in range(FRAMES)), capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    assert path.stat().st_size < 256 * 1024
    # Explicit libvpx decoder is needed to preserve VP9's alpha stream.
    decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-c:v", "libvpx-vp9", "-i", str(path),
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"], capture_output=True, check=True).stdout
    assert len(decoded) == 100 * 100 * 4
    assert decoded[3] == 0, "Outer corner must remain transparent"


def main():
    output = FOLDER / "emoji"
    output.mkdir(exist_ok=True)
    pictures = {}
    for path in sorted((FOLDER / "raster").glob("*.png")):
        image = prepare_bank(Image.open(path).convert("RGBA"))
        pictures[path.stem] = image
    pik = Image.open(FOLDER / "source/pik.png").convert("RGBA")
    # The PIK reference intentionally retains its black background, within transparent corners.
    pik.thumbnail((480, 480), Image.Resampling.LANCZOS)
    branded = Image.new("RGBA", (512, 512))
    branded.alpha_composite(pik, ((512 - pik.width) // 2, (512 - pik.height) // 2))
    pictures["pik"] = branded
    for bank_id, image in pictures.items():
        frame_at(image, 0).save(output / f"{bank_id}.png")
        encode(image, output / f"{bank_id}.webm")
        print(bank_id, (output / f"{bank_id}.webm").stat().st_size, flush=True)
    # A contact sheet checks both chat themes and actual chat-size readability.
    cols, cellw, cellh = 5, 200, 160
    rows = math.ceil(len(pictures) / cols)
    sheet = Image.new("RGB", (cols * cellw, rows * cellh), "#e9edf2")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 14)
    for i, (bank_id, image) in enumerate(pictures.items()):
        x, y = (i % cols) * cellw, (i // cols) * cellh
        draw.rectangle((x + 4, y + 4, x + 98, y + 126), fill="white")
        draw.rectangle((x + 100, y + 4, x + 194, y + 126), fill="#17212b")
        for offset in (8, 104):
            tile = frame_at(image, 0).resize((88, 88), Image.Resampling.LANCZOS)
            sheet.paste(tile, (x + offset, y + 8), tile)
            tiny = frame_at(image, 0).resize((22, 22), Image.Resampling.LANCZOS)
            sheet.paste(tiny, (x + offset + 32, y + 98), tiny)
        draw.text((x + 8, y + 134), bank_id, font=font, fill="#18212b")
    sheet.save(FOLDER / "preview.png")
    # Inline animated preview of a small sample, including the brand mark.
    sample = [pictures[k] for k in ("melli", "mellat", "sber", "tbank", "pik")]
    preview = []
    for frame in range(FRAMES):
        canvas = Image.new("RGBA", (500, 100), "#17212b")
        for i, picture in enumerate(sample):
            canvas.alpha_composite(frame_at(picture, frame), (i * 100, 0))
        preview.append(canvas.convert("RGB"))
    preview[0].save(FOLDER / "motion-preview.gif", save_all=True, append_images=preview[1:], duration=50, loop=0)


if __name__ == "__main__":
    main()
