"""
face_utils.py — 人臉辨識(可切換 LBPH / ArcFace)

兩種辨識引擎，UI 可即時切換：
  • lbph    : OpenCV LBPH(opencv-contrib)。零額外檔案、輕量；正面為主。
  • arcface : ONNX 深度 embedding(512 維)+ 餘弦相似度。更準、抗側臉；
              需 `pip install onnxruntime` 且放 models/arcface.onnx(112×112→512)。

偵測 / 對齊：
  • 若有 models/blaze_face_short_range.tflite → 用 MediaPipe FaceDetector
    (容忍角度，並給雙眼關鍵點 → 用雙眼做相似變換對齊)；
  • 否則退回 OpenCV Haar(正面、無對齊)。

落地(faces/，重開即用)：
  faces/labels.json   {"0":"小明",...}            名字(可中文)只存這
  faces/<id>/<n>.png  對齊後 112×112 彩色臉(兩引擎共用 → 切換引擎不必重註冊)
ArcFace 的 embedding 於載入/註冊時即時算出(快取在記憶體)。

中文相容：影像用 np.fromfile / tofile 讀寫；名字只存 utf-8 json。
"""
import os
import json
import threading
import numpy as np
import cv2

FACE_DIR = "faces"
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
ARC_MODEL = os.path.join(MODEL_DIR, "arcface.onnx")
FD_MODEL = os.path.join(MODEL_DIR, "blaze_face_short_range.tflite")

LBPH_SIZE = (160, 160)
ARC_SIZE = 112
DEFAULT_THR_LBPH = 70.0          # LBPH 距離(越小越像)；<=門檻才算認得
DEFAULT_SIM_ARC = 0.35           # ArcFace 餘弦相似度(越大越像)；>=門檻才算認得

# ArcFace 標準 5 點對齊模板(112×112)；本檔只用前兩點(雙眼)
ARC_DST5 = np.array([[38.2946, 51.6963], [73.5318, 51.5014],
                     [56.0252, 71.7366], [41.5493, 92.3655],
                     [70.7299, 92.2041]], dtype=np.float32)

_HAAR = cv2.CascadeClassifier(
    os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))

