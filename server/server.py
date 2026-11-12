# 大学側の入口。HTTP受付、トークン照合、画像復元、応答を担当します。
"""Run on the remote Windows PC. Uses CPU models; never opens a camera/window."""
import argparse
import hmac
import ipaddress
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import BASE_DIR, REGISTERED_FACES, TODO_FILES, FACE_THRESHOLD, LIVENESS_THRESHOLD, MAX_JPEG_BYTES
from remote_protocol import get_token, jpeg_size
from remote_service import RecognitionService, ServiceError
from instance_lock import instance_lock


class ApiServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, address, service, token):
        self.service = service
        self.token = token
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Do not log credentials, image data or recognition identities.

    def _send(self, status, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # 認証・経路・画像を確認して、サービスの処理へ渡します。
    def _handle(self, post=False):
        try:
            supplied = self.headers.get("Authorization", "").encode("utf-8")
            expected = ("Bearer " + self.server.token).encode("ascii")
            if not hmac.compare_digest(supplied, expected):
                raise ServiceError(401, "Unauthorized")
            service = self.server.service
            if not post:
                if self.path == "/health":
                    result = {"ready": True, "device": "cpu"}
                elif self.path.startswith("/result/"):
                    result = service.receipt(self.path[len("/result/"):])
                else:
                    raise ServiceError(404, "Not found")
            else:
                if self.path not in ("/recognize", "/stamp/work", "/stamp/break"):
                    raise ServiceError(404, "Not found")
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type") != "image/jpeg":
                    raise ServiceError(400, "Send image/jpeg with Content-Length")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise ServiceError(400, "Invalid Content-Length") from None
                if not 0 < length <= MAX_JPEG_BYTES:
                    raise ServiceError(413, "JPEG exceeds size limit")
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ServiceError(400, "Incomplete image")
                width, height = jpeg_size(data)
                import cv2
                import numpy as np
                frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None or frame.shape[:2] != (height, width):
                    raise ServiceError(400, "Invalid JPEG image")
                if self.path == "/recognize":
                    result = service.recognize(frame)
                else:
                    result = service.stamp(self.headers.get("X-Request-ID"), self.path.rsplit("/", 1)[1], frame, data)
            self._send(200, result)
        except ServiceError as error:
            self._send(error.status, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            # A pending stamp is intentionally preserved if completion is uncertain.
            try:
                self._send(500, {"error": "Server processing failed; check operation result before another stamp"})
            except OSError:
                pass
            print("Request failed. Check model/data access; unfinished stamps require CSV review.")

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle(post=True)


# 起動引数を読み、必要な処理を初期化してメインループを開始します。
def main():
    parser = argparse.ArgumentParser(description="CPU face attendance server")
    parser.add_argument("--host", default="127.0.0.1", help="Remote PC's Tailscale IPv4 address")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    address = ipaddress.ip_address(args.host)
    if not (address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10")):
        parser.error("Bind only to loopback or your Tailscale IPv4 address")
    token = get_token()
    # Bind before loading models/data so a duplicate launch fails immediately.
    with instance_lock(BASE_DIR / "data" / "remote_server.lock"), ApiServer((args.host, args.port), None, token) as server:
        from face_recognition import FaceRecognizer
        from attendance import AttendanceManager
        from todo import TodoViewer
        print("Loading CPU models...")
        recognizer = FaceRecognizer(REGISTERED_FACES, FACE_THRESHOLD, LIVENESS_THRESHOLD)
        attendance = AttendanceManager(list(REGISTERED_FACES), BASE_DIR / "data" / "attendance.csv")
        todo = TodoViewer(BASE_DIR / "todo", TODO_FILES)
        server.service = RecognitionService(recognizer, attendance, todo, BASE_DIR / "data" / "remote_requests.sqlite3")
        print(f"Ready: http://{args.host}:{args.port} (CPU). Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Stopping after active requests finish...")


if __name__ == "__main__":
    main()
