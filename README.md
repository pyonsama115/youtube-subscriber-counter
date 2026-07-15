# YouTube Subscriber Counter Widget

デスクトップに常駐する軽量な YouTube チャンネル登録者数ウィジェット。
Windows 内蔵の WebView2(pywebview)で動くため、Electron のような巨大ランタイム不要でメモリ使用量は数十 MB 程度です。

## 機能

- 🎛 グラスモーフィズム調のダークカード(アバター / チャンネル名 / 登録者数 / 再生数 / 動画数)
- 🎰 オドメーター式に回転する数字アニメーション、増減時は `+1` チップを表示
- 📌 最前面固定のON/OFF切り替え(OFF のときはタスクバーにアイコン表示)
- 🔔 通知領域(時計の横)に登録者数バッジを常駐表示 — タスクバーの天気ウィジェット風
  - 左クリック: カードの表示/非表示、右クリック: メニュー(終了など)
- 🔗 アバター/チャンネル名クリックでチャンネルページを開く
- 📍 ウィンドウ位置の自動保存、ドラッグで移動

## 必要なもの

- Python 3.x + `pywebview`(`pip install pywebview`)
- **YouTube Data API v3 の API キー**(無料)
  1. https://console.cloud.google.com/ でプロジェクトを作成
  2. 「APIとサービス」→「ライブラリ」→ **YouTube Data API v3** を有効化
  3. 「認証情報」→「APIキーを作成」

## 使い方

1. `YouTube Counter.vbs` をダブルクリック(コンソールなしで起動)
2. 初回起動時に設定画面が出るので入力:
   - **APIキー**
   - **チャンネル**: `@ハンドル`(例 `@HikakinTV`)または `UC…` で始まるチャンネルID
   - **更新間隔**: 秒(最小15秒 / 既定30秒)
3. 「保存して開始」で表示開始

> 💡 トレイのバッジが見えない場合は、タスクバー右端の「^」(隠れているインジケーター)から
> アイコンをタスクバーへドラッグすると常時表示になります。

## exe へのパッケージング

```powershell
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "YouTubeSubCounter" --add-data "ui;ui" widget.py
```

`dist/YouTubeSubCounter.exe` が単体で動きます(`config.json` は exe と同じフォルダに作成されます)。

## 自動起動(Windows スタートアップ)

`Win + R` → `shell:startup` → 開いたフォルダに `YouTube Counter.vbs`(または exe)のショートカットを置く。

## 注意

YouTube API の仕様上、登録者数が 1,000 人を超えると **3桁の有効数字に丸められた値**
(例: 12,300)しか取得できません。1,000 人未満は正確な値が表示されます。
更新間隔 30 秒でも API クォータ(1日 10,000 ユニット)内に十分収まります(約 2,880 ユニット/日)。

## 設定ファイル

`config.json` に APIキー・チャンネル・ウィンドウ位置が保存されます。**API キーを含むため公開リポジトリにコミットしないでください**(`.gitignore` 済み)。

## License

MIT
