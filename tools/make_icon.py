"""Draw the Kahroba icon (an amber gem) as app/renderer/icon.png (512 px) and icon.ico."""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

out = Path(sys.argv[1] if len(sys.argv) > 1 else "app/renderer")
S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
# rounded dark tile
tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(tile).rounded_rectangle([16, 16, S - 16, S - 16], radius=110, fill=(13, 17, 23, 255))
img.alpha_composite(tile)
# glow
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse([120, 120, S - 120, S - 120], fill=(244, 168, 58, 150))
img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40)))
# gem: rotated rounded square with a radial amber gradient
gem = Image.new("RGBA", (S, S), (0, 0, 0, 0))
px = gem.load()
c = S / 2
for y in range(S):
    for x in range(S):
        u, v = (x - c) / 150, (y - c) / 150
        a, b = (u + v) / math.sqrt(2), (v - u) / math.sqrt(2)  # rotate 45 deg
        if abs(a) ** 6 + abs(b) ** 6 <= 1:
            d = math.hypot(u + 0.35, v + 0.45) / 1.6
            t = min(max(d, 0), 1)
            r = int(255 * (1 - t) + 154 * t)
            g = int(226 * (1 - t) ** 1.2 + 90 * t)
            bl = int(166 * (1 - t) ** 2 + 12 * t)
            px[x, y] = (r, g, bl, 255)
img.alpha_composite(gem)
# highlight
hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(hl).ellipse([200, 165, 270, 215], fill=(255, 245, 220, 150))
img.alpha_composite(hl.filter(ImageFilter.GaussianBlur(10)))
out.mkdir(parents=True, exist_ok=True)
img.save(out / "icon.png")
img.save(out / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon written to", out)
