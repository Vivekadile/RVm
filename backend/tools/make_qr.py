"""Generate a printable QR for a machine: python tools/make_qr.py [--ip X] [--machine RVM-01]"""
import argparse
import os
import socket
import sys

import qrcode
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.config import Config  # noqa: E402  (reads .env)

DOCS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs"))


def lan_ip():
    """Best-guess LAN IP of this laptop (no packets are actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def make_qr(url, caption, out_path):
    """Write a white PNG: QR code with a caption and the URL underneath."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=20, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    code = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    big = ImageFont.load_default(size=72)
    small = ImageFont.load_default(size=28)
    w, h = code.size
    canvas = Image.new("RGB", (w, h + 170), "white")
    canvas.paste(code, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((w // 2, h + 20), caption, font=big, fill="black", anchor="mt")
    draw.text((w // 2, h + 115), url, font=small, fill="#555555", anchor="mt")
    canvas.save(out_path)


def main():
    """Parse args and write docs/qr_<machine>.png."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default=None, help="laptop LAN IP (default: auto-detect)")
    parser.add_argument("--port", type=int, default=Config.PORT)
    parser.add_argument("--machine", default=Config.MACHINE_IDS[0])
    args = parser.parse_args()

    url = f"http://{args.ip or lan_ip()}:{args.port}/m/{args.machine}"
    out_path = os.path.join(DOCS_DIR, f"qr_{args.machine}.png")
    make_qr(url, "Scan to recycle", out_path)
    print(f"QR for {url}\n -> {out_path}")


if __name__ == "__main__":
    main()
