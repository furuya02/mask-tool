# mask-tool

[English README](README.md)

Amazon Nova 2 Lite (Amazon Bedrock) を使用して、連番PNG画像に写り込んだ認証情報を
検出してマスクするコマンドラインツールです。

カレントディレクトリの `001.png` のような連番PNGをまとめて処理し、原本をバックアップ
ディレクトリへ退避したうえで、元画像を上書きします。

AWS 側には何もデプロイしません。画像を S3 にアップロードする必要はなく、待機コストも
発生しません。

検出そのものに正規表現やキーワードリストのメンテナンスは不要です。Nova 2 Lite が画像を
読んで文脈から判断するため、日本語のスクリーンショットも英語と同様に扱えます。

> このツールは
> [nova2-image-credential-masker](https://github.com/furuya02/nova2-image-credential-masker)
> を、カレントディレクトリに対してその場で動作する CLI として再パッケージしたものです。
> 解説記事:
> [Amazon Nova 2 Lite で画像内の認証情報をマスクする](https://dev.classmethod.jp/articles/bedrock-nova-2-lite-image-credential-masking/)

## 仕組み

画像は手元に置いたまま Bedrock を直接呼び出すため、AWS 側にリソースは作られません。

画像 1 枚あたり、次の 5 ステップで処理します。

| ステップ | 内容 | 目的 |
|---|---|---|
| 1 12桁スキャン | 文字起こしして、正規表現で 12 桁の数字を拾う | 判定をモデルに委ねない |
| 2 スクリーニング | 認証情報があるかどうかだけを判定 | 無関係な画像を早期に除外してコストを抑える |
| 3 検出 | 認証情報ごとのバウンディングボックスを取得 | `[0, 1000]` の正規化座標で返る |
| 4 マスク | Pillow で塗り潰す | 座標を実寸へ変換し、パディングを付与 |
| 5 再検証 | マスク済み画像を再チェック | 疑わしいものは実行末尾に一覧表示 |

検出結果は実行のたびに揺らぎます。1 回の検出パスですべてを拾える保証はなく、
ステップ 1 と 5 はそのための仕組みです。

### 12 桁の数字の扱い

長い識別子に埋め込まれた 12 桁の数字は、検出から漏れやすいという性質があります。
「探せ」と指示しても、モデルは `arn:aws:iam::123456789012:user/foo` を 1 つの識別子と
して扱い、その中の数字の並びを見ません。詳細パネルに単独で表示されたアカウントIDでも
同じことが起き、検出パスが単純に見落とします。

そこで判定をモデルに委ねません。画像を文字起こしし、**12 桁かどうかは正規表現で判定**
します。該当した行は値だけを切り出さず、まるごとマスクします。範囲は広くなりますが
確実です。

文字起こし自体も実行のたびに揺らぐため、既定で 3 回実行して結果を重ね合わせます
（`--ocr-passes`）。このパスを省略するには `--no-digit-scan` を指定します。

### 本当に漏れたものを見分ける

マスク済み画像を再検証に渡すと、Nova はマスクの下にあったはずの値を文脈から推測して
「まだ読める」と報告してくることがあります。プロンプトで禁止しても止まりません。

そのため再検証でも座標を返させ、**報告された位置がマスク領域の内側にある場合は推測と
みなして無視**します。マスクがずれて本当に読めてしまっている場合は領域の外側に出るため、
正しく検知できます。

### 処理を日本国内に閉じる

既定のモデルIDは `jp.amazon.nova-2-lite-v1:0` です。

Nova 2 Lite はオンデマンド呼び出しに対応していないため、推論プロファイル経由で呼び出す
必要があります。`jp.` プロファイルは `ap-northeast-1` と `ap-northeast-3` にのみ
ルーティングされるため、処理を日本国内に閉じられます。

グローバルにルーティングする場合は `--model global.amazon.nova-2-lite-v1:0` を指定します。

### 画像は原寸のまま送信する

画像入力は解像度によらず**一律 230 トークン**で課金されます
（[Multimodal understanding](https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-multimodal-models.html)）。
縮小してもコストは下がらず、小さい文字を読みにくくするだけなので、画像はそのまま
送信します。リクエストに収まらない大きさの画像だけ、収まるところまで縮小します。

## 前提条件

- Python 3.10 以上
- Amazon Bedrock で Nova 2 Lite のモデルアクセスが有効であること
- 以下の権限を持つ AWS 認証情報

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

プロファイル経由の呼び出しになるため、推論プロファイルと基盤モデルの両方を許可する
必要があります。

認証情報は boto3 の通常の解決順序（環境変数、`~/.aws/credentials`、IAM ロール等）で
読み込まれます。

## インストール

```bash
git clone https://github.com/furuya02/mask-tool.git
cd mask-tool
pip install -e .
```

pip でインストールすると、`mask-tool` コマンドがグローバルに利用可能になります。

## 使い方

画像のあるディレクトリに移動して実行します。

```bash
mask-tool
```

ファイル名が数字のみの PNG（`001.png`, `002.png` ...）がすべて処理されます。
実行例:

```
Scanning: /path/to/screenshots

Found 3 images:

  [IMG] 001.png
  [IMG] 002.png
  [IMG] 003.png

Model:   jp.amazon.nova-2-lite-v1:0 (ap-northeast-1)
Style:   pixelate
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

`[OK]` はマスク済み、`[--]` は対象なし、`[!!]` は要確認です。要確認が 1 件でもあると
終了コードは 1 になります。

### オプション

```
usage: mask-tool [-h] [-d DIRECTORY] [-s {pixelate,blur,black}] [-n]
                 [--ocr-passes OCR_PASSES] [--no-digit-scan] [--no-screen]
                 [--no-verify] [--padding-x PADDING_X] [--padding-y PADDING_Y]
                 [--max-tokens MAX_TOKENS] [--region REGION] [--model MODEL] [-v]
```

| オプション | 既定値 | 説明 |
|---|---|---|
| `-d`, `--directory` | カレント | 対象ディレクトリ |
| `-s`, `--style` | `pixelate` | 塗り潰しの方式（`pixelate` / `blur` / `black`） |
| `-n`, `--dry-run` | - | ファイルを変更せずに検出・再検証だけ行う |
| `--ocr-passes` | `3` | 12桁スキャンの文字起こし回数 |
| `--no-digit-scan` | - | 12桁スキャンを省略する |
| `--no-screen` | - | スクリーニングを省略して全画像を検出にかける |
| `--no-verify` | - | マスク後の再検証を省略する |
| `--padding-x` | `1.5` | 横方向のパディング（検出枠の高さに対する倍率） |
| `--padding-y` | `0.4` | 縦方向のパディング（同上） |
| `--max-tokens` | `4000` | 応答の上限トークン数 |
| `--region` | `ap-northeast-1` | リージョン |
| `--model` | `jp.amazon.nova-2-lite-v1:0` | モデルID（推論プロファイル） |
| `-v`, `--version` | - | バージョン表示 |

### 実行例

**ファイルを変更せずに結果だけ確認する:**

```bash
mask-tool --dry-run
```

**特定のディレクトリを処理する:**

```bash
mask-tool -d /path/to/screenshots
```

**単色で塗り潰す（最も確実）:**

```bash
mask-tool --style black
```

**確実性よりコストを優先する（1画像あたり1回の呼び出し）:**

```bash
mask-tool --no-digit-scan --no-screen --no-verify
```

### 塗り潰しの方式について

既定は `pixelate`（モザイク）です。`blur` はより柔らかい見た目、`black` は領域を単色で
置き換えます。

見た目より確実性が重要な場合は `black` を使ってください。単色で塗り潰すと元の画素が
残らないため、最も確実に隠せます。

### パディングについて

検出枠は実際の文字位置から少しずれるため、塗り潰す前にパディングを付けます。

パディングは画像サイズではなく**検出枠の高さ**に対する倍率で指定します。文字サイズに
追従するため、解像度の異なる画像でも同じ値が使えます。

長い文字列に埋め込まれた数字のように、値の前後に文字が詰まっている場合は、既定値では
隣接する数文字も覆われます。周囲の文字を残したい場合は `--padding-x` を下げてください。
ただし値の一部を覆いきれない可能性が上がります。

## バックアップ

元画像は上書き前に必ずコピーされます。

バックアップ先は `bak/` です。`bak/` が既に存在する場合は `bak2/`、`bak3/` ... と
順に採番されるため、同じディレクトリで繰り返し実行しても、以前の実行で退避した原本が
失われることはありません。

```
screenshots/
├── 001.png      <- マスク済み（上書き）
├── 002.png      <- マスク済み（上書き）
├── bak/         <- 1回目の実行: 手つかずの原本
│   ├── 001.png
│   └── 002.png
└── bak2/        <- 2回目の実行: 2回目実行前の状態
    ├── 001.png
    └── 002.png
```

## 応答を解析できない場合

Nova は必ずしもプロンプトで指定した形で応答しません。該当なしの場合に
`{"remaining": []}` ではなく `[]` だけを返すことがあるため、オブジェクトと配列の
両方を受け取れるようにしています。

それでも解析できない場合（トークン上限による打ち切り、JSON でない応答など）は、
理由とともに要確認として報告します。上限を引き上げて再実行してください。

```bash
mask-tool --max-tokens 8000
```

1 枚の失敗で残りの処理は止まりません。

## コスト

AWS 上で常時稼働するものはないため、待機コストは発生しません。課金は Bedrock の利用分
のみで、実行ごとの概算コストが末尾に表示されます。

既定では 1 画像あたり最大 6 回の呼び出し（文字起こし3回・スクリーニング・検出・再検証）
となり、**おおよそ 1 枚 $0.002** です。検出が多い画像ほど出力トークンが増えるため、
まず数枚で実際のコスト感を掴んでください。

東京リージョンの Nova 2 Lite の単価は、入力 $0.396 / 1M トークン、
出力 $3.311 / 1M トークンです。

## 制限事項

- 対象となるのはファイル名が数字のみの PNG（`001.png`、`12.png`）です。
  `screenshot_001.png` のようなファイルは対象外です。
- サブディレクトリは検索しません。
- 検出はモデルの判断に依存するため、すべての認証情報を捕捉できるわけではありません。
  同じ画像でも実行ごとに結果が変わることが確認されています。**公開前には必ず目視で
  確認してください。** このツールはその作業を減らすためのものであり、置き換えるもの
  ではありません。
- 画像は解析のため Amazon Bedrock に送信されます。既定の `jp.` プロファイルでは日本国内
  リージョンに閉じますが、対象データにとって許容できるかを確認してから使用してください。

## ライセンス

MIT License

## コントリビューション

Issue および Pull Request を歓迎します。
