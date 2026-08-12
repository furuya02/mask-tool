# mask-tool

[日本語版 README](README.ja.md)

A command-line tool that detects credentials left visible in sequentially numbered PNG
files and masks them, using Amazon Nova 2 Lite (Amazon Bedrock).

It processes every `001.png`-style file in the current directory at once, keeps the
originals in a backup directory, and overwrites the images in place.

Nothing is deployed to AWS, so images never have to be uploaded to S3 and there is no
idle cost to worry about.

No regular expressions or keyword lists to maintain for the detection itself. Nova 2 Lite
reads the image and decides from context, so Japanese screenshots work as well as English
ones.

> This tool is a repackaging of
> [nova2-image-credential-masker](https://github.com/furuya02/nova2-image-credential-masker)
> as an installable CLI that works on the current directory in place.
> See the accompanying article:
> [Amazon Nova 2 Lite で画像内の認証情報をマスクする](https://dev.classmethod.jp/articles/bedrock-nova-2-lite-image-credential-masking/)

## How it works

Images stay on your machine — Bedrock is called directly, and nothing is created on the
AWS side.

Each image goes through five steps.

| Step | What it does | Why |
|---|---|---|
| 1 Digit scan | Transcribes the text and finds 12-digit runs with a regex | Keeps the judgement out of the model |
| 2 Screening | Decides only whether credentials are present | Skips irrelevant images early to keep cost down |
| 3 Detection | Gets a bounding box for each credential | Returned in the normalized `[0, 1000]` space |
| 4 Masking | Paints over the text with Pillow | Converts coordinates to pixels and adds padding |
| 5 Verification | Re-checks the masked image | Anything suspicious is listed at the end of the run |

Detection results vary between runs. A single detection pass is not guaranteed to catch
everything, which is what steps 1 and 5 are for.

### How 12-digit numbers are handled

A 12-digit number embedded in a longer identifier tends to slip past detection. Asked to
find it, the model treats `arn:aws:iam::123456789012:user/foo` as a single identifier and
never looks at the digits inside. The same happens with an account ID sitting alone in a
detail panel — the detection pass simply misses it.

So the judgement does not sit with the model. The image is transcribed, and **a regex
decides what counts as 12 digits**. Matching lines are masked whole rather than trimmed to
the value — a wider box, but a reliable one.

Transcription itself varies between runs, so it is repeated 3 times by default and the
results are merged (`--ocr-passes`). Pass `--no-digit-scan` to skip this pass entirely.

#### When the digits are split across two lines

A long value such as a subnet ARN wraps, and the account ID can end up split — the last
few digits at the end of one line, the rest at the start of the next. Neither line
contains 12 consecutive digits on its own, so a line-by-line check finds nothing.

So lines are also joined in pairs and checked. When a 12-digit run **crosses the join**,
both lines are masked whole. Lines are treated as a wrapped pair only when the second sits
directly below the first and their horizontal ranges overlap, so columns sitting side by
side are never joined.

Pairs are found by position, not by reading order. In a multi-column layout such as an AWS
console detail panel, a line from a neighbouring column often falls between the two halves
of a wrapped value when everything is sorted top to bottom, so only comparing consecutive
lines would miss it.

Two unrelated lines can still form 12 digits by coincidence, which results in more being
masked than necessary — the safe direction to err in.

### Deciding what was actually missed

When the masked image is sent back for verification, Nova sometimes infers the value that
used to be under a mask and reports it as "still readable". Forbidding this in the prompt
does not stop it.

So this tool asks the verification step for coordinates as well, and **ignores any report
whose position falls inside a masked area**, treating it as a guess. If a mask is
misaligned and a value really is legible, it falls outside the area and is correctly
flagged.

### Keeping processing inside Japan

The default model ID is `jp.amazon.nova-2-lite-v1:0`.

Nova 2 Lite does not support on-demand invocation, so it must be called through an
inference profile. The `jp.` profile routes only to `ap-northeast-1` and `ap-northeast-3`,
which keeps processing within Japan.

To route globally, pass `--model global.amazon.nova-2-lite-v1:0`.

### Images are sent at full resolution

Image input is billed at a flat 230 tokens per image regardless of resolution
([Multimodal understanding](https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-multimodal-models.html)).
Downscaling would therefore save nothing while making small text harder to read, so images
are sent as they are. Only images too large for the request are shrunk until they fit.

### Saved images are resized to 900px wide

Screenshots are usually too large to embed in an article as they are, so the saved image is
resized to at most **900px wide**, keeping the aspect ratio (`--max-width`, `0` disables it).

Detection, masking and verification all run at the original resolution — the resize is
applied last, so it never costs accuracy. **Images with nothing masked are resized too**,
so every file ends up the same width. Images already at or below the limit are left
untouched rather than re-encoded, and the backup always keeps the original size.

The only exception is an image whose detection result could not be parsed. That file is
left at its original size so a retry can run at full resolution.

## Requirements

- Python 3.10 or higher
- Model access to Nova 2 Lite enabled in Amazon Bedrock
- AWS credentials with the following permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": [
        "arn:aws:bedrock:*:*:inference-profile/jp.amazon.nova-2-lite-v1:0",
        "arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0"
      ]
    }
  ]
}
```

Both the inference profile and the foundation model need to be allowed, since the call
goes through the profile.

Credentials are resolved by boto3 in the usual way (environment variables,
`~/.aws/credentials`, IAM role, ...).

## Installation

```bash
git clone https://github.com/furuya02/mask-tool.git
cd mask-tool
pip install -e .
```

After installing with pip, the `mask-tool` command will be available globally.

## Usage

Move to the directory that contains the images and run:

```bash
mask-tool
```

Every PNG whose name consists of digits only (`001.png`, `002.png`, ...) is processed.
Example output:

```
Scanning: /path/to/screenshots

