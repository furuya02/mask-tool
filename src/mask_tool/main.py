#!/usr/bin/env python3
"""
mask-tool - 連番PNG画像に写り込んだ認証情報を検出してマスクするツール

カレントディレクトリにある 001.png のような連番PNG（数字のみのファイル名）を対象に、
Amazon Bedrock の Amazon Nova 2 Lite で認証情報を検出し、該当領域をマスクして
元画像を上書きする。

処理フロー（画像 1 枚あたり）:
  1. 12 桁スキャン   テキストを文字起こしして、正規表現で 12 桁の数字を含む行を拾う
                     （判定をモデルに委ねないため確実。該当行はまるごとマスクする）
                     折り返しで 12 桁が 2 行に分断される場合に備え、隣接行を連結した
                     文字列も判定し、境界をまたぐ並びがあれば両方の行をマスクする
  2. スクリーニング  認証情報があるかだけを判定。無ければ検出を省略（コスト削減）
  3. 検出            認証情報のバウンディングボックスを [0,1000] 正規化座標で取得
  4. マスク          Pillow で塗り潰し（座標を実寸へ変換し、パディングを付与）
  5. 再検証          マスク済み画像を再度 Nova に渡し、残存があれば要確認として報告

バックアップについて:
    元画像は上書きされるため、処理前に必ずバックアップディレクトリへ退避する。
    バックアップ先は bak/ で、既に存在する場合は bak2/, bak3/ ... と採番するため、
    同じディレクトリで複数回実行しても過去の原本が失われることはない。

画像は縮小せずに送信する:
    Nova 2 Lite の画像入力は解像度によらず一律 230 トークンで課金されるため、
    縮小してもコストは下がらず、小さい文字の読み取り精度を落とすだけになる。
    送信サイズの上限を超える画像だけ、収まるところまで縮小する。

保存時の縮小:
    保存する画像は、既定で横幅 900px 以内になるよう縦横比を保って縮小する（--max-width）。
    検出・マスク・再検証はすべて原寸で行い、縮小は最後に適用する。
    マスク対象が無かった画像も同じように縮小して書き戻す。
"""

import argparse
import io
import json
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple, TypeGuard

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError
from PIL import Image, ImageDraw, ImageFilter

from mask_tool import __version__

# Nova 2 Lite は ON_DEMAND 非対応のため推論プロファイル経由で呼び出す。
# jp. プレフィックスは ap-northeast-1 / ap-northeast-3 にのみルーティングされるため、
# 機密画像の処理を日本国内リージョンに閉じられる。
DEFAULT_MODEL_ID = "jp.amazon.nova-2-lite-v1:0"
DEFAULT_REGION = "ap-northeast-1"

# 東京リージョンの Nova 2 Lite 単価（USD / 1M トークン）。
# aws pricing get-products --service-code AmazonBedrock で取得した実値。
PRICE_IN_PER_1M = 0.396
PRICE_OUT_PER_1M = 3.311

# 001.png のように数字のみで構成されたPNGを対象とする
IMAGE_PATTERN = re.compile(r"^\d+\.png$", re.IGNORECASE)

# バックアップ先は bak → bak2 → bak3 ... と採番する
BACKUP_BASE_NAME = "bak"

# 検出枠は正解に対してずれるため、パディングを付けて塗り潰す。
# パディングは「検出枠の高さ」を基準にした比率で指定する。画像の解像度ではなく
# 文字サイズに追従するため、高解像度の画像でも比率を変えずに済む。
DEFAULT_PAD_X = 1.5
DEFAULT_PAD_Y = 0.4

# 塗り潰しの方式。pixelate はモザイク、blur はガウスぼかし、black は単色での置き換え。
# 認証情報を確実に消したい場合は black を使うこと。
DEFAULT_STYLE = "pixelate"
BLUR_RADIUS_RATIO = 0.5   # ぼかし半径。検出枠の高さに対する比率
PIXELATE_BLOCKS = 12      # モザイクの粗さ（領域をおよそ何ブロックに分割するか）

# 応答の上限トークン数。検出・再検証は座標付き JSON を返すため、
# 認証情報が多く写り込んだ画像ではそれなりの長さになる。
# 上限に達すると JSON が途中で終わり解析できないので、余裕を持たせている。
DEFAULT_MAX_TOKENS = 4000

# 文字起こしの実行回数。同じ画像でも拾える行が毎回変わるため、
# 複数回まわして重ね合わせる。取りこぼしを減らすことを優先した既定値。
DEFAULT_OCR_PASSES = 3

