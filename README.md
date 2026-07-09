# Hermes Content Radar

LinkedIn / note 投稿ネタを自動収集するための GitHub Actions 版 Hermes MVP です。

目的は、単なるAIニュース収集ではなく、松田幸一の文脈である **不動産実務 × AI × 白箱OSS × 公的データ × JSAI** に変換した投稿候補を毎回出すことです。

## 何が動くか

| Workflow | 実行タイミング | 役割 |
|---|---:|---|
| `Hermes Feedback Sync` | 毎日 07:57 JST | GitHub Issueコメントから採用/ボツを `memory/post_feedback.csv` に反映 |
| `Hermes Daily Tech Radar` | 毎日 08:17 JST | テック、生成AI、AIエージェント、OSS、不動産AI、公的データ、PDF/OCRの投稿ネタ収集 |
| `Hermes JSAI Deep Dive` | 火・金 09:17 JST | JSAI、人工知能学会、J-STAGE、論文・学会誌の深掘り |
| `Hermes Weekly Editorial Review` | 土曜 10:17 JST | 今週のネタを整理し、翌週の投稿カレンダーを作成 |

GitHub Actions の `schedule` はUTC指定なので、ワークフロー内ではJSTに換算したcronを使っています。

## 必要な設定

Repository Settings → Secrets and variables → Actions で以下を設定してください。

### Secrets

- `OPENAI_API_KEY`: OpenAI APIキー

### Variables 任意

- `OPENAI_MODEL`: 使用モデル。未設定時は `gpt-4.1-mini`。

## 出力先

```text
outputs/daily/YYYY-MM-DD.md
outputs/jsai/YYYY-MM-DD.md
outputs/weekly/YYYY-MM-DD.md
```

各実行後、同じ内容のGitHub Issueも作成されます。

## 学習の仕組み

Hermesは以下のメモリを毎回読み込みます。

```text
memory/profile.md
memory/editorial_policy.md
memory/post_feedback.csv
memory/source_scores.json
memory/banned_phrases.md
```

Issueコメントに以下のように書くと、翌朝の `Hermes Feedback Sync` が拾って `post_feedback.csv` に追記します。

```text
案1 採用。PDF行政資料ネタは強い。
案2 ボツ。AI一般論すぎる。
案3 保留。JSAI寄りでnote向き。
```

このCSVを次回以降のHermesが読み、ネタ選定を寄せていきます。

## 手動実行

GitHub Actions画面から各workflowを選び、`Run workflow` を押すと即時実行できます。

おすすめの初回順序:

1. `Hermes Feedback Sync`
2. `Hermes Daily Tech Radar`
3. `Hermes JSAI Deep Dive`
4. `Hermes Weekly Editorial Review`

## セキュリティ方針

- APIキーはGitHub Secretsに保存する
- 自動投稿はしない
- 投稿前に必ず人間が確認する
- クライアント情報や個人情報は入れない
- 誇大表現、鑑定評価・投資助言に見える表現は `memory/banned_phrases.md` で抑制する

## 将来拡張

- Notion / Obsidian への自動同期
- Issueコメントのより厳密な構造化
- 採用/ボツ履歴から source score を自動更新
- 週次AI参謀本部テンプレートへの自動反映