Found 3 images:

  [IMG] 001.png
  [IMG] 002.png
  [IMG] 003.png

Model:   jp.amazon.nova-2-lite-v1:0 (ap-northeast-1)
Style:   pixelate
Width:   max 900px
Backup:  bak/

Note: 3 image(s) will be sent to Amazon Bedrock, several calls per image. Charges apply per token.

[1/3] [OK] 001.png  findings=3
        12-digit scan: 1 hit(s)
[2/3] [--] 002.png  findings=0
[3/3] [!!] 003.png  findings=2
        possibly still readable: aws_account_id '123456789012'

Masked: 2  Nothing found: 1  Needs review: 1

Check these images by eye:
  003.png
      possibly still readable: aws_account_id '123456789012'

14 call(s) / 4820 in / 1130 out tokens
Estimated cost $0.0056
Original images were saved to bak/
```

`[OK]` masked, `[--]` nothing found, `[!!]` needs review. The exit code is 1 when any
image needs review.

### Options

```
usage: mask-tool [-h] [-d DIRECTORY] [-s {pixelate,blur,black}] [-n]
                 [--max-width MAX_WIDTH] [--ocr-passes OCR_PASSES]
                 [--no-digit-scan] [--no-screen] [--no-verify]
                 [--padding-x PADDING_X] [--padding-y PADDING_Y]
                 [--max-tokens MAX_TOKENS] [--region REGION] [--model MODEL] [-v]
