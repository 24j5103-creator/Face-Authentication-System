# 大学側CPUで顔検出と登録特徴の照合を行い、ライブネスを確認します。
import cv2
import numpy as np
from insightface.app import FaceAnalysis
from Liveness_detection import LivenessDetector

class FaceRecognizer:
    def __init__(self, registered_faces, threshold, liveness_threshold=0.8):
        self.threshold = threshold

        # Load ArcFace model
        self.app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        self.app.prepare(ctx_id=-1, det_size=(640, 640))

        # Load liveness models once
        self.liveness = LivenessDetector(threshold=liveness_threshold)
        self.live_score = None

        # Store multiple registered face vectors per person
        self.registered_vectors = {}
        for name, image_paths in registered_faces.items():
            self.registered_vectors[name] = []
            for image_path in image_paths:
                img = cv2.imread(image_path)
                if img is None:
                    raise RuntimeError(image_path + " could not be loaded.")
                faces = self.app.get(img)
                if len(faces) != 1:
                    raise RuntimeError("Exactly one face is required in " + image_path)
                self.registered_vectors[name].append(faces[0].normed_embedding)

    # 入力画像を判定します。認証成功の条件はここで確認します。
    def check(self, frame):
        self.live_score = None

        # Detect faces from webcam frame
        faces = self.app.get(frame)
        if len(faces) == 0:
            return False, "Unknown", 0.0, None

        # Use the largest detected face
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        x1, y1, x2, y2 = face.bbox.astype(int)
        box = (x1, y1, x2, y2)

        # Compare with all registered users
        camera_vector = face.normed_embedding
        best_name = "Unknown"
        best_similarity = -1.0
        for name, registered_vectors in self.registered_vectors.items():
            similarity = max(
                float(np.dot(vector, camera_vector))
                for vector in registered_vectors
            )
            if similarity > best_similarity:
                best_similarity = similarity
                best_name = name

        # Reject unrecognized faces before running liveness
        if best_similarity < self.threshold:
            return False, "Unknown", best_similarity, box

        # Check liveness on the same frame and face
        is_live, self.live_score = self.liveness.check(frame, face.bbox)
        if not is_live:
            return False, "Unknown", best_similarity, box

        return True, best_name, best_similarity, box
