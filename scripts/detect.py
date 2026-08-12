"""API に検査画像を POST し、検出結果を描画して保存する動作確認スクリプト

Usage:
    python detect.py <API_URL> <image_path> [objects]

Example:
    python detect.py https://xxxx.execute-api.ap-northeast-1.amazonaws.com/prod/detect \
        images/sample.jpg "scratch,dent,rust"
"""

import base64
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

COLORS = {"scratch": "#ef4444", "dent": "#f59e0b", "rust": "#8b5cf6"}


def main():
    api_url = sys.argv[1]
    image_path = Path(sys.argv[2])
    objects = sys.argv[3].split(",") if len(sys.argv) > 3 else ["scratch", "dent", "rust"]

    image_bytes = image_path.read_bytes()
    image_format = "png" if image_path.suffix.lower() == ".png" else "jpeg"

    payload = json.dumps(
        {
            "image": base64.b64encode(image_bytes).decode(),
            "format": image_format,
            "objects": [o.strip() for o in objects],
        }
    ).encode()

    req = Request(api_url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req) as res:
        data = json.loads(res.read())

    print(json.dumps(data, indent=2))

    image = Image.open(image_path).convert("RGB")
    w, h = image.size
    draw = ImageDraw.Draw(image)
    lw = max(2, (w + h) // 400)
    font = ImageFont.load_default(size=lw * 6)

    labels = data["detections"]["labels"]
    boxes = data["detections"]["boxes"]
    for label, box in zip(labels, boxes):
        # 0-1000 の正規化座標をピクセルに変換
        x1, y1 = box[0] / 1000 * w, box[1] / 1000 * h
        x2, y2 = box[2] / 1000 * w, box[3] / 1000 * h
        color = COLORS.get(label, "#22c55e")
        draw.rectangle([x1, y1, x2, y2], outline=color, width=lw)
        tb = draw.textbbox((x1, max(0, y1 - lw * 8)), label, font=font)
        draw.rectangle([tb[0] - lw, tb[1] - lw, tb[2] + lw, tb[3] + lw], fill=color)
        draw.text((tb[0], tb[1]), label, fill="white", font=font)

    out_path = image_path.with_name(f"result_{image_path.stem}.png")
    image.save(out_path)
    print(f"saved: {out_path} ({len(labels)} detections)")


if __name__ == "__main__":
    main()