```

| Option | Default | Description |
|---|---|---|
| `-d`, `--directory` | current directory | Directory containing the images |
| `-s`, `--style` | `pixelate` | How to hide values (`pixelate` / `blur` / `black`) |
| `-n`, `--dry-run` | - | Detect and verify without modifying any file |
| `--max-width` | `900` | Resize the saved image to at most this width (`0` disables) |
| `--ocr-passes` | `3` | How many times to transcribe for the 12-digit scan |
| `--no-digit-scan` | - | Skip the 12-digit scan |
| `--no-screen` | - | Skip screening and run detection on every image |
| `--no-verify` | - | Skip verification of the masked image |
| `--padding-x` | `1.5` | Horizontal padding, as a multiple of the detected box height |
| `--padding-y` | `0.4` | Vertical padding, same unit |
| `--max-tokens` | `4000` | Response token limit |
| `--region` | `ap-northeast-1` | Region |
| `--model` | `jp.amazon.nova-2-lite-v1:0` | Model ID (inference profile) |
| `-v`, `--version` | - | Show the version |

### Examples

**See what would be masked without touching the files:**

```bash
mask-tool --dry-run
```

**Process a specific directory:**

```bash
mask-tool -d /path/to/screenshots
```

**Keep the original size:**

```bash
mask-tool --max-width 0
```

**Replace the pixels with solid black — the most certain option:**

```bash
mask-tool --style black
```

**Trade thoroughness for cost (one call per image):**

```bash
mask-tool --no-digit-scan --no-screen --no-verify
```

### About the masking style

The default is `pixelate`. `blur` is the softer variant, and `black` replaces the region
with a solid colour.

Use `black` when certainty matters more than appearance — painting the region a solid
colour replaces what was there, which is the more reliable way to hide it.

### About padding

Detected boxes sit slightly off from the actual text, so padding is added before painting.

Padding is expressed as a multiple of the **detected box height** rather than the image
size. Because it follows the text size, the same value works across images of different
resolutions.

Where characters sit flush against the value — a number embedded in a longer string, say —
the default will also cover a few neighbouring characters. Lower `--padding-x` if the
surrounding text matters more to you, at the cost of a higher chance of missing part of a
value.

## Backup

The original images are always copied before being overwritten.

The backup directory is `bak/`. If `bak/` already exists, `bak2/`, `bak3/` ... are used in
turn, so running the tool repeatedly in the same directory never destroys the originals
kept by an earlier run.

```
screenshots/
├── 001.png      <- masked (overwritten)
├── 002.png      <- masked (overwritten)
├── bak/         <- 1st run: the untouched originals
│   ├── 001.png
│   └── 002.png
└── bak2/        <- 2nd run: the images as they were before the 2nd run
    ├── 001.png
    └── 002.png
```

## When the response cannot be parsed

Nova does not always answer in the exact shape the prompt asks for. With nothing to report
it may return just `[]` instead of `{"remaining": []}`, so this tool accepts both an object
and a bare array.

If the response still cannot be parsed — cut off by the token limit, or not JSON at all —
the image is reported as needing review with the reason. Raise the limit and run again:

```bash
mask-tool --max-tokens 8000
```

A failure on one image does not stop the rest of the folder.

## Cost

Nothing runs continuously on AWS, so there is no idle cost. The only charge is Bedrock
usage, and the estimated cost of each run is printed at the end.

With the defaults, each image takes up to six calls: three transcriptions, screening,
detection and verification.

A measured run over six dense AWS console screenshots came to **$0.13 — about $0.02 per
image** (28 calls, 13,774 input and 37,421 output tokens). Output tokens dominate: the
transcription pass reproduces every line of text in the image, and it runs three times by
default.

Cost therefore scales with how much text an image contains, not with its resolution. Plain
screenshots cost far less than a dense console page. Lower `--ocr-passes` (or pass
`--no-digit-scan`) to cut it, at the cost of missing more.

Nova 2 Lite in the Tokyo region is priced at $0.396 per 1M input tokens and $3.311 per 1M
output tokens.

## Limitations

- Only PNG files whose name consists of digits only (`001.png`, `12.png`) are processed.
  Files such as `screenshot_001.png` are ignored.
- Subdirectories are not searched.
- Detection relies on the model's judgement and will not catch every credential. Results
  have been observed to vary between runs on the same image. **Always review images by eye
  before publishing them.** This tool is meant to reduce that work, not to replace it.
- Images are sent to Amazon Bedrock for analysis. With the default `jp.` profile this stays
  within Japanese regions, but confirm it is acceptable for your data before pointing the
  tool at sensitive material.

## License

MIT License

## Contributing

Issues and pull requests are welcome.
