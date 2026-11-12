# 認証・打刻を直列化し、要求IDと結果をSQLiteで管理します。
"""Serialized inference and durable operation receipts. No camera or GUI imports."""
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager


from remote_protocol import ServiceError


def operation_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ServiceError(400, "Invalid request ID") from None


class RecognitionService:
    def __init__(self, recognizer, attendance, todo, receipt_path):
        self.recognizer = recognizer
        self.attendance = attendance
        self.todo = todo
        self.receipt_path = str(receipt_path)
        self.lock = threading.Lock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.receipt_path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    # 以前の要求の結果を照会します。打刻そのものは再実行しません。
    def receipt(self, request_id):
        request_id = operation_id(request_id)
        with self._db() as db:
            row = db.execute("SELECT result FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise ServiceError(404, "Request not found; do not repeat the operation with a new ID")
        if row[0] is None:
            return {"request_id": request_id, "pending": True}
        return json.loads(row[0])

    # 判定結果を通信で返せる辞書に変換します。
    def _recognize(self, frame):
        start = time.monotonic()
        authenticated, name, similarity, box = self.recognizer.check(frame)
        return {
            "authenticated": bool(authenticated), "name": name if authenticated else "Unknown",
            "similarity": float(similarity), "box": None if box is None else [int(v) for v in box],
            "live_score": None if self.recognizer.live_score is None else float(self.recognizer.live_score),
            "width": int(frame.shape[1]), "height": int(frame.shape[0]),
            "inference_ms": round((time.monotonic() - start) * 1000, 1)
        }

    def recognize(self, frame):
        if not self.lock.acquire(blocking=False):
            raise ServiceError(503, "Server busy")
        try:
            return self._recognize(frame)
        finally:
            self.lock.release()

    # 要求IDで重複を確認し、新しい画像を再認証して打刻します。
    def stamp(self, request_id, kind, frame, jpeg):
        request_id = operation_id(request_id)
        if kind not in ("work", "break"):
            raise ServiceError(400, "Invalid operation")
        fingerprint = hashlib.sha256(kind.encode() + jpeg).hexdigest()
        if not self.lock.acquire(blocking=False):
            raise ServiceError(503, "Server busy; no operation accepted")
        try:
            with self._db() as db:
                row = db.execute("SELECT fingerprint, result FROM requests WHERE id=?", (request_id,)).fetchone()
                if row:
                    if row[0] != fingerprint:
                        raise ServiceError(409, "Request ID already used for different content")
                    return {"request_id": request_id, "pending": True} if row[1] is None else json.loads(row[1])
                # A crash between the CSV append and receipt commit is ambiguous.
                # Fail closed until an operator checks the CSV; never execute it again.
                if db.execute("SELECT 1 FROM requests WHERE result IS NULL LIMIT 1").fetchone():
                    raise ServiceError(409, "Unresolved operation exists; check the server CSV and receipts")
                db.execute("INSERT INTO requests VALUES (?, ?, NULL)", (request_id, fingerprint))
            # Any unexpected exception leaves the durable pending record intact.
            result = self._recognize(frame)
            result.update(request_id=request_id, pending=False, action=None, todo=None)
            if result["authenticated"]:
                name = result["name"]
                action = self.attendance.work_stamp(name) if kind == "work" else self.attendance.break_stamp(name)
                result.update(action=action, status=self.attendance.get_status(name))
                result["message"] = "打刻を保存しました。" if action else "現在の勤務状態ではこの操作はできません。"
                if action in ("出勤", "休憩終了"):
                    try:
                        result["todo"] = self.todo.read(name)
                    except (OSError, UnicodeError):
                        result["warning"] = "打刻は保存しましたが、Todoを読み込めませんでした。"
            else:
                result["message"] = "認証できないため、打刻しませんでした。"
            with self._db() as db:
                db.execute("UPDATE requests SET result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), request_id))
            return result
        finally:
            self.lock.release()

