# bedrock-nova2-metal-defect-inspection

[English README](README.md)

Amazon Nova 2 Lite (Amazon Bedrock) を使用した金属部品の欠陥検出サンプルです。
「scratch（傷）」「dent（へこみ）」「rust（錆）」のような欠陥を自然言語で指定するだけで、
モデルがバウンディングボックスを返します。コンピュータビジョンモデルのトレーニングは不要です。

## アーキテクチャ

- **Amazon API Gateway** — 検査画像（Base64）を POST で受け付け
- **AWS Lambda (Python)** — プロンプトを組み立て、Bedrock Converse API で Nova 2 Lite（`jp.amazon.nova-2-lite-v1:0`）を呼び出し
- **Amazon S3 + CloudFront (OAC)** — シンプルな Web UI をホスト（プライベートバケット）
- バウンディングボックスは 0-1000 の正規化座標で返却し、描画はクライアント側（ブラウザ Canvas / Pillow）で実施

全リソースはサーバーレスの従量課金で、放置しても固定費は発生しません。

## 前提条件

- Node.js / pnpm
- デプロイ権限のある AWS CLI 認証情報
- Amazon Bedrock: `ap-northeast-1` で Amazon Nova 2 Lite が有効であること

## デプロイ

```bash
git clone https://github.com/furuya02/mask-tool.git
cd mask-tool/cdk

pnpm install
pnpm cdk bootstrap   # アカウント/リージョンごとに初回のみ
pnpm cdk deploy
```

S3 バケット名はデフォルトで `bedrock-nova2-metal-defect-inspection-{アカウントID}-frontend` です。
アカウント ID の部分はコンテキストパラメータで上書きできます。

```bash
pnpm cdk deploy -c suffix=20260805
```

出力:

- `ApiUrl` — API Gateway エンドポイント
- `WebsiteUrl` — Web UI の CloudFront URL

## 動作確認

### Web UI

1. ブラウザで `WebsiteUrl` を開く
2. 検査画像（PNG / JPEG）をアップロード
3. 必要に応じて欠陥リストを編集（デフォルト: `scratch, dent, rust`）
4. 「検査する」をクリック — 検出された欠陥がバウンディングボックスで描画されます

### スクリプト

```bash
cd ../scripts
pip install -r requirements.txt
python detect.py <ApiUrl>detect images/sample.jpg "scratch,dent,rust"
# -> result_sample.png が生成されます
```

## 後片付け

```bash
cd cdk
pnpm cdk destroy
```

## コスト

- Nova 2 Lite はトークン単位の課金です（1 画像あたりおおよそ $0.001 未満）
- Lambda / API Gateway / S3 / CloudFront は従量課金で、待機コストはありません

## ライセンス

MIT License
