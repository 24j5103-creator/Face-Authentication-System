# 顔認証・出退勤管理（公開用・分離構成）

自宅PCへclientフォルダ、大学などのリモートPCへserverフォルダを配置します。
仕様書は親フォルダ直下の **SPECIFICATION.md** です。秘密情報や実際の顔写真・打刻履歴は含みません。

```text
face-attendance-github/
├── README.md
├── SPECIFICATION.md
├── .gitignore
├── test_remote.py          # 開発者向け統合テスト
├── client/                 # 自宅PCへコピー
│   ├── client.py
│   ├── camera.py
│   ├── config.py
│   ├── remote_protocol.py
│   ├── instance_lock.py
│   └── requirements.txt
└── server/                 # 大学PCへコピー
    ├── server.py
    ├── remote_service.py
    ├── remote_protocol.py
    ├── face_recognition.py
    ├── Liveness_detection.py
    ├── attendance.py
    ├── todo.py
    ├── config.py
    ├── instance_lock.py
    ├── receipt_admin.py
    ├── requirements.txt
    ├── registered/         # 文字だけのダミー画像
    ├── todo/               # 架空のTodo
    ├── data/               # 実行時の保存先（配布時は空）
    ├── examples/           # 架空のCSV例
    ├── live_test_assets/   # 第三者コード・ライセンス。重みは別途
    └── singleversion/     # 単体版 main.py / camera.py
```

## 実行準備
以下は新しく配布したフォルダの手順です。既存の運用フォルダは変更していません。

### 自宅PC：client内で実行
```powershell
py -3.13 -m venv .venv-client
.\.venv-client\Scripts\python.exe -m pip install -r requirements.txt
```
既存のOpenCV・NumPy環境も使用できます。

### 大学PC：server内で実行
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```
InsightFaceのインストールでビルドツールが必要になる場合があります。既存の動作する環境があればそれを使用してください。

ダミー写真には顔がないため、そのままでは認証器の初期化が成功しません。実運用には1顔の写真と、別途取得したモデルが必要です。
- 顔写真の対応はserver/config.pyで設定します。単体版はserver/singleversion/main.pyにも設定があります。
- InsightFaceモデル：ユーザー領域の ~/.insightface/models/buffalo_l。
- ライブネス重み：server/live_test_assets/2.7_80x80_MiniFASNetV2.pth および 4_0_0_80x80_MiniFASNetV1SE.pth。
- 個人写真は公開しないでください。追跡済みダミー画像を同名で置換した変更は.gitignoreで防げないため、別名の非公開画像を使ってください。

## 起動
自宅の端末AでSSHを開始します。university-pcは自身のSSH設定でホスト・ユーザー・鍵を定義した別名です。
```powershell
ssh -o ExitOnForwardFailure=yes -L 127.0.0.1:18765:127.0.0.1:8765 university-pc
```
接続先の大学PCでserverフォルダへ移動し、PowerShellで実行します。
```powershell
$secret = Read-Host 'Token' -AsSecureString
$env:FACE_API_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
Remove-Variable secret
.\.venv\Scripts\python.exe -B server.py --host 127.0.0.1 --port 8765
```
自分で生成したランダムなASCII英数字32文字以上を共通トークンとして使います。Ready表示後も端末Aを開いたままにします。

自宅の端末Bでclientフォルダへ移動し、実行します。
```powershell
.\.venv-client\Scripts\python.exe -B client.py --server http://127.0.0.1:18765
```
同じ共通トークンを入力します。Spaceで出退勤、Enterで休憩、Q/Escで終了します。打刻後は端末の保存結果を確認してください。

## 開発用テスト
リポジトリ全体を取得した環境で、親フォルダから実行します。
```powershell
.\server\.venv\Scripts\python.exe -B -m unittest test_remote -v
```
単独配布したclient/serverには親のテストファイルは不要です。通常の実行に兄弟フォルダの参照はありません。

## 単体版
大学側サーバーを止め、カメラのあるPCでserverフォルダから実行します。
```powershell
.\.venv\Scripts\python.exe -B singleversion/main.py
```
単体版はserver内の共通処理・データを使用します。singleversionだけでの実行はできません。

## 第三者コード・ライセンス
server/live_test_assets内のMiniFASNet.pyとgenerate_patches.pyの元の表記・LICENSE・SOURCE.txtを保持しています。モデル重みは同梱しません。モデルの利用条件はコードと別に確認してください。プロジェクト全体に新しいライセンスは付与していません。
