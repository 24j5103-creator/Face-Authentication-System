# 共通トークンとJPEGの形式・サイズを確認する共通処理です。
import os
import struct

# HTTPサービスで返すエラー。クライアント配布物に業務ロジックは不要です。
class ServiceError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


# 環境変数からトークンを読みます。秘密値はソースに保存しません。
def get_token():
    token = os.environ.get("FACE_API_TOKEN", "")
    if len(token) < 32 or not token.isascii() or not token.isalnum():
        raise RuntimeError("FACE_API_TOKEN must contain at least 32 ASCII letters/digits; use the same token on both PCs.")
    return token


def jpeg_size(data):
    """Check JPEG dimensions BEFORE decoding to bound decoded memory."""
    if data[:2] != b"\xff\xd8":
        raise ServiceError(400, "JPEG required")
    pos = 2
    while pos < len(data):
        if data[pos] != 255:
            break
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD9, 0xDA):
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            continue
        if pos + 2 > len(data):
            break
        length = struct.unpack_from(">H", data, pos)[0]
        if length < 2 or pos + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2):
            if length < 8:
                break
            height, width = struct.unpack_from(">HH", data, pos + 3)
            if not (1 <= width <= 1920 and 1 <= height <= 1080):
                raise ServiceError(413, "Image must be at most 1920 x 1080")
            return width, height
        pos += length
    raise ServiceError(400, "Invalid or unsupported JPEG")

