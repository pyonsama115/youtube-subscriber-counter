# YouTube Subscriber Counter Widget

<p align="center">
  <img src="icon.png" width="96" alt="app icon">
</p>

デスクトップに常駐する、軽量でおしゃれな YouTube チャンネル登録者数ウィジェットです。
Windows 内蔵の **WebView2**(pywebview)で動くため、Electron のような巨大ランタイムは不要。メモリ使用量は数十 MB 程度です。

## 機能

| 機能 | 説明 |
|---|---|
| 🎛 メインカード | グラスモーフィズム調のダークカードに、アバター / チャンネル名 / 登録者数 / 総再生数 / 動画数 / 更新時刻を表示 |
| 🎰 オドメーター | 登録者数はスロットのように回転するデジタルカウンター。増減時は `+1` / `-1` チップがふわっと表示 |
| 📊 タスクバー・ミニピル | 天気ウィジェット風の「▶ 登録者数」ピルを常駐表示。**ドラッグで好きな位置へ移動**でき、位置は保存されます |
| 📌 最前面固定 | カードにマウスを乗せて出るピンボタンで ON/OFF。固定中はミニピルを自動で隠します |
| 🖱 クリック操作 | ミニピルをクリック → カードの表示/非表示。アバター / チャンネル名をクリック → ブラウザでチャンネルを開く |
| 🚀 自動起動 | 設定画面のチェックボックスで「PC起動時に自動起動」を ON/OFF(スタートアップに登録/解除) |
| 📍 位置記憶 | カード・ミニピルともドラッグ移動でき、位置は `config.json` に自動保存 |

タスクバーボタンや通知領域アイコンは出しません。カードとミニピルだけの、ウィジェットらしい佇まいです。

## 動作環境

- Windows 10 / 11(WebView2 ランタイム — Windows 11 には標準搭載)
- exe 版を使う場合 Python は不要

## インストール

### 方法1: exe(かんたん)

1. [Releases](../../releases) から `YouTubeSubCounter.exe` をダウンロード
2. 好きなフォルダに置いてダブルクリック

設定ファイル `config.json` は exe と同じフォルダに作成されます。

### 方法2: ソースから

```powershell
git clone https://github.com/pyonsama115/youtube-subscriber-counter.git
cd youtube-subscriber-counter
pip install pywebview
python widget.py          # または「YouTube Counter.vbs」(コンソール非表示)
```

## 初期設定

初回起動時に設定画面が開きます。

1. **YouTube Data API キー**(無料)を入力
   1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
   2. 「APIとサービス」→「ライブラリ」→ **YouTube Data API v3** を有効化
   3. 「認証情報」→「+認証情報を作成」→「APIキー」
2. **チャンネル**: `@ハンドル`(例 `@HikakinTV`)または `UC…` で始まるチャンネルID
3. **更新間隔**: 秒単位(最小15秒 / 既定30秒)
4. **PC起動時に自動起動**: チェックで Windows スタートアップに登録
5. 「保存して開始」

設定は後からカード右上の ⚙ でいつでも変更できます。

## 使い方

| 操作 | 動作 |
|---|---|
| カードをドラッグ | 移動(位置は自動保存) |
| カードにホバー → 📌 | 最前面固定の ON/OFF |
| カードにホバー → ⚙ | 設定を開く |
| カードにホバー → ✕ | 終了 |
| ミニピルをクリック | カードの表示/非表示 |
| ミニピルをドラッグ | ピルを好きな位置へ移動 |
| アバター/チャンネル名をクリック | チャンネルページを開く |

## exe のビルド

```powershell
pip install pyinstaller pillow
python make_icon.py   # icon.ico を再生成する場合
pyinstaller --noconfirm --onefile --windowed --name "YouTubeSubCounter" --icon icon.ico --add-data "ui;ui" widget.py
```

`dist/YouTubeSubCounter.exe` が生成されます。
`v*` タグを push すると GitHub Actions が自動でビルドし、Release に exe を添付します。

## 制限事項

- YouTube API の仕様上、登録者数が 1,000 人以上のチャンネルは **3桁の有効数字に丸めた値**(例: 12,300)になります。1,000 人未満は正確な値です
- API クォータは 1 日 10,000 ユニット。更新間隔 30 秒でも約 2,880 ユニット/日なので十分収まります
- 登録者数を非公開にしているチャンネルは「登録者数は非公開です」と表示されます

## ファイル構成

```
widget.py            # 本体(ウィンドウ管理 / YouTube API / 自動起動)
ui/index.html        # メインカード UI(単一ファイル、依存なし)
ui/mini.html         # タスクバー・ミニピル UI
make_icon.py         # アプリアイコン生成スクリプト
YouTube Counter.vbs  # コンソール非表示ランチャー(ソース実行用)
config.json          # 設定(自動生成 / gitignore 済み)
```

> ⚠️ `config.json` には API キーが含まれます。公開リポジトリにコミットしないでください(`.gitignore` 済み)。

## License

[MIT](LICENSE)
