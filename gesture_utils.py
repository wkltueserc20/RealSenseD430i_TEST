"""
gesture_utils.py  —  共用模組（MediaPipe Tasks API）

新版 mediapipe (>=0.10.x, Python 3.13) 移除了 mp.solutions，改用 Tasks API。
這個模組封裝：建立偵測器、手指計數、手勢判斷、在影像上畫關鍵點。
"""

import os
import math
import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
HAND_MODEL = os.path.join(MODEL_DIR, "hand_landmarker.task")
POSE_MODEL = os.path.join(MODEL_DIR, "pose_landmarker_lite.task")

# 手部 21 點的連線（畫骨架用）
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
]

# 人體姿態主要連線（簡化骨架；33 點 BlazePose 索引）
POSE_CONNECTIONS = [
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),   # 雙臂
    (11, 23), (12, 24), (23, 24),                       # 軀幹
    (23, 25), (25, 27), (24, 26), (26, 28),             # 雙腿
    (0, 11), (0, 12),                                   # 頭到肩
]


def _read_model(path):
    # 用 buffer 載入，避免中文/非 ASCII 路徑在 mediapipe C++ 層開檔失敗
    with open(path, "rb") as f:
        return f.read()


def create_hand_landmarker(max_hands=2):
    opts = vision.HandLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_buffer=_read_model(HAND_MODEL)),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=max_hands,
        min_hand_detection_confidence=0.6,
        min_tracking_confidence=0.5,
    )
    return vision.HandLandmarker.create_from_options(opts)


def create_pose_landmarker(num_poses=3):
    # 偵測多人：避免兩人重疊時被硬接成一個怪骨架（再由後端用深度挑主體）
    opts = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_buffer=_read_model(POSE_MODEL)),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=num_poses,
        min_pose_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return vision.PoseLandmarker.create_from_options(opts)


def to_mp_image(rgb):
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)


def _dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def _dist3(a, b):
    """含 z 的 3D 距離（拇指朝向鏡頭時 2D 會縮短，用 3D 較準）"""
    dz = getattr(a, "z", 0.0) - getattr(b, "z", 0.0)
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + dz * dz)


def _angle(a, b, c):
    """回傳三點 a-b-c 在頂點 b 的夾角(度)。手指伸直≈180，彎曲則變小。"""
    bax, bay = a.x - b.x, a.y - b.y
    bcx, bcy = c.x - b.x, c.y - b.y
    na = math.hypot(bax, bay)
    nc = math.hypot(bcx, bcy)
    if na == 0 or nc == 0:
        return 180.0
    cosv = (bax * bcx + bay * bcy) / (na * nc)
    cosv = max(-1.0, min(1.0, cosv))
    return math.degrees(math.acos(cosv))


# 四指用 (MCP, PIP, TIP) 算 PIP 夾角；夾角大=伸直
_FINGER_JOINTS = [(5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20)]
_FINGER_THRESH = 150.0   # 夾角 > 此值視為伸直
_THUMB_LAT = 0.15        # 拇指外展門檻(占手掌尺度比例)；越小越容易判為張開


def count_fingers(landmarks, handed_label=None):
    """
    以「關節夾角 / 側向外展」判斷手指張開，與手的方向、左右手、鏡像都無關。
    回傳 (張開數 0~5, [拇, 食, 中, 無, 小] 布林)。
    handed_label 已不需要(保留參數相容舊呼叫)。
    """
    lm = landmarks

    # 其餘四指：PIP 夾角夠直即為張開
    four = [
        _angle(lm[mcp], lm[pip], lm[tip]) > _FINGER_THRESH
        for mcp, pip, tip in _FINGER_JOINTS
    ]

    # 拇指：看指尖在「手掌側向軸」上是否往拇指那側「外展」。
    # 張開時拇指外展、指尖比根部更往側邊；折進掌心時指尖縮回 → 外展量變負。
    # 這比單純距離更能分辨「折起的拇指(4)」與「張開的拇指(5)」。
    wrist, mmcp = lm[0], lm[9]
    ax, ay = mmcp.x - wrist.x, mmcp.y - wrist.y
    al = math.hypot(ax, ay) or 1e-6
    ax, ay = ax / al, ay / al                      # 手掌前向單位向量

    def lat(p):                                    # 點相對手掌軸的側向(帶正負)
        return ax * (p.y - wrist.y) - ay * (p.x - wrist.x)

    side = 1.0 if lat(lm[2]) >= 0 else -1.0        # 拇指在哪一側
    extension = (lat(lm[4]) - lat(lm[2])) * side   # 指尖比拇指根多伸出的側向量
    scale = _dist3(lm[0], lm[9]) or 1e-6
    thumb_open = extension > _THUMB_LAT * scale

    # 後備：四指全折但拇指明顯比四指都更突出(讚) → 仍算張開
    if not thumb_open and not any(four):
        max_finger_d = max(_dist3(lm[tip], lm[0]) for _, _, tip in _FINGER_JOINTS)
        if _dist3(lm[4], lm[0]) > max_finger_d * 1.15:
            thumb_open = True

    fingers = [thumb_open] + four
    return sum(fingers), fingers


def guess_gesture(count, fingers):
    thumb, index, middle, ring, pinky = fingers
    if count == 0:
        return "Fist (0)"
    if count == 5:
        return "Open palm (5)"
    if index and middle and not ring and not pinky and not thumb:
        return "Peace / 2"
    if index and not middle and not ring and not pinky:
        return "Point / 1"
    if thumb and not index and not middle and not ring and not pinky:
        return "Thumbs up"
    if thumb and pinky and not index and not middle and not ring:
        return "Call me"
    return f"{count} fingers"


def draw_landmarks(img, landmarks, connections, color=(0, 255, 0),
                   pt_color=(0, 0, 255)):
    """在 BGR 影像上畫關鍵點與連線。landmarks 為 normalized (.x/.y)。"""
    h, w = img.shape[:2]
    pts = [(int(p.x * w), int(p.y * h)) for p in landmarks]
    for a, b in connections:
        if a < len(pts) and b < len(pts):
            cv2.line(img, pts[a], pts[b], color, 2)
    for (x, y) in pts:
        cv2.circle(img, (x, y), 3, pt_color, -1)
