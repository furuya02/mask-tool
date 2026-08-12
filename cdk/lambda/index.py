import base64
import json
import os
import re

import boto3

MODEL_ID = os.environ.get("MODEL_ID", "jp.amazon.nova-2-lite-v1:0")

PROMPT_TEMPLATE = """Your task is to detect and localize defects on the metal part in the image with high precision and recall.

The defects to be detected are: {elements}

Output Requirements:
1. Provide coordinates of the top-left corner and bottom-right corner of each bounding box
2. Use [x_min, y_min, x_max, y_max] format with values from 0-1000
3. Fit bounding boxes tightly around each defect
4. Detect all instances of the specified defects
5. If a defect is not found, return an empty array for that defect

Return ONLY valid JSON in this exact format:
{schema}

Example output:
{{"scratch": [{{"bbox": [321, 432, 543, 876]}}], "dent": [], "rust": [{{"bbox": [100, 200, 150, 300]}}, {{"bbox": [400, 500, 450, 600]}}]}}

Important: Return pure JSON without any markdown formatting, explanations, or code blocks."""


def parse_detections(text):
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    raw = (match.group(1) if match else text).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 引用符が壊れた JSON が返ることがあるため、ラベルごとに座標 4 つ組を正規表現で抽出する
        result = {}
        parts = re.split(r"[\"'](\w[\w \-]*)[\"']\s*:\s*\[", raw)
        for i in range(1, len(parts), 2):
            content = parts[i + 1] if i + 1 < len(parts) else ""
            bboxes = re.findall(
                r"\[\s*\"?(\d+)\"?\s*,\s*\"?(\d+)\"?\s*,\s*\"?(\d+)\"?\s*,\s*\"?(\d+)\"?\s*\]",
                content,
            )
            result[parts[i]] = [
                {"bbox": [int(a), int(b), int(c), int(d)]} for a, b, c, d in bboxes
            ]
        return result


def handler(event, context):
    body = json.loads(event["body"])
    image_bytes = base64.b64decode(body["image"])
    image_format = body.get("format", "jpeg")
    objects = body.get("objects", ["scratch", "dent", "rust"])

    schema = json.dumps(
        {name: [{"bbox": ["x_min", "y_min", "x_max", "y_max"]}] for name in objects}
    )
    prompt = PROMPT_TEMPLATE.format(elements=", ".join(objects), schema=schema)

    bedrock = boto3.client("bedrock-runtime")
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                    {"text": prompt},
                ],
            }
        ],
        inferenceConfig={"temperature": 0, "maxTokens": 2048},
    )
    text = response["output"]["message"]["content"][0]["text"]
    print(f"model response: {text}")

    result = parse_detections(text)

    # 0-1000 の正規化座標のまま返却し、ピクセル変換・描画はクライアント側で行う
    labels = []
    boxes = []
    for label, detections in result.items():
        for d in detections:
            box = d.get("bbox", []) if isinstance(d, dict) else d
            try:
                box = [int(v) for v in box]
            except (ValueError, TypeError):
                continue
            if len(box) == 4 and box[0] < box[2] and box[1] < box[3]:
                labels.append(label)
                boxes.append(box)

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(
            {
                "detections": {"labels": labels, "boxes": boxes},
                "usage": response.get("usage", {}),
            }
        ),
    }
