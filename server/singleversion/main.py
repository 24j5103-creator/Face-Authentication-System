# 単体版の入口。親フォルダの共通モジュールとデータを使用します。
from pathlib import Path
import sys

# Shared modules and data remain in the project root.
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import cv2
from camera import Camera
from face_recognition import FaceRecognizer
from attendance import AttendanceManager
from todo import TodoViewer


def main():
    #Register face photos (multiple photos supported)
    registered_faces = {
        "userA": [
            str(BASE_DIR / "registered" / "userA.jpg"),
            str(BASE_DIR / "registered" / "userA_mask.jpg")
        ],
        "userB": [
            str(BASE_DIR / "registered" / "userB.jpg"),
        ],
        "userC": [
            str(BASE_DIR / "registered" / "userC.jpg")
        ]
    }
    # Prepare models and saved state before opening the camera.
    recognizer = FaceRecognizer(registered_faces, threshold=0.6)
    attendance = AttendanceManager(list(registered_faces), BASE_DIR / "data" / "attendance.csv")
    todo = TodoViewer(BASE_DIR / "todo", {
        "userA": "userA.txt",
        "userB": "userB.txt",
        "userC": "userC.txt"
    })
    camera = None
    frame_count = 0
    authenticated, name, similarity, box = False, "Unknown", 0.0, None
    window = "Face Attendance System"
    try:
        camera = Camera()
        while True:
            frame = camera.read()
            if frame is None:
                print("カメラ画像を取得できませんでした。")
                break
            # Periodic results are used for display only.
            if frame_count % 10 == 0:
                authenticated, name, similarity, box = recognizer.check(frame)
            frame_count += 1
            if box is not None:
                x1, y1, x2, y2 = box
                color = (0, 255, 0) if authenticated else (0, 0, 255)
                label = name if authenticated else "Unknown"
                cv2.putText(frame, "{} {:.3f}".format(label, similarity), (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, "SPACE : 出勤 / 退勤", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(frame, "ENTER : 休憩開始 / 休憩終了", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imshow(window, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key not in (32, 13):
                continue

            # Never stamp using a cached display result. Capture an unannotated frame.
            stamp_frame = camera.read()
            if stamp_frame is None:
                print("打刻用のカメラ画像を取得できませんでした。")
                break
            authenticated, name, similarity, box = recognizer.check(stamp_frame)
            if not authenticated:
                print("認証できないため、打刻しませんでした。")
                continue
            try:
                action = attendance.work_stamp(name) if key == 32 else attendance.break_stamp(name)
            except OSError as error:
                print("打刻データを保存できませんでした:", error)
                continue
            if action in ("出勤", "休憩終了"):
                try:
                    todo.show(name)
                except (OSError, UnicodeError) as error:
                    print("打刻は保存しましたが、Todoを読み込めませんでした:", error)
    finally:
        try:
            if camera is not None:
                camera.release()
        finally:
            cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