# Converse API に渡せる画像サイズの目安。これを超える画像だけ縮小する。
MAX_IMAGE_BYTES = 4_000_000

# 出力画像の最大幅。これより広い画像は縦横比を保ったまま縮小して保存する。
# 検出とマスクは原寸のまま行い、縮小は最後に適用する（精度を落とさないため）。
# マスク対象が無かった画像も同じように縮小する。
DEFAULT_MAX_WIDTH = 900

# 12 桁の数字。AWS コンソールでは 1234-5678-9012 のように区切って表示されることが
# あるため、ハイフンや空白で区切られた形も拾う。
DIGIT_RUN = re.compile(r"\d{12}|\d{4}[\s-]\d{4}[\s-]\d{4}")

# 折り返しとみなす行間の上限（直前の行の高さに対する倍率）。
# ARN のような長い値は折り返され、12 桁が行末と次行先頭に分断されることがある。
WRAP_MAX_GAP_RATIO = 1.5

# 認証・権限の問題は全画像で再現するため、検出した時点で処理全体を中断する
FATAL_ERROR_CODES = {
    "AccessDeniedException",
    "ExpiredTokenException",
    "InvalidSignatureException",
    "ResourceNotFoundException",
    "UnrecognizedClientException",
}

CATEGORIES = """- aws_account_id: a run of exactly 12 consecutive digits. It may stand alone, or
  sit inside a longer identifier such as arn:aws:iam::123456789012:user/foo -
  report those as well. Cover ONLY the 12 digits: the characters before and after
  them must stay readable. "text" must be the 12 digits alone.
- aws_access_key_id: an access key ID such as AKIA...
- aws_secret_access_key: a long secret key string
- api_token: an API key, bearer token, or session token
- password: a password value
- email: an email address
- phone: a phone number"""

SCREEN_PROMPT = """Look at this screenshot and decide whether it contains any
credential or sensitive identifier rendered as visible text.

Target categories:
{categories}

The screenshot may be in Japanese or English.
Respond with JSON only: {{"has_credentials": true}} or {{"has_credentials": false}}"""

DETECT_PROMPT = """You are a security screening tool. Find every credential or
sensitive identifier that is visibly rendered as text in this screenshot.

Target categories:
{categories}

Rules:
- Report only the VALUE, never the label or field name next to it.
- The screenshot may be in Japanese or English.
- Return a bounding box for each finding using the normalized [0, 1000] coordinate
  space, as [x1, y1, x2, y2] where (x1, y1) is the top-left corner.
- The box must tightly enclose the rendered text of the value.

Respond with JSON only, no markdown fence, in this exact shape:
{{"findings": [{{"category": "...", "text": "...", "bbox": [x1, y1, x2, y2]}}]}}
If there is nothing to report, return {{"findings": []}}."""

# 12 桁の数字を確実に拾うための OCR パス。
# 「12 桁かどうか」の判定はモデルに任せず、文字起こしした文字列に対して
# コード側の正規表現で行う。モデルは長い識別子を 1 つの塊として扱い、
# その中の数字の並びを取り出せないことがあるため。
OCR_PROMPT = """Transcribe the text in this screenshot, line by line.

Rules:
- Copy each line exactly as it appears, including punctuation and long identifiers.
- Do not summarise, translate, or omit anything.
- Give each line a bounding box in the normalized [0, 1000] coordinate space,
  as [x1, y1, x2, y2] where (x1, y1) is the top-left corner.

Respond with JSON only, no markdown fence. Every element must be an object that
has BOTH "text" and "bbox" - never a bare string:
{"lines": [
  {"text": "first line as it appears", "bbox": [100, 200, 400, 230]},
  {"text": "second line as it appears", "bbox": [100, 240, 350, 270]}
]}"""

# 文字起こしが座標なしで返ってきたときに、位置だけを聞き直すためのプロンプト。
LOCATE_PROMPT = """Find where each of the following strings appears in this
screenshot, and give its bounding box.

Strings to locate:
{targets}

Rules:
- Treat each string as an exact target. Locate the whole string.
- Give the bounding box in the normalized [0, 1000] coordinate space,
  as [x1, y1, x2, y2] where (x1, y1) is the top-left corner.
- If a string appears more than once, report each occurrence.

Respond with JSON only, no markdown fence. Every element must be an object:
{{"found": [{{"text": "...", "bbox": [x1, y1, x2, y2]}}]}}"""

