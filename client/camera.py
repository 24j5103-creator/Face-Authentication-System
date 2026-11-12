# カメラを開き、フレームを取得し、終了時に解放します。
import cv2

class Camera:
    def __init__(self, camera_id=0):
        # Open web camera
        self.cap = cv2.VideoCapture(camera_id)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError("Web camera could not be opened.")

    def read(self):
        # Get one frame
        ret, frame = self.cap.read()
        if not ret:
            return None
        return frame

    def release(self):
        # Release camera
        self.cap.release()
