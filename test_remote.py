# 架空データと一時フォルダで通信・打刻・結果照会を検証します。
import json
from pathlib import Path
import sys
# 統合テスト時のみ両方の実装を読みます。通常運用は各フォルダ単独です。
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "server"))
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import Future
import client
import uuid

import cv2
import numpy as np

from attendance import AttendanceManager
from client import ApiClient, ApiError, encode_frame
from instance_lock import instance_lock
from remote_protocol import jpeg_size
from remote_service import RecognitionService, ServiceError
from server import ApiServer
from todo import TodoViewer


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.recognizer = Mock(live_score=0.9)
        self.recognizer.check.return_value = (True, "Alice", 0.8, (0, 0, 64, 64))
        self.attendance = AttendanceManager(["Alice", "Bob"], self.root / "attendance.csv")
        (self.root / "alice.txt").write_text("今日の課題", encoding="utf-8")
        self.todo = TodoViewer(self.root, {"Alice": "alice.txt"})
        self.service = RecognitionService(self.recognizer, self.attendance, self.todo, self.root / "receipts.sqlite3")
        self.frame = np.zeros((80, 100, 3), dtype=np.uint8)
        self.jpeg = encode_frame(self.frame, 85, 960)

    def tearDown(self):
        self.temp.cleanup()

    # 要求IDで重複を確認し、新しい画像を再認証して打刻します。
    def stamp(self, kind="work", request_id=None):
        return self.service.stamp(request_id or str(uuid.uuid4()), kind, self.frame, self.jpeg)

    def test_full_shift_and_todo(self):
        self.assertEqual(self.stamp()["todo"], "今日の課題")
        self.assertEqual(self.stamp("break")["action"], "休憩開始")
        self.assertEqual(self.stamp("break")["todo"], "今日の課題")
        self.assertEqual(self.stamp()["action"], "退勤")
        self.assertIsNone(self.stamp()["action"])

    def test_duplicate_survives_restart(self):
        request_id = str(uuid.uuid4())
        first = self.stamp(request_id=request_id)
        restored = AttendanceManager(["Alice"], self.root / "attendance.csv")
        service = RecognitionService(self.recognizer, restored, self.todo, self.root / "receipts.sqlite3")
        self.assertEqual(service.stamp(request_id, "work", self.frame, self.jpeg), first)
        self.assertEqual(restored.get_status("Alice"), "勤務中")
        self.assertEqual(self.recognizer.check.call_count, 1)

    def test_id_conflict(self):
        request_id = str(uuid.uuid4())
        self.stamp(request_id=request_id)
        with self.assertRaises(ServiceError) as caught:
            self.stamp("break", request_id)
        self.assertEqual(caught.exception.status, 409)

    def test_fresh_recognition_rejects_changed_face(self):
        self.assertTrue(self.service.recognize(self.frame)["authenticated"])
        self.recognizer.check.return_value = (False, "Unknown", 0.3, None)
        self.assertIsNone(self.stamp()["action"])
        self.assertEqual(self.attendance.get_status("Alice"), "未出勤")

    def test_failed_save_is_uncertain_and_blocks_new_stamps(self):
        self.attendance.save = Mock(side_effect=OSError("disk full"))
        request_id = str(uuid.uuid4())
        with self.assertRaises(OSError):
            self.stamp(request_id=request_id)
        self.assertTrue(self.service.receipt(request_id)["pending"])
        self.assertEqual(self.attendance.get_status("Alice"), "未出勤")
        with self.assertRaises(ServiceError):
            self.stamp()

    def test_crash_after_csv_does_not_repeat_stamp(self):
        original = self.attendance.work_stamp
        def crash(name):
            original(name)
            raise RuntimeError("simulated termination before receipt")
        self.attendance.work_stamp = crash
        request_id = str(uuid.uuid4())
        with self.assertRaises(RuntimeError):
            self.stamp(request_id=request_id)
        self.assertTrue(self.stamp(request_id=request_id)["pending"])
        self.assertEqual(self.attendance.get_status("Alice"), "勤務中")

    def test_missing_todo_does_not_undo_stamp(self):
        (self.root / "alice.txt").unlink()
        result = self.stamp()
        self.assertEqual(result["action"], "出勤")
        self.assertIn("warning", result)

    def test_busy_is_rejected(self):
        with self.service.lock:
            with self.assertRaises(ServiceError) as caught:
                self.service.recognize(self.frame)
        self.assertEqual(caught.exception.status, 503)

    def test_image_validation_and_resize(self):
        self.assertEqual(jpeg_size(self.jpeg), (100, 80))
        with self.assertRaises(ServiceError):
            jpeg_size(b"not an image")
        large = np.zeros((1200, 2000, 3), dtype=np.uint8)
        self.assertEqual(jpeg_size(encode_frame(large, 85, 960)), (960, 576))
        _, encoded = cv2.imencode(".jpg", large)
        with self.assertRaises(ServiceError):
            jpeg_size(encoded.tobytes())

    def test_instance_lock(self):
        path = self.root / "server.lock"
        with instance_lock(path):
            with self.assertRaises(RuntimeError):
                with instance_lock(path):
                    pass

    def test_real_http_jpeg_auth_stamp_and_receipt(self):
        token = "a" * 64
        with ApiServer(("127.0.0.1", 0), self.service, token) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}"
                api = ApiClient(url, token)
                self.assertTrue(api.request("/health")["ready"])
                with self.assertRaises(ApiError) as caught:
                    ApiClient(url, "b" * 64).request("/health")
                self.assertEqual(caught.exception.status, 401)
                self.assertTrue(api.request("/recognize", self.jpeg)["authenticated"])
                request_id = str(uuid.uuid4())
                result = api.request("/stamp/work", self.jpeg, request_id)
                self.assertEqual(result["action"], "出勤")
                self.assertEqual(api.request("/result/" + request_id), result)
                self.assertEqual(api.request("/stamp/work", self.jpeg, request_id), result)
                with self.assertRaises(ApiError):
                    api.request("/recognize", b"bad JPEG")
            finally:
                server.shutdown()
                thread.join()

    def test_client_stamps_with_fresh_unannotated_frame_and_cleans_up(self):
        calls = []
        class ImmediatePool:
            def __init__(self, **kwargs):
                pass
            def submit(self, fn, *args):
                future = Future()
                try:
                    future.set_result(fn(*args))
                except Exception as error:
                    future.set_exception(error)
                return future
            def shutdown(self, **kwargs):
                pass
        # 共通トークンを付けてHTTP要求を送り、JSON応答を読みます。
        def request(path, data=None, request_id=None):
            calls.append((path, data))
            if path.startswith("/stamp/"):
                return {"pending": False, "action": "出勤", "name": "Alice", "message": "saved"}
            return {"authenticated": True, "name": "Alice", "similarity": 0.8, "box": None}
        camera = Mock()
        camera.read.side_effect = [np.full((80, 100, 3), v, np.uint8) for v in (20, 90, 160)]
        api = Mock(url="http://127.0.0.1:8765")
        api.request.side_effect = request
        with patch.dict("os.environ", {"LOCALAPPDATA": str(self.root), "FACE_API_TOKEN": "a" * 64}), \
                patch("sys.argv", ["client.py", "--server", api.url]), \
                patch.object(client, "ApiClient", return_value=api), \
                patch.object(client, "Camera", return_value=camera), \
                patch.object(client, "ThreadPoolExecutor", ImmediatePool), \
                patch.object(cv2, "imshow"), patch.object(cv2, "destroyAllWindows") as destroy, \
                patch.object(cv2, "getWindowProperty", return_value=1), \
                patch.object(cv2, "waitKey", side_effect=[32, -1, ord("q")]):
            client.main()
        stamps = [data for path, data in calls if path == "/stamp/work"]
        self.assertEqual(len(stamps), 1)
        decoded = cv2.imdecode(np.frombuffer(stamps[0], np.uint8), cv2.IMREAD_COLOR)
        self.assertAlmostEqual(float(decoded.mean()), 90, delta=1)
        camera.release.assert_called_once()
        destroy.assert_called_once()
        self.assertEqual(list((self.root / "FaceAttendanceRemote").glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
