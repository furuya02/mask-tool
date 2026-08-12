# bedrock-nova2-metal-defect-inspection

[日本語版 README](README.ja.md)

Metal part defect inspection sample using Amazon Nova 2 Lite (Amazon Bedrock).
Specify defects such as "scratch", "dent", or "rust" in natural language, and the
model returns bounding boxes — no computer vision model training required.

## Architecture

- **Amazon API Gateway** — accepts an inspection image (Base64) via POST
- **AWS Lambda (Python)** — builds the prompt and calls Nova 2 Lite via the Bedrock Converse API (`jp.amazon.nova-2-lite-v1:0`)
- **Amazon S3 + CloudFront (OAC)** — hosts a simple web UI (private bucket)
- Bounding boxes are returned as 0-1000 normalized coordinates; drawing is done on the client side (browser Canvas / Pillow)

All resources are serverless and pay-per-use. No idle cost remains.

## Prerequisites

- Node.js / pnpm
- AWS CLI credentials with deploy permissions
- Amazon Bedrock: Amazon Nova 2 Lite enabled in `ap-northeast-1`

## Deploy

```bash
git clone https://github.com/furuya02/mask-tool.git
cd mask-tool/cdk

pnpm install
pnpm cdk bootstrap   # only once per account/region
pnpm cdk deploy
```

The S3 bucket name is `bedrock-nova2-metal-defect-inspection-{ACCOUNT_ID}-frontend` by default.
You can override the account ID part with a context parameter:

```bash
pnpm cdk deploy -c suffix=20260805
```

Outputs:

- `ApiUrl` — API Gateway endpoint
- `WebsiteUrl` — CloudFront URL of the web UI

## Usage

### Web UI

1. Open `WebsiteUrl` in a browser
2. Upload an inspection image (PNG / JPEG)
3. Edit the defect list if needed (default: `scratch, dent, rust`)
4. Click the inspect button — detected defects are drawn with bounding boxes

### Script

```bash
cd ../scripts
pip install -r requirements.txt
python detect.py <ApiUrl>detect images/sample.jpg "scratch,dent,rust"
# -> result_sample.png
```

## Clean up

```bash
cd cdk
pnpm cdk destroy
```

## Cost

- Nova 2 Lite is charged per token (roughly less than $0.001 per image)
- Lambda / API Gateway / S3 / CloudFront are pay-per-use with no idle cost

## License

MIT License
