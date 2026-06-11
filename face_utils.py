"""
face_utils.py — 人臉辨識(註冊 + 即時辨識 + 落地存檔)

設計重點：
  - 偵測：OpenCV 內建 Haar 正面人臉(零額外檔案)
  - 辨識：OpenCV LBPH(opencv-contrib)；小量註冊即可，訓練/預測都很快
  - 落地：faces/ 資料夾
        faces/labels.json   {"0":"小明","1":"Alice"}   名字(可中文)只存這裡
        faces/0/000.png …   每個人的臉部灰階裁切樣本(資料夾用數字 id → 避免中文路徑問題)
    重開程式時自動載入 → 直接可用。

中文相容：cv2.imread/imwrite 不吃非 ASCII 路徑，因此影像一律用
  np.fromfile / ndarray.tofile 讀寫；名字只存在 utf-8 的 json。
"""
import os
import json
import threading
import numpy as np
import cv2

FACE_DIR = "faces"
FACE_SIZE = (160, 160)            # 統一裁切尺寸
DEFAULT_THR = 70.0               # LBPH 距離門檻(越小越像)；<=門檻才算「認得」

_CASCADE = cv2.CascadeClassifier(
    os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))

# 中文字型(畫名字用；cv2.putText 不支援中文) — 找一個 Windows 內建 CJK 字型
_FONT = None
def _font():
    global _FONT
    if _FONT is None:
        try:
            from PIL import ImageFont
            for p in (r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\msyh.ttc",
                      r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
                if os.path.exists(p):
                    _FONT = ImageFont.truetype(p, 24)
                    break
            if _FONT is None:
                _FONT = ImageFont.load_default()
        except Exception:
            _FONT = False            # PIL 不可用 → 退回 cv2(只能畫英數)
    return _FONT


def detect_faces(gray, min_size=80):
    """回傳 [(x,y,w,h), …]，依面積大→小排序。"""
    if _CASCADE.empty():
        return []
    faces = _CASCADE.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5,
                                      minSize=(min_size, min_size))
    faces = [tuple(int(v) for v in f) for f in faces]
    faces.sort(key=lambda b: b[2] * b[3], reverse=True)
    return faces


def _norm_crop(gray, box):
    x, y, w, h = box
    crop = gray[max(0, y):y + h, max(0, x):x + w]
    if crop.size == 0:
        return None
    crop = cv2.resize(crop, FACE_SIZE)
    return cv2.equalizeHist(crop)    # 抗光照差異


def draw_labels(bgr, items):
    """一次把多個中文名字畫到畫面(items=[(x, y, text, (b,g,r)), …])。"""
    font = _font()
    if not font:                     # 無 PIL → 用 cv2(中文會變亂碼，退而求其次)
        for x, y, text, col in items:
            cv2.putText(bgr, text, (x, max(16, y)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, col, 2)
        return
    from PIL import Image, ImageDraw
    img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(img)
    for x, y, text, col in items:
        d.text((x, max(0, y)), text, font=font, fill=(col[2], col[1], col[0]))
    bgr[:] = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


class FaceDB:
    """整個人臉資料庫；所有 mutate 由相機執行緒單一呼叫(無跨緒競爭)。"""

    def __init__(self, root=FACE_DIR):
        self.root = root
        self.thr = DEFAULT_THR
        self.rec = None              # 已訓練的 LBPH；None=尚無資料
        self.names = {}              # id(int) -> name
        self._people = []            # 快取的人員清單(給狀態用，免每幀掃磁碟)
        self._lock = threading.Lock()
        os.makedirs(root, exist_ok=True)
        self._load_names()
        self.reload()

    # ---- 名字對照表 ----
    def _names_path(self):
        return os.path.join(self.root, "labels.json")

    def _load_names(self):
        try:
            with open(self._names_path(), encoding="utf-8") as f:
                self.names = {int(k): v for k, v in json.load(f).items()}
        except Exception:
            self.names = {}

    def _save_names(self):
        try:
            with open(self._names_path(), "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in self.names.items()},
                          f, ensure_ascii=False, indent=0)
        except Exception:
            pass

    # ---- 影像讀寫(中文路徑安全) ----
    @staticmethod
    def _imread_gray(path):
        try:
            data = np.fromfile(path, np.uint8)
            return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        except Exception:
            return None

    @staticmethod
    def _imwrite(path, img):
        ok, buf = cv2.imencode(".png", img)
        if ok:
            buf.tofile(path)

    def _dir(self, fid):
        return os.path.join(self.root, str(fid))

    def _count(self, fid):
        d = self._dir(fid)
        if not os.path.isdir(d):
            return 0
        return len([f for f in os.listdir(d) if f.lower().endswith(".png")])

    # ---- 重新訓練 ----
    def reload(self):
        imgs, labels = [], []
        for fid in list(self.names):
            d = self._dir(fid)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if f.lower().endswith(".png"):
                    im = self._imread_gray(os.path.join(d, f))
                    if im is not None:
                        imgs.append(cv2.resize(im, FACE_SIZE))
                        labels.append(fid)
        with self._lock:
            if imgs and hasattr(cv2, "face"):
                try:
                    rec = cv2.face.LBPHFaceRecognizer_create()
                    rec.train(imgs, np.array(labels))
                    self.rec = rec
                except Exception as e:
                    print("LBPH 訓練失敗(需 opencv-contrib-python):", e, flush=True)
                    self.rec = None
            else:
                self.rec = None
        self._people = [{"name": self.names[i], "samples": self._count(i)}
                        for i in sorted(self.names) if self._count(i) > 0]

    # ---- 對外操作 ----
    def people(self):
        return list(self._people)

    def enroll(self, name, gray, box):
        """把目前畫面這張臉存成 name 的一個樣本，並重新訓練。回傳 (ok, 訊息)。"""
        name = (name or "").strip()
        if not name:
            return False, "需要輸入名字"
        crop = _norm_crop(gray, box)
        if crop is None:
            return False, "臉部裁切失敗"
        fid = next((i for i, n in self.names.items() if n == name), None)
        if fid is None:
            fid = (max(self.names) + 1) if self.names else 0
            self.names[fid] = name
            self._save_names()
        d = self._dir(fid)
        os.makedirs(d, exist_ok=True)
        self._imwrite(os.path.join(d, f"{self._count(fid):03d}.png"), crop)
        self.reload()
        return True, f"已擷取「{name}」第 {self._count(fid)} 張樣本"

    def delete(self, name):
        fid = next((i for i, n in self.names.items() if n == name), None)
        if fid is None:
            return
        d = self._dir(fid)
        if os.path.isdir(d):
            for f in os.listdir(d):
                try:
                    os.remove(os.path.join(d, f))
                except Exception:
                    pass
            try:
                os.rmdir(d)
            except Exception:
                pass
        self.names.pop(fid, None)
        self._save_names()
        self.reload()

    def clear(self):
        for p in list(self._people):
            self.delete(p["name"])
        self.names = {}
        self._save_names()
        self.reload()

    def recognize(self, gray, box):
        """回傳 (name 或 None, 距離分數)。距離>門檻或無模型 → name=None(未知)。"""
        with self._lock:
            rec = self.rec
        if rec is None:
            return None, 999.0
        crop = _norm_crop(gray, box)
        if crop is None:
            return None, 999.0
        lab, conf = rec.predict(crop)
        name = self.names.get(int(lab))
        if name is None or conf > self.thr:
            return None, float(conf)
        return name, float(conf)


# 單例：import 時即載入 faces/ → 重開可直接用
DB = FaceDB()
