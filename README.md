# ChatMap Agent

ChatMap Agent は、長い会話ログを Gemini API で解析し、要約・時系列・決定事項・未解決の疑問・次のアクション・話題の流れを整理する Tkinter 製デスクトップアプリです。

解析結果は、Markdown レポートや Mermaid グラフとして確認できます。さらに、検索・フィルタ・ノード詳細表示つきの ChatMap HTML ビューアとして保存できます。

## Features

- 会話ログのノイズ除去とチャンク分割
- Gemini API によるチャンク要約と全体マップ生成
- 要約、時系列、決定事項、未解決の疑問、次のアクションの表示
- Mermaid グラフの生成
- Markdown レポートの保存
- 検索・フィルタ・詳細ペインつき ChatMap HTML ビューアの保存
- Gemini の一時的な `503 UNAVAILABLE` / high demand エラーへの自動リトライ

## Requirements

- Python 3.10 以降
- Gemini API キー

Tkinter は多くの Python 環境に同梱されています。Linux など一部環境では、OS のパッケージマネージャーで Tkinter を追加インストールする必要があります。

## Setup

```powershell
cd chatmap
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

`.env` を開き、Gemini API キーを設定します。

```env
GEMINI_API_KEY=your_actual_api_key
```

`.env` を使わず、アプリ画面上部の入力欄に API キーを直接入力することもできます。

## Run

```powershell
python app_tkinter.py
```

## Basic Workflow

1. 左側の入力欄に会話ログを貼り付ける、または `.txt` / `.md` ファイルを開きます。
2. `解析する` を押します。
3. 要約、Mermaid、JSON、Chunks タブで結果を確認します。
4. `Markdown保存` または `ChatMap HTML保存` で結果を保存します。

## Privacy

このリポジトリには、本物の API キーや固定の個人情報は含めていません。`.env` は `.gitignore` で除外されています。

ただし、アプリに貼り付けた会話ログや開いたログファイルの内容は、解析のため Gemini API に送信されます。個人情報、秘密情報、社外秘の情報を含むログを扱う場合は、事前に削除・マスクしてから使ってください。

保存される Markdown / HTML / JSON 表示内容にも、入力ログから抽出された情報が含まれる可能性があります。共有前に内容を確認してください。

## Notes

- `ChatMap HTML保存` で作成する HTML は Mermaid を CDN から読み込みます。オフラインで使いたい場合は、Mermaid をローカルに同梱する変更が必要です。
- Gemini のモデルが混み合っている場合、`503 UNAVAILABLE` が返ることがあります。アプリは自動でリトライしますが、失敗が続く場合は少し待つか、画面上部の `Model` 欄で別モデルを指定してください。
- `.env` はコミットしないでください。

## License

ライセンスはまだ未設定です。第三者に再利用や改変を許可したい場合は、公開前に `LICENSE` ファイルを追加してください。
