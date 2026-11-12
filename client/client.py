# 自宅側の入口。撮影・JPEG送信・結果の描画・キー入力を担当します。
"""Run locally, outside VS Code's Remote SSH terminal."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import getpass
import ipaddress
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import cv2

from camera import Camera
from config import MAX_JPEG_BYTES
from instance_lock import instance_lock
from remote_protocol import get_token


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class ApiClient:
    def __init__(self, url, token, timeout=15):
        parsed = urllib.parse.urlsplit(url)
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            raise ValueError("Use the remote PC's Tailscale IPv4 address") from None
        if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or not (address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10"))):
            raise ValueError("Use http://<Tailscale IPv4>:8765, or loopback for testing")
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        # Keep private camera traffic out of system HTTP proxies.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    # 共通トークンを付けてHTTP要求を送り、JSON応答を読みます。
    def request(self, path, data=None, request_id=None):
        headers = {"Authorization": "Bearer " + self.token}
        if data is not None:
            headers["Content-Type"] = "image/jpeg"
        if request_id:
            headers["X-Request-ID"] = request_id
        request = urllib.request.Request(self.url + path, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read(8192).decode("utf-8")).get("error", "HTTP error")
            except (ValueError, UnicodeError):
                message = "HTTP error"
            raise ApiError(error.code, message) from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# 画像の縦横比を保って縮小し、送信用JPEGを生成します。
def encode_frame(frame, quality, max_width):
    height, width = frame.shape[:2]
    scale = min(1, max_width / width, 1080 / height)
    if scale < 1:
        frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))))
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok or len(encoded) > MAX_JPEG_BYTES:
        raise ValueError("Camera image could not be encoded within the size limit")
    return encoded.tobytes()


def show_stamp(result):
    print(result.get("message", ""))
    if result.get("action"):
        print(result["name"], result["action"], result.get("status", ""))
    if result.get("warning"):
        print(result["warning"])
    if result.get("todo") is not None:
        print("\n====================\n" + result["name"] + " Todo\n====================")
        print(result["todo"])
        print("====================\n")


# 起動引数を読み、必要な処理を初期化してメインループを開始します。
def main():
    parser = argparse.ArgumentParser(description="Local webcam client")
    parser.add_argument("--server", required=True, help="http://100.x.x.x:8765")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--quality", type=int, default=85)
    parser.add_argument("--max-width", type=int, default=960)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--image", help="Stage 1: recognize one image without opening the camera or stamping")
    args = parser.parse_args()
    if not (0.1 <= args.interval <= 60 and 40 <= args.quality <= 100 and 320 <= args.max_width <= 1920 and 1 <= args.timeout <= 120):
        parser.error("Invalid interval, quality, width or timeout")
    if not os.environ.get("FACE_API_TOKEN"):
        os.environ["FACE_API_TOKEN"] = getpass.getpass("FACE_API_TOKEN (hidden): ")
    api = ApiClient(args.server, get_token(), args.timeout)
    if args.image:
        import numpy as np
        frame = cv2.imdecode(np.frombuffer(Path(args.image).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("Image could not be loaded")
        print(json.dumps(api.request("/recognize", encode_frame(frame, args.quality, args.max_width)), ensure_ascii=False, indent=2))
        return
    # Store unresolved request IDs locally across client restarts, not images/tokens.
    state_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FaceAttendanceRemote"
    state_dir.mkdir(parents=True, exist_ok=True)
    import hashlib
    state_path = state_dir / (hashlib.sha256(api.url.encode()).hexdigest()[:16] + ".json")
    client_lock = instance_lock(state_path.with_suffix(".lock"))
    client_lock.__enter__()
    unresolved = json.loads(state_path.read_text(encoding="utf-8"))["request_id"] if state_path.exists() else None
    camera = None
    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    job = None
    capture_time = 0
    last_send = 0
    result = None
    result_time = 0
    result_capture_time = 0
    queued_stamp = None
    message = "CONNECTING"
    window = "Face Attendance - Local Camera"
    try:
        camera = Camera(args.camera)
        while True:
            frame = camera.read()
            if frame is None:
                raise RuntimeError("Camera frame could not be read")
            now = time.monotonic()
            if future is not None and future.done():
                try:
                    reply = future.result()
                    if job in ("stamp", "result"):
                        if reply.get("pending"):
                            message = "PENDING - checking saved result"
                        else:
                            # Keep receipt ID on disk until the response is confirmed.
                            state_path.unlink(missing_ok=True)
                            unresolved = None
                            show_stamp(reply)
                            message = "STAMP SAVED" if reply.get("action") else "STAMP NOT ACCEPTED"
                    else:
                        result, result_time = reply, now
                        result_capture_time = capture_time
                        message = "CONNECTED"
                except Exception as error:
                    result = None
                    message = "NETWORK / SERVER ERROR"
                    if isinstance(error, ApiError):
                        print(f"Server: {error.status} {error}")
                        # These responses explicitly mean this upload was not accepted.
                        if job == "stamp" and error.status in (400, 401, 404, 413, 503):
                            state_path.unlink(missing_ok=True)
                            unresolved = None
                    else:
                        print("通信できません。接続先・トークン・サーバー起動状態を確認してください。")
                    if unresolved:
                        print("打刻結果を確認中です。新しい打刻は停止しています。")
                        print("要求ID:", unresolved)
                future = None
                last_send = now
            if future is None:
                if unresolved and now - last_send >= 2:
                    job = "result"
                    future = pool.submit(api.request, "/result/" + unresolved)
                elif queued_stamp and not unresolved:
                    kind = queued_stamp
                    queued_stamp = None
                    request_id = str(uuid.uuid4())
                    payload = encode_frame(frame, args.quality, args.max_width)
                    # Persist before sending; a lost reply must not cause another stamp.
                    state_path.write_text(json.dumps({"request_id": request_id}), encoding="utf-8")
                    unresolved = request_id
                    job = "stamp"
                    result = None
                    message = "VERIFYING FOR STAMP"
                    future = pool.submit(api.request, "/stamp/" + kind, payload, request_id)
                elif not unresolved and now - last_send >= args.interval:
                    capture_time = now
                    job = "recognize"
                    future = pool.submit(api.request, "/recognize", encode_frame(frame, args.quality, args.max_width))
            display = frame.copy()
            # Display-only results remain visible after a slow remote response.
            # Stamping still requests independent recognition of a fresh frame.
            if result and now - result_time <= 3 and not unresolved and not queued_stamp:
                box = result.get("box")
                if box:
                    sx, sy = frame.shape[1] / result["width"], frame.shape[0] / result["height"]
                    x1, y1, x2, y2 = [round(v * (sx if i % 2 == 0 else sy)) for i, v in enumerate(box)]
                    color = (0, 255, 0) if result["authenticated"] else (0, 0, 255)
                    cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(display, f'{result["name"]} {result["similarity"]:.3f}', (x1, max(20, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            lines = ["SPACE: Clock in/out   ENTER: Break start/end", "Q / ESC: Quit", message]
            if result and not unresolved and not queued_stamp:
                age = now - result_capture_time
                inference = result.get("inference_ms", 0) / 1000
                lines.append(f'Last result: {result["name"]} | image {age:.1f}s ago | CPU {inference:.2f}s')
                if now - result_time > 3:
                    lines.append("Waiting for next result - old box hidden")
                elif not result.get("box"):
                    lines.append("No face detected in last processed image")
            if unresolved:
                lines.append("Waiting for stamp receipt - new stamps disabled")
            elif queued_stamp:
                lines.append("Stamp queued - capturing a fresh frame when ready")
            for i, line in enumerate(lines):
                point = (15, 28 + i * 26)
                cv2.putText(display, line, point, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            cv2.imshow(window, display)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key in (32, 13) and not unresolved and queued_stamp is None:
                queued_stamp = "work" if key == 32 else "break"
    finally:
        if camera is not None:
            camera.release()
        cv2.destroyAllWindows()
        pool.shutdown(wait=True, cancel_futures=True)
        client_lock.__exit__(None, None, None)
        if unresolved:
            print("未確認の打刻があります。次回起動時に同じ要求IDで結果を照会します。")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print("起動・実行エラー:", error)
        raise SystemExit(1)