# ---------------- 中文字型(畫名字) ----------------
_FONT = None
def _font():
    global _FONT
    if _FONT is None:
        try:
            from PIL import ImageFont
            for p in (r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\msyh.ttc",
                      r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
                if os.path.exists(p):
                    _FONT = ImageFont.truetype(p, 24); break
            if _FONT is None:
                _FONT = ImageFont.load_default()
        except Exception:
            _FONT = False
    return _FONT


def draw_labels(bgr, items):
    """一次把多個(中文)名字畫到畫面：items=[(x, y, text, (b,g,r)), …]。"""
    font = _font()
    if not font:
        for x, y, text, col in items:
            cv2.putText(bgr, text, (x, max(16, y)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
        return
    from PIL import Image, ImageDraw
    img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(img)
    for x, y, text, col in items:
        d.text((x, max(0, y)), text, font=font, fill=(col[2], col[1], col[0]))
    bgr[:] = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


# ---------------- 偵測器(MediaPipe FaceDetector 優先，否則 Haar) ----------------
_FD = None; _FD_TRIED = False
def _mp_detector():
    global _FD, _FD_TRIED
    if _FD_TRIED:
        return _FD
    _FD_TRIED = True
    if not os.path.exists(FD_MODEL):
        return None
    try:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        with open(FD_MODEL, "rb") as f:
            buf = f.read()
        opts = vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_buffer=buf),
            running_mode=vision.RunningMode.IMAGE, min_detection_confidence=0.5)
        _FD = vision.FaceDetector.create_from_options(opts)
        print("人臉偵測：MediaPipe FaceDetector(可對齊/抗角度)", flush=True)
    except Exception as e:
        print("MediaPipe FaceDetector 載入失敗，改用 Haar：", e, flush=True)
        _FD = None
    return _FD


def detect(bgr):
    """回傳 [{'box':(x,y,w,h), 'kps':[(rx,ry),(lx,ly)] 或 None}]，面積大→小。"""
    h, w = bgr.shape[:2]
    fd = _mp_detector()
    out = []
    if fd is not None:
        try:
            import mediapipe as mp
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            res = fd.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            for d in res.detections:
                bb = d.bounding_box
                box = (int(bb.origin_x), int(bb.origin_y), int(bb.width), int(bb.height))
                kps = None
                if d.keypoints and len(d.keypoints) >= 2:   # [右眼,左眼,鼻,嘴,右耳,左耳]
                    kps = [(d.keypoints[0].x * w, d.keypoints[0].y * h),
                           (d.keypoints[1].x * w, d.keypoints[1].y * h)]
                out.append({"box": box, "kps": kps})
        except Exception:
            out = []
    if not out and not _HAAR.empty():                       # 退回 Haar
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        for f in _HAAR.detectMultiScale(gray, 1.2, 5, minSize=(80, 80)):
            out.append({"box": tuple(int(v) for v in f), "kps": None})
    out.sort(key=lambda d: d["box"][2] * d["box"][3], reverse=True)
    return out


def _crop112(bgr, det):
    """對齊(有雙眼)或裁切縮放 → 112×112 彩色臉。"""
    kps = det.get("kps")
    if kps and len(kps) >= 2:
        M, _ = cv2.estimateAffinePartial2D(
            np.array(kps[:2], np.float32), ARC_DST5[:2])
        if M is not None:
            return cv2.warpAffine(bgr, M, (ARC_SIZE, ARC_SIZE), flags=cv2.INTER_LINEAR)
    x, y, w, h = det["box"]
    c = bgr[max(0, y):y + h, max(0, x):x + w]
    if c.size == 0:
        return None
    return cv2.resize(c, (ARC_SIZE, ARC_SIZE))


def _lbph_in(crop112):
    g = cv2.cvtColor(crop112, cv2.COLOR_BGR2GRAY)
    return cv2.equalizeHist(cv2.resize(g, LBPH_SIZE))


# ---------------- ArcFace embedder(ONNX，延遲載入) ----------------
class _Embedder:
    def __init__(self):
        self.sess = None; self.iname = None; self.tried = False
    def available(self):
        if self.sess is not None:
            return True
        if self.tried:
            return False
        self.tried = True
        if not os.path.exists(ARC_MODEL):
            return False
        try:
            import onnxruntime as ort
            self.sess = ort.InferenceSession(
                ARC_MODEL, providers=["CPUExecutionProvider"])
            self.iname = self.sess.get_inputs()[0].name
            print("ArcFace 模型載入成功：", ARC_MODEL, flush=True)
            return True
        except Exception as e:
            print("ArcFace 不可用(需 onnxruntime + models/arcface.onnx)：", e, flush=True)
            self.sess = None
            return False
    def embed(self, crop112):
        rgb = cv2.cvtColor(crop112, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = (rgb - 127.5) / 127.5
        blob = np.transpose(rgb, (2, 0, 1))[None]          # 1×3×112×112
        v = self.sess.run(None, {self.iname: blob})[0][0].astype(np.float32)
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

_EMB = _Embedder()


class FaceDB:
    """名字 + 對齊臉樣本 + 兩引擎模型；mutate 由相機執行緒單一呼叫。"""

    def __init__(self, root=FACE_DIR):
        self.root = root
        self.engine = "lbph"             # 使用者選的引擎
        self.thr_lbph = DEFAULT_THR_LBPH
        self.thr_arc = DEFAULT_SIM_ARC
        self.rec = None                  # LBPH
        self.gallery = {}                # id -> [embedding, …]
        self.names = {}                  # id -> name
        self._people = []
        self._lock = threading.Lock()
        os.makedirs(root, exist_ok=True)
        self._load_names()
        self.reload()

    # ---- 名字表 ----
    def _np(self):
        return os.path.join(self.root, "labels.json")
    def _load_names(self):
        try:
            with open(self._np(), encoding="utf-8") as f:
                self.names = {int(k): v for k, v in json.load(f).items()}
        except Exception:
            self.names = {}
    def _save_names(self):
        try:
            with open(self._np(), "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in self.names.items()},
                          f, ensure_ascii=False)
        except Exception:
            pass

    # ---- 影像讀寫(中文路徑安全) ----
    @staticmethod
    def _imread(path):
        try:
            return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            return None
    @staticmethod
    def _imwrite(path, img):
        ok, buf = cv2.imencode(".png", img)
        if ok:
            buf.tofile(path)
    def _dir(self, fid):
        return os.path.join(self.root, str(fid))
    def _files(self, fid):
        d = self._dir(fid)
        return ([os.path.join(d, f) for f in sorted(os.listdir(d))
                 if f.lower().endswith(".png")] if os.path.isdir(d) else [])

    # ---- 能力 / 引擎 ----
    def arc_available(self):
        return _EMB.available()
    def effective(self):
        """實際使用的引擎(選 arcface 但不可用時自動退回 lbph)。"""
        return "arcface" if (self.engine == "arcface" and _EMB.available()) else "lbph"
    def set_engine(self, eng):
        eng = "arcface" if eng == "arcface" else "lbph"
        if eng != self.engine:
            self.engine = eng
            self.reload()                # 切換 → 確保該引擎的模型/gallery 已就緒

    # ---- 訓練/載入 ----
    def reload(self):
        imgs, labels, gallery = [], [], {}
        use_arc = (self.effective() == "arcface")
        for fid in list(self.names):
            embs = []
            for p in self._files(fid):
                crop = self._imread(p)
                if crop is None:
                    continue
                crop = cv2.resize(crop, (ARC_SIZE, ARC_SIZE))
                imgs.append(_lbph_in(crop)); labels.append(fid)
                if use_arc:
                    try:
                        embs.append(_EMB.embed(crop))
                    except Exception:
                        pass
            if embs:
                gallery[fid] = embs
        with self._lock:
            if imgs and hasattr(cv2, "face"):
                try:
                    r = cv2.face.LBPHFaceRecognizer_create()
                    r.train(imgs, np.array(labels)); self.rec = r
                except Exception as e:
                    print("LBPH 訓練失敗(需 opencv-contrib-python)：", e, flush=True)
                    self.rec = None
            else:
                self.rec = None
            self.gallery = gallery
        self._people = [{"name": self.names[i], "samples": len(self._files(i))}
                        for i in sorted(self.names) if self._files(i)]

    # ---- 操作 ----
    def people(self):
        return list(self._people)

    def enroll(self, name, bgr, det):
        name = (name or "").strip()
        if not name:
            return False, "需要輸入名字"
        crop = _crop112(bgr, det)
        if crop is None:
            return False, "臉部裁切失敗"
        fid = next((i for i, n in self.names.items() if n == name), None)
        if fid is None:
            fid = (max(self.names) + 1) if self.names else 0
            self.names[fid] = name; self._save_names()
        d = self._dir(fid); os.makedirs(d, exist_ok=True)
        self._imwrite(os.path.join(d, f"{len(self._files(fid)):03d}.png"), crop)
        self.reload()
        return True, f"已擷取「{name}」第 {len(self._files(fid))} 張樣本"

    def delete(self, name):
        fid = next((i for i, n in self.names.items() if n == name), None)
        if fid is None:
            return
        for p in self._files(fid):
            try: os.remove(p)
            except Exception: pass
        try: os.rmdir(self._dir(fid))
        except Exception: pass
        self.names.pop(fid, None); self._save_names(); self.reload()

    def clear(self):
        for p in list(self._people):
            self.delete(p["name"])
        self.names = {}; self._save_names(); self.reload()

    def recognize(self, bgr, det):
        """回傳 (name 或 None, score)。lbph: score=距離(小=像)；arcface: score=餘弦(大=像)。"""
        crop = _crop112(bgr, det)
        if crop is None:
            return None, 0.0
        if self.effective() == "arcface":
            with self._lock:
                gal = self.gallery
            if not gal:
                return None, 0.0
            try:
                q = _EMB.embed(crop)
            except Exception:
                return None, 0.0
            best_id, best = None, -1.0
            for fid, embs in gal.items():
                s = max(float(np.dot(q, e)) for e in embs)   # 餘弦(已正規化)
                if s > best:
                    best, best_id = s, fid
            if best_id is None or best < self.thr_arc:
                return None, round(best, 3)
            return self.names.get(best_id), round(best, 3)
        # LBPH
        with self._lock:
            rec = self.rec
        if rec is None:
            return None, 999.0
        lab, conf = rec.predict(_lbph_in(crop))
        name = self.names.get(int(lab))
        if name is None or conf > self.thr_lbph:
            return None, round(float(conf), 1)
        return name, round(float(conf), 1)


# 單例：import 時載入 faces/
DB = FaceDB()