VERIFY_PROMPT = """This screenshot has already been redacted: sensitive values were
hidden behind pixelated, blurred, or solid boxes. Find any value the redaction MISSED.

Target categories:
{categories}

Rules:
- Report a value only if you can actually read its characters in the image.
- Give the bounding box of each finding in the normalized [0, 1000] coordinate
  space, as [x1, y1, x2, y2].
- If every sensitive value is covered, return an empty list.

Respond with JSON only, no markdown fence:
{{"remaining": [{{"category": "...", "text": "...", "bbox": [x1, y1, x2, y2]}}]}}"""


PixelBox = tuple[float, float, float, float]


class Result(NamedTuple):
    """画像 1 枚の処理結果。"""

    status: str          # "clean" | "masked" | "review"
    findings: int        # マスクした領域の数
    digits: int          # 12 桁スキャンで拾った数
    notes: list[str]     # 残存の疑い・エラー理由など、末尾で報告する内容


class Usage:
    """トークン使用量を積算してコストを概算する。"""

    def __init__(self) -> None:
        self.input = 0
        self.output = 0
        self.calls = 0

    def add(self, usage: dict[str, int]) -> None:
        self.input += usage.get("inputTokens", 0)
        self.output += usage.get("outputTokens", 0)
        self.calls += 1

    def usd(self) -> float:
        return (self.input * PRICE_IN_PER_1M + self.output * PRICE_OUT_PER_1M) / 1_000_000


def parse_json(text: str) -> Any:
    """
    応答から JSON を取り出す。解析できなければ None を返す。

    指定した形（オブジェクト）で返らないことがある。とくに該当なしの場合、
    {"remaining": []} ではなく [] だけを返してくることがあるため、
    オブジェクトと配列の両方を受け取れるようにしている。
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]

    candidates: list[tuple[int, str]] = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if 0 <= start < end:
            candidates.append((start, stripped[start:end + 1]))

    # 先に現れる方が外側。[{...}] で中のオブジェクトだけを拾わないようにする
    for _, chunk in sorted(candidates):
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            continue
    return None


def items_of(data: Any, key: str) -> list[Any]:
    """{"key": [...]} でも [...] でも、リストとして受け取れるようにする。"""
    if isinstance(data, dict):
        items = data.get(key) or []
        return items if isinstance(items, list) else []
    if isinstance(data, list):
        return data
    return []


def valid_bbox(bbox: Any) -> TypeGuard[Sequence[float]]:
    """座標として使える形か確かめる。モデルの応答は形が崩れることがある。"""
    return (
        isinstance(bbox, (list, tuple))
        and len(bbox) == 4
        and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in bbox)
    )


def converse(
    client: Any,
    model_id: str,
    image_bytes: bytes,
    prompt: str,
    usage: Usage,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Any:
    """Nova を呼び出して JSON を受け取る。解析できなかった場合は None を返す。"""
    response = client.converse(
        modelId=model_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "png", "source": {"bytes": image_bytes}}},
                    {"text": prompt},
                ],
            }
        ],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
    )
    usage.add(response.get("usage", {}))

    # 上限に達した応答は JSON が途中で終わっているため使えない。
    # 検出項目が多い画像で起きやすく、--max-tokens で引き上げられる。
    if response.get("stopReason") == "max_tokens":
        return None
    return parse_json(response["output"]["message"]["content"][0]["text"])


def find_images(directory: Path) -> list[Path]:
    """
    連番PNG（001.png のように数字のみのファイル名）を列挙する。

    サブディレクトリは辿らず、指定ディレクトリ直下のみを対象とする。
    1.png と 010.png が混在しても数値順に並ぶよう、ファイル名を数値として比較する。
    """
    images = [
        path
        for path in directory.iterdir()
        if path.is_file() and IMAGE_PATTERN.match(path.name)
    ]
    return sorted(images, key=lambda path: (int(path.stem), path.name))


def next_backup_dir(directory: Path) -> Path:
    """
    未使用のバックアップディレクトリのパスを決める。

    bak/ が存在しなければ bak/ を、既に存在する場合は bak2/, bak3/ ... と
    未使用の名前が見つかるまで採番する。過去の実行で退避した原本を
    後の実行で上書きしないための処理。
    """
    candidate = directory / BACKUP_BASE_NAME
    index = 2
    while candidate.exists():
        candidate = directory / f"{BACKUP_BASE_NAME}{index}"
        index += 1
    return candidate


def to_png_bytes(image: Image.Image) -> bytes:
    """画像を PNG のバイト列にする。"""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def to_request_bytes(image: Image.Image) -> bytes:
    """
    送信用のバイト列を作る。

    Nova 2 Lite の画像入力は解像度によらず一律のトークン数で課金されるため、
    精度を落とさないよう原則として縮小しない。送信サイズの上限を超える画像だけ、
    収まるところまで段階的に縮小する。
    """
    data = to_png_bytes(image)
    if len(data) <= MAX_IMAGE_BYTES:
        return data

    work = image.copy()
    while len(data) > MAX_IMAGE_BYTES and max(work.size) > 800:
        work.thumbnail(
            (int(work.width * 0.8), int(work.height * 0.8)), Image.Resampling.LANCZOS
        )
        data = to_png_bytes(work)
    return data


def dedupe_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同じ箇所を指す結果をまとめる。座標が近いものは同一とみなす。"""
    kept: list[dict[str, Any]] = []
    for hit in hits:
        x1, y1, x2, y2 = hit["bbox"]
        if any(
            abs(x1 - k["bbox"][0]) < 20 and abs(y1 - k["bbox"][1]) < 20
            and abs(x2 - k["bbox"][2]) < 20 and abs(y2 - k["bbox"][3]) < 20
            for k in kept
        ):
            continue
        kept.append(hit)
    return kept


