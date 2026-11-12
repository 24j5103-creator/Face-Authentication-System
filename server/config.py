# 登録写真・Todo・認証しきい値の設定です。公開用のユーザー名を使用します。
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REGISTERED_FACES = {
    "userA": [str(BASE_DIR / "registered" / "userA.jpg"), str(BASE_DIR / "registered" / "userA_mask.jpg")],
    "userB": [str(BASE_DIR / "registered" / "userB.jpg")],
    "userC": [str(BASE_DIR / "registered" / "userC.jpg")]
}
TODO_FILES = {"userA": "userA.txt", "userB": "userB.txt", "userC": "userC.txt"}
FACE_THRESHOLD = 0.6
LIVENESS_THRESHOLD = 0.8
MAX_JPEG_BYTES = 2 * 1024 * 1024