def locate_texts(
    client: Any,
    args: argparse.Namespace,
    image_bytes: bytes,
    targets: list[str],
    usage: Usage,
) -> list[dict[str, Any]]:
    """指定した文字列が画像のどこにあるかを聞き、座標を得る。"""
    prompt = LOCATE_PROMPT.format(targets="\n".join(f"- {text}" for text in targets))
    data = converse(client, args.model, image_bytes, prompt, usage, max_tokens=args.max_tokens)

    hits: list[dict[str, Any]] = []
    for item in items_of(data, "found"):
        if not isinstance(item, dict):
            continue
        bbox = item.get("bbox")
        if valid_bbox(bbox):
            hits.append({"category": "digits12", "text": item.get("text", ""), "bbox": bbox})
    return hits


def line_entries(lines: list[Any]) -> list[dict[str, Any]]:
    """文字起こし結果から、テキストと座標が揃った行だけを読み順で取り出す。"""
    entries: list[dict[str, Any]] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        text, bbox = line.get("text", ""), line.get("bbox")
        if isinstance(text, str) and text and valid_bbox(bbox):
            entries.append({"text": text, "bbox": list(bbox)})
    return sorted(entries, key=lambda entry: (entry["bbox"][1], entry["bbox"][0]))


def is_wrapped(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """
    second が first の折り返しの続きとみなせるか判定する。

    値が長いと 1 行に収まらず折り返される。折り返し行は、直前の行のすぐ下にあり、
    横方向の範囲が重なる。左右に並んだ別の列を誤って連結しないための条件でもある。
    """
    fx1, fy1, fx2, fy2 = first["bbox"]
    sx1, sy1, sx2, sy2 = second["bbox"]

    height = fy2 - fy1
    if height <= 0:
        return False

    # 次の行が下にあり、行間が離れすぎていないこと。
    # 枠がわずかに重なって返ることがあるため、少しの食い込みは許容する。
    gap = sy1 - fy2
    if not -height * 0.5 <= gap <= height * WRAP_MAX_GAP_RATIO:
        return False

    # 横方向に重なっていること
    return bool(min(fx2, sx2) > max(fx1, sx1))


def spans_boundary(first_text: str, second_text: str) -> bool:
    """
    2 行を連結したとき、その境界をまたぐ 12 桁の並びがあるか調べる。

    折り返しによって 12 桁が行末と次行先頭に分断されると、行単位の判定では
    どちらにも一致しない。連結してから判定し、かつ一致が境界をまたぐものだけを
    拾うことで、行単体で完結する一致との二重計上を避ける。
    """
    combined = first_text + second_text
    split = len(first_text)
    return any(m.start() < split < m.end() for m in DIGIT_RUN.finditer(combined))


def scan_once(
    client: Any,
    args: argparse.Namespace,
    image_bytes: bytes,
    usage: Usage,
) -> list[dict[str, Any]]:
    """文字起こしを 1 回行い、12 桁の数字を含む行を拾う。"""
    data = converse(client, args.model, image_bytes, OCR_PROMPT, usage, max_tokens=args.max_tokens)
    lines = items_of(data, "lines")

    # 座標付きで返ってきた場合はそのまま使う
    entries = line_entries(lines)
    hits: list[dict[str, Any]] = [
        {"category": "digits12", "text": entry["text"], "bbox": entry["bbox"]}
        for entry in entries
        if DIGIT_RUN.search(entry["text"])
    ]

    # 折り返しで 12 桁が 2 行に分断されているケース。
    # ARN のような長い値では行末と次行先頭に分かれるため、行単位では一致しない。
    # 連結して境界をまたぐ並びを見つけたら、両方の行をマスク対象にする。
    #
    # 多段組みの画面では、折り返し行のあいだに別カラムの行が y 座標的に挟まる。
    # そのため並び順で隣り合う行だけを見ても見つからない。組み合わせは位置関係
    # （is_wrapped）で絞れるので、行数は多くないことも踏まえて総当たりする。
    for first in entries:
        for second in entries:
            if first is second:
                continue
            if is_wrapped(first, second) and spans_boundary(first["text"], second["text"]):
                hits.append({"category": "digits12-wrapped", "text": first["text"],
                             "bbox": first["bbox"]})
                hits.append({"category": "digits12-wrapped", "text": second["text"],
                             "bbox": second["bbox"]})

    if hits:
        return dedupe_hits(hits)

    # 座標付きで返っていたのに該当が無いなら、この画像には無かったということ。
    # ここで聞き直すと、座標を使った折り返し判定を迂回してしまう。
    if entries:
        return []

    # 座標なし（文字列だけ）で返ることがある。その場合は該当行の位置を聞き直す。
    texts = [
        line.get("text", "") if isinstance(line, dict) else line
        for line in lines
    ]
    texts = [text for text in texts if isinstance(text, str) and text]

    targets: list[str] = [text for text in texts if DIGIT_RUN.search(text)]
    # 座標が無いと折り返しかどうかは判断できないため、並び順で隣り合う行を連結して調べる
    for first_text, second_text in zip(texts, texts[1:], strict=False):
        if spans_boundary(first_text, second_text):
            targets += [first_text, second_text]

    targets = list(dict.fromkeys(targets))
    if not targets:
        return []
    return locate_texts(client, args, image_bytes, targets, usage)


def scan_digit_runs(
    client: Any,
    args: argparse.Namespace,
    image_bytes: bytes,
    usage: Usage,
) -> list[dict[str, Any]]:
    """
    12 桁の数字を含む行を、文字起こしと正規表現で拾う。

    検出プロンプトだけでは、長い識別子に埋め込まれた数字を取りこぼす。
    ここでは判定をモデルに委ねず、文字起こししたテキストに正規表現をかけ、
    該当した行はまるごとマスク対象にする。範囲は広くなるが確実に消せる。

    文字起こし自体は実行のたびに揺らぎ、1 回では拾い切れないことがある。
    そのため既定で複数回実行し、結果を重ね合わせる（--ocr-passes）。
    """
    hits: list[dict[str, Any]] = []
    for _ in range(max(1, args.ocr_passes)):
        hits += scan_once(client, args, image_bytes, usage)
    return dedupe_hits(hits)


def merge_findings(
    findings: list[dict[str, Any]],
    extra: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """重なりの大きい枠を捨てて統合する。同じ箇所を二重に塗らないため。"""
    findings = [f for f in findings if isinstance(f, dict) and valid_bbox(f.get("bbox"))]
    merged = list(findings)
    for item in extra:
        ex1, ey1, ex2, ey2 = item["bbox"]
        for finding in findings:
            fx1, fy1, fx2, fy2 = finding["bbox"]
            # 既存の枠が新しい枠にすっぽり入るなら、行全体で塗るので不要
            if fx1 >= ex1 and fy1 >= ey1 and fx2 <= ex2 and fy2 <= ey2:
                merged = [m for m in merged if m is not finding]
        merged.append(item)
    return merged


def paint_region(image: Image.Image, box: PixelBox, style: str, text_height: float) -> None:
    """
    指定された領域をその場で塗り潰す。

    pixelate は領域を縮小してから最近傍補間で拡大し直すことでモザイクにする。
    blur は文字サイズに応じた半径のガウスぼかし。black は単色での置き換えで、
    元の画素値が残らないため最も確実。
    """
    if style == "black":
        ImageDraw.Draw(image).rectangle(list(box), fill="black")
        return

    region_box = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
    region = image.crop(region_box)
    width, height = region.size
    if width < 1 or height < 1:
        return

    if style == "blur":
        masked = region.filter(ImageFilter.GaussianBlur(max(2.0, text_height * BLUR_RADIUS_RATIO)))
    else:
        small = region.resize(
            (max(1, width // PIXELATE_BLOCKS), max(1, height // PIXELATE_BLOCKS)),
            Image.Resampling.BILINEAR,
        )
        masked = small.resize((width, height), Image.Resampling.NEAREST)

    image.paste(masked, region_box)


def apply_mask(
    image: Image.Image,
    findings: list[dict[str, Any]],
    pad_x_ratio: float,
    pad_y_ratio: float,
    style: str,
) -> list[PixelBox]:
    """[0,1000] 正規化座標を実寸へ変換し、パディングを付けて塗り潰す。"""
    width, height = image.size
    boxes: list[PixelBox] = []

    for finding in findings:
        x1, y1, x2, y2 = finding["bbox"]
        px1, py1 = x1 / 1000 * width, y1 / 1000 * height
        px2, py2 = x2 / 1000 * width, y2 / 1000 * height
        text_height = py2 - py1                      # 検出枠の高さ = 文字サイズの目安
        pad_x, pad_y = text_height * pad_x_ratio, text_height * pad_y_ratio
        box: PixelBox = (
            max(0.0, px1 - pad_x),
            max(0.0, py1 - pad_y),
            min(float(width), px2 + pad_x),
            min(float(height), py2 + pad_y),
        )
        if box[2] - box[0] < 1 or box[3] - box[1] < 1:
            continue
        paint_region(image, box, style, text_height)
        boxes.append(box)

    return boxes


def drop_hallucinations(
    remaining: list[Any],
    boxes: list[PixelBox],
    size: tuple[int, int],
) -> list[dict[str, Any]]:
    """
    マスク済み領域を指す残存報告を落とす。

    再検証にマスク後の画像を渡すと、Nova はマスクの下にあったはずの値を文脈から
    推測して「まだ読める」と報告してくることがある。プロンプトで禁止しても消えない
    ため、報告された座標がマスク矩形の内側なら機械的にハルシネーションとみなす。
    マスクがずれて本当に読めてしまっている場合は矩形の外側に出るため検知できる。
    """
    width, height = size
    real: list[dict[str, Any]] = []

    for report in remaining:
        if not isinstance(report, dict):
            continue
        bbox = report.get("bbox")
        if valid_bbox(bbox):
            cx = (bbox[0] + bbox[2]) / 2 / 1000 * width
            cy = (bbox[1] + bbox[3]) / 2 / 1000 * height
            if not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes):
                real.append(report)
        else:
            # 座標が読めない報告は判断できないため、安全側に倒して残存として扱う
            real.append(report)

    return real


def shrink_to_width(image: Image.Image, max_width: int) -> Image.Image | None:
    """
    最大幅を超える画像を、縦横比を保ったまま縮小する。

    Args:
        image: 対象の画像
        max_width: 出力の最大幅。0 以下なら縮小しない

    Returns:
        縮小した画像。縮小が不要なら None
    """
    if max_width <= 0 or image.width <= max_width:
        return None
    height = max(1, round(image.height * max_width / image.width))
    return image.resize((max_width, height), Image.Resampling.LANCZOS)


def write_output(
    image: Image.Image,
    image_path: Path,
    backup_dir: Path | None,
    max_width: int,
    masked: bool,
) -> None:
    """
    処理結果を元のパスへ書き戻す。

    マスクの有無にかかわらず、最大幅を超える画像は縮小して保存する。
    マスクもされず縮小も不要な場合は、無駄な書き換えを避けるため何もしない。
    backup_dir が None のとき（ドライラン）は一切書き込まない。
    """
    if backup_dir is None:
        return

    shrunk = shrink_to_width(image, max_width)
    if shrunk is not None:
        shrunk.save(image_path)
    elif masked:
        image.save(image_path)


def process_image(
    client: Any,
    image_path: Path,
    backup_dir: Path | None,
    args: argparse.Namespace,
    usage: Usage,
) -> Result:
    """
    画像 1 枚をバックアップし、認証情報をマスクして上書きする。

    マスク対象が無かった画像も、最大幅を超えていれば縮小して書き戻す。
    検出結果を解析できなかった場合だけは、原寸のまま再実行できるよう手を付けない。

    backup_dir が None の場合（ドライラン）はバックアップも上書きも行わず、
    検出と再検証だけを実施する。
    """
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, backup_dir / image_path.name)

    with Image.open(image_path) as opened:
        # パレット画像などはマスク処理を適用できないためRGBに変換する
        # 上書き保存する前に元ファイルを閉じたいので、ここで画素を読み切る
        image = opened.copy() if opened.mode in ("RGB", "RGBA") else opened.convert("RGB")

    image_bytes = to_request_bytes(image)

    # 12 桁スキャンはスクリーニングより先に、無条件で実行する。
    # スクリーニングは「認証情報らしさ」で判断するため、識別子やリソース名に
    # 埋もれた数字しか写っていない画面を「対象なし」と判定してしまう。
    # 数字の判定は正規表現で完結するので、モデルの判断を待つ必要がない。
    digit_hits = [] if args.no_digit_scan else scan_digit_runs(client, args, image_bytes, usage)

    if not args.no_screen and not digit_hits:
        screened = converse(
            client, args.model, image_bytes,
            SCREEN_PROMPT.format(categories=CATEGORIES), usage, max_tokens=100,
        )
        # 判定できなかったときは検出へ進める（対象なしと決めつけない）
        if isinstance(screened, dict) and not screened.get("has_credentials"):
            write_output(image, image_path, backup_dir, args.max_width, masked=False)
            return Result("clean", 0, 0, [])

    detected = converse(
        client, args.model, image_bytes,
        DETECT_PROMPT.format(categories=CATEGORIES), usage, max_tokens=args.max_tokens,
    )
    if detected is None:
        return Result(
            "review", 0, len(digit_hits),
            ["could not parse the detection result (try raising --max-tokens)"],
        )

    findings = [
        f for f in items_of(detected, "findings")
        if isinstance(f, dict) and valid_bbox(f.get("bbox"))
    ]
    findings = merge_findings(findings, digit_hits)

    if not findings:
        write_output(image, image_path, backup_dir, args.max_width, masked=False)
        return Result("clean", 0, len(digit_hits), [])

    boxes = apply_mask(image, findings, args.padding_x, args.padding_y, args.style)
    if not boxes:
        write_output(image, image_path, backup_dir, args.max_width, masked=False)
        return Result("clean", 0, len(digit_hits), [])

    # 再検証は縮小前の画像に対して行う（小さくすると読み取り精度が落ちるため）
    masked_bytes = to_request_bytes(image)
    write_output(image, image_path, backup_dir, args.max_width, masked=True)

    if args.no_verify:
        return Result("masked", len(boxes), len(digit_hits), [])

    verified = converse(
        client, args.model, masked_bytes,
        VERIFY_PROMPT.format(categories=CATEGORIES), usage, max_tokens=args.max_tokens,
    )
    if verified is None:
        return Result(
            "review", len(boxes), len(digit_hits),
            ["could not parse the verification result (try raising --max-tokens)"],
        )

    remaining = drop_hallucinations(items_of(verified, "remaining"), boxes, image.size)
    if remaining:
        notes = [
            f"possibly still readable: {r.get('category')} {str(r.get('text'))[:40]!r}"
            for r in remaining
        ]
        return Result("review", len(boxes), len(digit_hits), notes)

    return Result("masked", len(boxes), len(digit_hits), [])


def display_images(images: list[Path], directory: Path) -> None:
    """処理対象の画像一覧を表示する。"""
    print(f"\nFound {len(images)} image{'s' if len(images) > 1 else ''}:\n")
    for image_path in images:
        print(f"  [IMG] {image_path.relative_to(directory)}")
    print()


def build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数の定義を組み立てる。"""
    parser = argparse.ArgumentParser(
        description="連番PNG画像に写り込んだ認証情報を検出してマスクする",
        prog="mask-tool",
    )
    parser.add_argument(
        "-d", "--directory",
        type=str,
        default=".",
        help="Directory containing the images (default: current directory)"
    )
    parser.add_argument(
        "-s", "--style",
        choices=["pixelate", "blur", "black"],
        default=DEFAULT_STYLE,
        help=f"Masking style (default: {DEFAULT_STYLE}). "
             "black is the most certain since it replaces the pixels"
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Show what would be masked without modifying any file"
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=DEFAULT_MAX_WIDTH,
        help=f"Resize the saved image so it is at most this wide, keeping the aspect ratio. "
             f"Applies to images with nothing masked as well. 0 disables resizing "
             f"(default: {DEFAULT_MAX_WIDTH})"
    )
    parser.add_argument(
        "--ocr-passes",
        type=int,
        default=DEFAULT_OCR_PASSES,
        help=f"How many times to transcribe the image for the 12-digit scan "
             f"(default: {DEFAULT_OCR_PASSES})"
    )
    parser.add_argument(
        "--no-digit-scan",
        action="store_true",
        help="Skip the 12-digit scan"
    )
    parser.add_argument(
        "--no-screen",
        action="store_true",
        help="Skip screening and run detection on every image"
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip verification of the masked image"
    )
    parser.add_argument(
        "--padding-x",
        type=float,
        default=DEFAULT_PAD_X,
        help=f"Horizontal padding, as a multiple of the detected box height "
             f"(default: {DEFAULT_PAD_X})"
    )
    parser.add_argument(
        "--padding-y",
        type=float,
        default=DEFAULT_PAD_Y,
        help=f"Vertical padding, same unit (default: {DEFAULT_PAD_Y})"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help=f"Response token limit (default: {DEFAULT_MAX_TOKENS})"
    )
    parser.add_argument(
        "--region",
        type=str,
        default=DEFAULT_REGION,
        help=f"AWS region for Amazon Bedrock (default: {DEFAULT_REGION})"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL_ID,
        help=f"Bedrock model ID (default: {DEFAULT_MODEL_ID})"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}"
    )
    return parser


def main() -> int:
    """
    mask-tool のエントリポイント。

    Returns:
        終了コード（正常終了は 0、エラーは 1）
    """
    args = build_parser().parse_args()

    directory = Path(args.directory).resolve()

    if not directory.exists():
        print(f"Error: Directory '{directory}' does not exist.", file=sys.stderr)
        return 1

    if not directory.is_dir():
        print(f"Error: '{directory}' is not a directory.", file=sys.stderr)
        return 1

    print(f"Scanning: {directory}")

    images = find_images(directory)
    if not images:
        print("No sequentially numbered PNG files (e.g. 001.png) found.")
        return 0

    display_images(images, directory)

    backup_dir = None if args.dry_run else next_backup_dir(directory)

    print(f"Model:   {args.model} ({args.region})")
    print(f"Style:   {args.style}")
    print(f"Width:   {f'max {args.max_width}px' if args.max_width > 0 else 'unchanged'}")
    print(f"Backup:  {'(dry run - not saved)' if backup_dir is None else backup_dir.name + '/'}")
    print(
        f"\nNote: {len(images)} image(s) will be sent to Amazon Bedrock, "
        "several calls per image. Charges apply per token."
    )

    client = boto3.client(
        "bedrock-runtime",
        region_name=args.region,
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )

    usage = Usage()
    tally = {"clean": 0, "masked": 0, "review": 0}
    review: list[tuple[str, list[str]]] = []

    print()
    for index, image_path in enumerate(images, start=1):
        try:
            result = process_image(
                client, image_path, backup_dir, args, usage
            )
        except NoCredentialsError as error:
            print(f"  Error: {error}", file=sys.stderr)
            print("Aborted. Configure your AWS credentials and try again.", file=sys.stderr)
            return 1
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in FATAL_ERROR_CODES:
                print(f"  Error: {error}", file=sys.stderr)
                print(
                    "Aborted. Check your AWS credentials, region, "
                    "and Amazon Bedrock model access.",
                    file=sys.stderr,
                )
                return 1
            # 1 枚の失敗で全体を止めない。処理できなかった画像は要確認へ回す
            result = Result("review", 0, 0, [f"{type(error).__name__}: {error}"])
        except (OSError, KeyError, IndexError, ValueError) as error:
            result = Result("review", 0, 0, [f"{type(error).__name__}: {error}"])

        tally[result.status] += 1
        mark = {"clean": "--", "masked": "OK", "review": "!!"}[result.status]
        print(f"[{index}/{len(images)}] [{mark}] {image_path.name}  findings={result.findings}")
        if result.digits:
            print(f"        12-digit scan: {result.digits} hit(s)")
        for note in result.notes:
            print(f"        {note}")
        if result.status == "review":
            review.append((image_path.name, result.notes))

    print(
        f"\nMasked: {tally['masked']}  Nothing found: {tally['clean']}  "
        f"Needs review: {tally['review']}"
    )
    if review:
        print("\nCheck these images by eye:")
        for name, notes in review:
            print(f"  {name}")
            for note in notes:
                print(f"      {note}")

    print(f"\n{usage.calls} call(s) / {usage.input} in / {usage.output} out tokens")
    print(f"Estimated cost ${usage.usd():.4f}")

    if args.dry_run:
        print("Dry run mode - no files were modified.")
    elif backup_dir is not None and backup_dir.exists():
        print(f"Original images were saved to {backup_dir.name}/")

    return 1 if tally["review"] else 0


if __name__ == "__main__":
    sys.exit(main())
