"""
vision_server.py  —  視覺後端（給 Rust 前端用）

職責：RealSense 取像 + MediaPipe 手/人偵測，把結果透過本機 TCP 傳給 Rust UI。
  - 影像畫上骨架後 JPEG 壓縮傳出
  - 同時傳出狀態 JSON（有沒有人 / 距離 / 手指數 / 手勢 / 每指狀態）
  - 接收前端指令（開關 pose/hands/depth/flip、擷取點雲）

協定（伺服器→前端，每幀）：
  [u32 json_len][json bytes][u32 jpeg_len][jpeg bytes]   皆 big-endian
前端→伺服器（指令，一行一個 JSON）：
  {"pose":true,"hands":true,"depth":false,"flip":true}\n
  {"scan":true}\n

執行：  python vision_server.py    （Rust UI 會自動連上 127.0.0.1:50505）
"""

import os
import math
import threading
import time
import webbrowser
import numpy as np
import cv2
import pyrealsense2 as rs
import gesture_utils as gu
import face_utils as fu

API_HOST, API_PORT = "0.0.0.0", 8000   # REST API + 網頁介面
W, H, FPS = 640, 480, 30
OUT_DIR = "scans"
FALL_DIR = "falls"   # 跌倒截圖存證資料夾


class Config:
    def __init__(self):
        self.pose = False
        self.hands = False
        self.depth = False
        self.flip = True
        self.dfilter = True     # 深度後處理濾鏡（去噪/補洞）
        self.measure = False    # 物體量測模式開關
        self.pick = None        # 點選的目標種子像素 (u,v)，None=未選
        self.template = None    # 教學的物體樣板（ORB+顏色+尺寸）
        self.detect = False     # 自動辨識已教學物體
        self.teach_request = False  # 一次性：擷取目前物體為樣板
        self.level = True       # IMU 自動水平校正（後端旋轉）
        self.zoom = 1.0         # 畫面縮放倍率
        self.disp = 800         # 輸出正方形畫布邊長(像素)
        self.inspect = False    # 參考框定位品檢
        self.inspect_box = None # 參考框 (x0,y0,x1,y1) 顯示座標
        self.inspect_ref = None # 框內物體的特徵參考(畫框當下擷取)
        self.inspect_thr = 60.0 # NG 判定門檻分數
        self.pcloud = False     # 即時 3D 點雲(網頁檢視器開啟時才計算)
        self.obstacle = False   # 障礙物檢測開關
        self.obstacle_regions = []  # 檢測範圍清單；每個=多邊形 [[x,y],...] 顯示座標；空=整個畫面
        self.obstacle_dist = 1.0  # 觸發距離(公尺)；任一範圍最近深度<=此值即警報
        self.face = False       # 人臉辨識開關
        self.face_engine = "lbph"  # 辨識引擎：lbph / arcface
        self.face_thr = 70.0    # LBPH 距離門檻(越小越嚴)；<=此值才顯示名字
        self.face_sim = 0.35    # ArcFace 餘弦相似度門檻(越大越嚴)；>=此值才顯示名字
        self.face_enroll = None  # 一次性：擷取目前畫面的臉當作此名字的樣本
        self.face_delete = None  # 一次性：刪除此名字的人臉資料
        self.face_clear = False  # 一次性：清除所有人臉資料
        self.scan = False
        self.snapshot = False   # 跌倒自動截圖
        self.lock = threading.Lock()


def depth_at(depth_frame, x, y, win=5):
    h_, w_ = depth_frame.get_height(), depth_frame.get_width()
    xs = range(max(0, x - win), min(w_, x + win + 1))
    ys = range(max(0, y - win), min(h_, y + win + 1))
    vals = [depth_frame.get_distance(i, j) for j in ys for i in xs]
    vals = [v for v in vals if v > 0]
    return float(np.median(vals)) if vals else 0.0


# ---- 點選物體量測（3D 點雲 + PCA 有向包圍盒）----
# 3D 有向包圍盒(OBB) 8 個角的 12 條邊（角點以 (sx,sy,sz) 三位元編碼）
OBB_EDGES = [(i, j) for i in range(8) for j in range(i + 1, 8)
             if bin(i ^ j).count("1") == 1]
_OBB_SIGNS = np.array([[(-1) ** (i >> 2 & 1), (-1) ** (i >> 1 & 1),
                        (-1) ** (i & 1)] for i in range(8)], dtype=np.float32)


def _project_to_pixel(pts_cam, fx, fy, ppx, ppy):
    """3D 相機座標(公尺) → 影像像素(u,v)，使用顯示座標系內參。"""
    z = np.where(np.abs(pts_cam[:, 2]) < 1e-6, 1e-6, pts_cam[:, 2])
    u = pts_cam[:, 0] / z * fx + ppx
    v = pts_cam[:, 1] / z * fy + ppy
    return np.stack([u, v], axis=1)


def _valid_seed(depth_m, u, v, win=10):
    """在點擊處附近找種子像素。**偏好較近(前景)** 的點，避免落到後方背景
    或反光造成的錯誤遠距離讀數。回傳 (su,sv) 或 None。"""
    h, w = depth_m.shape
    u = int(np.clip(u, 0, w - 1))
    v = int(np.clip(v, 0, h - 1))
    y0, y1 = max(0, v - win), min(h, v + win + 1)
    x0, x1 = max(0, u - win), min(w, u + win + 1)
    sub = depth_m[y0:y1, x0:x1]
    ok = np.argwhere(sub > 0.1)
    if ok.size == 0:
        return None
    depths = sub[ok[:, 0], ok[:, 1]]
    near = float(np.percentile(depths, 5))          # 視窗內最近深度
    fg = ok[depths <= near + 0.05]                  # 只保留前景一層
    if fg.shape[0] == 0:
        fg = ok
    cy, cx = v - y0, u - x0                          # 前景中取最靠近點擊處者
    dd = (fg[:, 0] - cy) ** 2 + (fg[:, 1] - cx) ** 2
    yy, xx = fg[int(np.argmin(dd))]
    return x0 + int(xx), y0 + int(yy)


def segment_click(depth_m, seed, tol=0.03, max_band=0.15):
    """從點擊處用深度泛水填充(flood fill)分割出連通的物體。

    相鄰像素深度差 <= tol 才視為同一物體 → 在物體邊緣的深度跳變處自動停住。
    再用「離種子深度 max_band 內」限制，避免漏到支撐桌面太遠。
    回傳 bool 遮罩或 None。
    """
    sd = _valid_seed(depth_m, seed[0], seed[1])
    if sd is None:
        return None
    su, sv = sd
    seed_d = float(depth_m[sv, su])
    h, w = depth_m.shape
    img = depth_m.copy()
    img[img <= 0.1] = -10.0                          # 破洞設極端值，阻擋越界
    ffmask = np.zeros((h + 2, w + 2), np.uint8)
    flags = 8 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY
    cv2.floodFill(img, ffmask, (su, sv), 0, tol, tol, flags)
    m = ffmask[1:-1, 1:-1].astype(bool)
    m &= np.abs(depth_m - seed_d) <= max_band
    m &= depth_m > 0.1
    return m


def _obb_from_points(pts, fx, fy, ppx, ppy, min_pts=400, max_pts=15000):
    """一團 3D 點(Nx3) → PCA 有向包圍盒。回傳尺寸/傾斜/距離/投影角點。"""
    if pts.shape[0] < min_pts:
        return None
    if pts.shape[0] > max_pts:                       # 下採樣加速
        idx = np.random.choice(pts.shape[0], max_pts, replace=False)
        pts = pts[idx]

    # === 穩健清理（參考 PCL StatisticalOutlierRemoval + 深度門檻）===
    # 1) 深度門檻：剔除偏離主深度的飛點(反光/穿透造成的錯誤遠/近讀數)。
    #    用 MAD 自適應：深度乾淨→帶窄，雜訊大→帶寬但有上限。
    z = pts[:, 2]
    medz = float(np.median(z))
    madz = float(np.median(np.abs(z - medz)))
    zband = min(0.30, max(0.05, 5.0 * madz))
    pts = pts[np.abs(z - medz) <= zband]
    if pts.shape[0] < min_pts:
        return None
    # 2) 統計離群移除：迭代剔除離質心 > mean+1.5σ 的點(去掉散落噪點)
    for _ in range(3):
        c = pts.mean(axis=0)
        dd = np.linalg.norm(pts - c, axis=1)
        keep = dd <= dd.mean() + 2.0 * dd.std()
        if keep.all() or int(keep.sum()) < min_pts:
            break
        pts = pts[keep]
    if pts.shape[0] < min_pts:
        return None

    # PCA：covariance 的特徵向量 = 物體三個正交主軸
    centroid = pts.mean(axis=0)
    Q = pts - centroid
    cov = (Q.T @ Q) / Q.shape[0]
    _, evecs = np.linalg.eigh(cov)
    proj = Q @ evecs
    lo = np.percentile(proj, 0.5, axis=0)            # 百分位數抗雜訊
    hi = np.percentile(proj, 99.5, axis=0)
    extents = hi - lo

    order = np.argsort(extents)[::-1]                # 由大到小：長,寬,高
    length = float(extents[order[0]])
    width = float(extents[order[1]])
    height = float(extents[order[2]])
    if length > 2.5:                                 # 明顯是雜訊團，不回報
        return None
    dist = float(np.median(pts[:, 2]))

    a = evecs[:, order[0]]                            # 最長主軸 → 傾斜
    tilt = math.degrees(math.atan2(a[1], a[0]))
    while tilt <= -90:
        tilt += 180
    while tilt > 90:
        tilt -= 180

    mid = (lo + hi) / 2.0
    half = (hi - lo) / 2.0
    pts8 = centroid + (mid + _OBB_SIGNS * half) @ evecs.T
    corners_2d = _project_to_pixel(pts8, fx, fy, ppx, ppy).astype(np.int32)
    center_2d = _project_to_pixel(centroid[None, :], fx, fy, ppx,
                                  ppy)[0].astype(np.int32)
    return {
        "length": length, "width": width, "height": height,
        "tilt": tilt, "dist": dist, "n_pts": int(pts.shape[0]),
        "corners_2d": corners_2d, "center_2d": center_2d,
    }


def measure_mask_3d(depth_m, mask, fx, fy, ppx, ppy, min_pts=400):
    """遮罩內像素 → 反投影成 3D 點雲 → OBB。

    depth_m 與 (fx,fy,ppx,ppy) 都在「顯示座標系」(已含鏡像/水平校正)，
    因此 tilt 直接相對顯示水平線；已水平校正時即相對重力水平。
    """
    ys, xs = np.nonzero(mask)
    if xs.size < min_pts:
        return None
    d = depth_m[ys, xs].astype(np.float32)
    X = (xs - ppx) * d / fx
    Y = (ys - ppy) * d / fy
    pts = np.stack([X, Y, d], axis=1)
    return _obb_from_points(pts, fx, fy, ppx, ppy, min_pts)


# ---- 教學/辨識：ORB 特徵 + 單應性定位 + 四道驗證(避免誤認) ----
_ORB = cv2.ORB_create(nfeatures=1500)
_BF = cv2.BFMatcher(cv2.NORM_HAMMING)
MIN_ORB = 18                    # 教學最少特徵數
MIN_GOOD = 15                   # 辨識：好配對數下限
MIN_INLIER = 12                 # 辨識：RANSAC 內點下限
MIN_INLIER_RATIO = 0.25         # 辨識：內點 / 好配對 比例下限
MIN_APPEAR = 0.30               # 辨識：外觀相關(NCC)下限
PATCH_W = 96                    # 樣板比對影像寬

# 偵測遲滯(避免閃爍)：確認需累積命中，確認後容忍連續漏失幀數
DET_CONFIRM = 2                 # 命中達此數即視為穩定追蹤
DET_LOSE = 8                    # 連續漏失超過此數才放棄(~0.27s@30fps)
MAX_VIEWS = 10                 # 每個物體最多教幾個視角
_track = {"hits": 0, "miss": 0, "active": False, "seed": None, "quad": None,
          "m": None, "rgb": False, "dist": 0.5, "ema": {}}


def _track_reset():
    _track.update(hits=0, miss=0, active=False, seed=None, quad=None,
                  m=None, rgb=False, dist=0.5, ema={})


def build_template(gray, mask, dims, dist):
    """擷取樣板：ORB 特徵(含關鍵點) + 樣板灰階影像(外觀比對用) + 邊框/比例/尺寸/距離。
    特徵不足回傳 None。"""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    bw, bh = x1 - x0, y1 - y0
    if bw < 24 or bh < 24:
        return None
    h, w = gray.shape
    pad = 12
    region = np.zeros((h, w), np.uint8)
    region[max(0, y0 - pad):min(h, y1 + pad),
           max(0, x0 - pad):min(w, x1 + pad)] = 255
    kp, des = _ORB.detectAndCompute(gray, region)
    if des is None or len(des) < MIN_ORB:
        return None
    kp_pts = np.array([k.pt for k in kp], np.float32)
    patch = cv2.resize(gray[y0:y1, x0:x1],
                       (PATCH_W, max(8, int(PATCH_W * bh / bw))))
    return {"des": des, "kp": kp_pts, "bbox": (x0, y0, x1, y1),
            "patch": patch, "aspect": max(bw, bh) / max(1, min(bw, bh)),
            "dims": tuple(float(x) for x in dims), "dist": float(dist),
            "n_orb": len(des)}


def _quad_ok(quad, frame_area, aspect_ref):
    """幾何驗證：四邊形要凸、面積合理、長寬比接近教學物體。"""
    q = quad.reshape(-1, 1, 2).astype(np.float32)
    if not cv2.isContourConvex(q):
        return False
    area = cv2.contourArea(q)
    if area < 0.004 * frame_area or area > 0.75 * frame_area:
        return False
    e = [float(np.linalg.norm(quad[(i + 1) % 4] - quad[i])) for i in range(4)]
    ew, eh = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
    if min(ew, eh) < 12:
        return False
    asp = max(ew, eh) / max(1e-6, min(ew, eh))
    return aspect_ref / 2.2 <= asp <= aspect_ref * 2.2


def _appearance_score(gray, H, tmpl):
    """外觀驗證：用單應性把畫面中的區域『拉正』回樣板座標，與教學影像做 NCC。"""
    try:
        Hinv = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return 0.0
    rect = cv2.warpPerspective(gray, Hinv, (gray.shape[1], gray.shape[0]))
    x0, y0, x1, y1 = tmpl["bbox"]
    crop = rect[y0:y1, x0:x1]
    if crop.size == 0:
        return 0.0
    crop = cv2.resize(crop, (tmpl["patch"].shape[1], tmpl["patch"].shape[0]))
    res = cv2.matchTemplate(crop, tmpl["patch"], cv2.TM_CCOEFF_NORMED)
    return float(res[0, 0])


def _match_one(kp2, des2, gray, view):
    """畫面 ORB(kp2,des2) 對『單一視角』比對 + 四道驗證。
    回傳 (種子, 內點數, 四邊形) 或 None。"""
    tdes = view.get("des")
    if tdes is None or view["kp"].shape[0] != len(tdes):
        return None
    matches = _BF.knnMatch(des2, tdes, k=2)
    good = [m for m, n in (p for p in matches if len(p) == 2)
            if m.distance < 0.75 * n.distance]
    if len(good) < MIN_GOOD:                                  # 關卡1: 配對數
        return None
    src = np.array([view["kp"][m.trainIdx] for m in good], np.float32)
    dst = np.array([kp2[m.queryIdx].pt for m in good], np.float32)
    H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if H is None or inl is None:
        return None
    n_in = int(inl.sum())
    if n_in < MIN_INLIER or n_in < MIN_INLIER_RATIO * len(good):  # 關卡2: 內點
        return None
    x0, y0, x1, y1 = view["bbox"]
    box = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                   np.float32).reshape(-1, 1, 2)
    quad = cv2.perspectiveTransform(box, H).reshape(-1, 2)
    if not _quad_ok(quad, gray.shape[0] * gray.shape[1], view["aspect"]):
        return None                                          # 關卡3: 幾何
    if _appearance_score(gray, H, view) < MIN_APPEAR:        # 關卡4: 外觀比對
        return None
    seed = (int(quad[:, 0].mean()), int(quad[:, 1].mean()))
    return seed, n_in, quad


def find_taught(gray, views):
    """對『多個教學視角』逐一比對，取通過驗證且內點最多者(任一視角中就算認出)。
    回傳 (種子, 'ORB', 內點, 四邊形, 命中視角) 或 (None,'',0,None,None)。"""
    kp2, des2 = _ORB.detectAndCompute(gray, None)
    if des2 is None or len(des2) < MIN_GOOD:
        return None, "", 0, None, None
    best, bview = None, None
    for v in views:
        r = _match_one(kp2, des2, gray, v)
        if r is not None and (best is None or r[1] > best[1]):
            best, bview = r, v
    if best is None:
        return None, "", 0, None, None
    return best[0], "ORB", best[1], best[2], bview


def _robust_depth_at(depth_m, cx, cy, win=8):
    """取 (cx,cy) 附近有效深度的中位數(公尺)；無有效深度回傳 0。"""
    h, w = depth_m.shape
    x0, y0 = max(0, int(cx) - win), max(0, int(cy) - win)
    x1, y1 = min(w, int(cx) + win + 1), min(h, int(cy) + win + 1)
    sub = depth_m[y0:y1, x0:x1]
    v = sub[(sub > 0.1) & (sub < 6.0)]
    return float(np.median(v)) if v.size > 5 else 0.0


def measure_rgb(quad, depth_m, fx, fy, fallback_dist):
    """RGB 輪廓量測(深度不可靠時的備援)：用 ORB 單應性的四邊形 + 中心距離，
    以針孔模型估實際長寬(無高度/厚度)。回傳 dict 或 None。"""
    cx, cy = float(quad[:, 0].mean()), float(quad[:, 1].mean())
    z = _robust_depth_at(depth_m, cx, cy)
    if z <= 0.1:
        z = fallback_dist
    if z <= 0.1:
        return None
    e = [float(np.linalg.norm(quad[(i + 1) % 4] - quad[i])) for i in range(4)]
    real_w = (e[0] + e[2]) / 2.0 * z / fx
    real_h = (e[1] + e[3]) / 2.0 * z / fy
    dx, dy = quad[1] - quad[0]
    tilt = math.degrees(math.atan2(dy, dx))
    while tilt <= -90:
        tilt += 180
    while tilt > 90:
        tilt -= 180
    return {"length": max(real_w, real_h), "width": min(real_w, real_h),
            "height": 0.0, "tilt": tilt, "dist": z, "n_pts": 0,
            "quad": quad.astype(np.int32),
            "center_2d": np.array([cx, cy], np.int32)}


# ---- 參考框定位品檢（特徵比對追蹤物體，忽略背景）----
# 畫框時擷取框內物體的 ORB 特徵當參考，之後比對物體現在的位置/角度與參考框的偏差。
_insp_ema = {"quad": None, "ng": None}      # 平滑四角 + 判定遲滯


def _insp_reset():
    _insp_ema["quad"] = None
    _insp_ema["ng"] = None


def inspect_capture_ref(gray, box):
    """畫框當下擷取框內物體特徵當參考。紋理不足回傳 None。"""
    bx0, bx1 = sorted((box[0], box[2]))
    by0, by1 = sorted((box[1], box[3]))
    if bx1 - bx0 < 24 or by1 - by0 < 24:
        return None
    h, w = gray.shape
    region = np.zeros((h, w), np.uint8)
    region[by0:by1, bx0:bx1] = 255
    kp, des = _ORB.detectAndCompute(gray, region)
    if des is None or len(des) < 12:
        return None
    return {"des": des, "kp": np.array([k.pt for k in kp], np.float32),
            "box": (bx0, by0, bx1, by1)}


def _inspect_quad(gray, ref):
    """在畫面中找物體現在的位置 → 把參考框四角經單應性映射成目前四邊形。"""
    kp2, des2 = _ORB.detectAndCompute(gray, None)
    if des2 is None or len(des2) < 8:
        return None
    matches = _BF.knnMatch(des2, ref["des"], k=2)
    good = [m for m, n in (p for p in matches if len(p) == 2)
            if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return None
    src = np.array([ref["kp"][m.trainIdx] for m in good], np.float32)
    dst = np.array([kp2[m.queryIdx].pt for m in good], np.float32)
    H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if H is None or inl is None or int(inl.sum()) < 8:
        return None
    bx0, by0, bx1, by1 = ref["box"]
    c = np.array([[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]],
                 np.float32).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(c, H).reshape(-1, 2)


def run_inspect(gray, box, ref, thr, color,
                maxdisp=0.22, skew_k=2.0, oob_frac=0.10,
                ema=0.3, hyst=2.0):
    """執行品檢並標註。物體在參考位置→100；位移/歪斜→扣分；移出框→OOB NG。
    四角做 EMA 平滑(靜止時分數穩定)，判定加遲滯(避免門檻附近 OK/NG 閃)。"""
    f = cv2.FONT_HERSHEY_SIMPLEX
    bx0, bx1 = sorted((box[0], box[2]))
    by0, by1 = sorted((box[1], box[3]))
    cv2.rectangle(color, (bx0, by0), (bx1, by1), (230, 230, 230), 1)
    for cx, cy in ((bx0, by0), (bx1, by0), (bx0, by1), (bx1, by1)):
        cv2.drawMarker(color, (cx, cy), (255, 255, 255), cv2.MARKER_CROSS, 12, 1)

    if ref is None:                                   # 還沒擷取到參考(紋理不足)
        _insp_reset()
        cv2.putText(color, "SETUP  texture low", (bx0, max(by0 - 8, 14)),
                    f, 0.6, (0, 200, 255), 2)
        return {"score": 0.0, "ng": True, "oob": False, "skew": 0.0,
                "verdict": "--", "reason": "texture_low"}

    quad = _inspect_quad(gray, ref)
    if quad is None:
        _insp_reset()
        cv2.putText(color, "NG  not found", (bx0, max(by0 - 8, 14)),
                    f, 0.7, (0, 0, 255), 2)
        return {"score": 0.0, "ng": True, "oob": False, "skew": 0.0,
                "verdict": "NG", "reason": "not_found"}

    # 四角 EMA 平滑：壓掉每幀 ORB/單應性的像素抖動 → 靜止時分數穩定
    pq = _insp_ema["quad"]
    if pq is not None and pq.shape == quad.shape:
        quad = pq + (quad - pq) * ema
    _insp_ema["quad"] = quad

    diag = math.hypot(bx1 - bx0, by1 - by0) or 1.0
    refc = np.array([[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]], np.float32)
    disp = float(np.mean(np.linalg.norm(quad - refc, axis=1))) / diag
    dx, dy = quad[1] - quad[0]
    skew = abs(((math.degrees(math.atan2(dy, dx)) + 45) % 90) - 45)
    over = max(bx0 - quad[:, 0].min(), by0 - quad[:, 1].min(),
               quad[:, 0].max() - bx1, quad[:, 1].max() - by1, 0.0) / diag
    oob = over > oob_frac
    score = max(0.0, min(100.0, 100.0 * (1 - disp / maxdisp) - skew * skew_k))

    # 判定遲滯：在門檻 ±hyst 內維持上一個判定，避免邊界跳動
    prev = _insp_ema["ng"]
    if oob:
        ng = True
    elif prev is True:
        ng = score < thr + hyst       # NG → 要明顯變好才轉 OK
    elif prev is False:
        ng = score < thr - hyst       # OK → 要明顯變差才轉 NG
    else:
        ng = score < thr
    _insp_ema["ng"] = ng

    col = (0, 0, 255) if ng else (0, 220, 0)
    cv2.polylines(color, [quad.astype(np.int32)], True, col, 2)
    tag = "OOB" if oob else f"skew{skew:.0f}"
    cv2.putText(color, f"{'NG' if ng else 'OK'} {score:.0f}  {tag}",
                (bx0, max(by0 - 8, 14)), f, 0.7, col, 2)
    return {"score": round(score, 1), "ng": bool(ng), "oob": bool(oob),
            "skew": round(skew, 1), "verdict": "NG" if ng else "OK"}


def _lm_depth(depth_frame, lm, w, h, flip, win=4):
    """取單一關鍵點處的深度(公尺)，0=無效。座標含鏡像還原。"""
    x = min(max(int(lm.x * w), 0), w - 1)
    y = min(max(int(lm.y * h), 0), h - 1)
    dx = (w - 1 - x) if flip else x
    return depth_at(depth_frame, dx, y, win)


def pick_person(poses, depth_frame, w, h, flip, split_thr=0.6):
    """從多個偵測到的人中挑『主體』。
    用深度一致性剔除『把兩人接成一個』的怪骨架(肩與髖深度差過大)，
    再取最近的有效者(主體通常最靠近鏡頭)。回傳 (landmarks, 距離) 或 (None, 0)。"""
    best = None
    for lms in poses:
        sh = [d for d in (_lm_depth(depth_frame, lms[i], w, h, flip)
                          for i in (11, 12)) if d > 0]
        hp = [d for d in (_lm_depth(depth_frame, lms[i], w, h, flip)
                          for i in (23, 24)) if d > 0]
        # 肩、髖深度差太大 → 上半身和下半身不是同一人 → 丟棄
        if sh and hp and abs(np.median(sh) - np.median(hp)) > split_thr:
            continue
        torso = sh + hp
        dist = float(np.median(torso)) if torso else 0.0
        key = dist if dist > 0 else 99.0       # 沒深度的排到最後
        if best is None or key < best[0]:
            best = (key, lms, dist)
    if best is None:
        return None, 0.0
    return best[1], best[2]


OBST_COLS = 64                 # 把視野水平切成幾個方向
OBST_MIN, OBST_MAX = 0.3, 4.0  # 障礙有效距離(公尺)


def detect_fallen(lms):
    """判斷跌倒/躺下，並排除「彎腰」。回傳 (是否跌倒, 軀幹傾角度數)。
    關鍵：彎腰時軀幹水平但「腿仍直立」；躺平時軀幹與腿都水平。"""
    def vis(i):
        return getattr(lms[i], "visibility", 1.0)

    def limb_tilt(a, b):  # 向量 a->b 與垂直線的夾角(0直立 90水平)
        return math.degrees(math.atan2(abs(lms[b].x - lms[a].x),
                                       abs(lms[b].y - lms[a].y) + 1e-6))

    if any(vis(i) < 0.4 for i in (11, 12, 23, 24)):
        return False, 0.0

    # 軀幹傾角(肩中 → 髖中)
    shx = (lms[11].x + lms[12].x) / 2.0
    shy = (lms[11].y + lms[12].y) / 2.0
    hpx = (lms[23].x + lms[24].x) / 2.0
    hpy = (lms[23].y + lms[24].y) / 2.0
    torso = math.degrees(math.atan2(abs(hpx - shx), abs(hpy - shy) + 1e-6))
    if torso <= 50.0:
        return False, round(torso, 1)   # 軀幹還算直立 → 沒跌倒

    # 軀幹水平了，再確認是「整個人躺平」而非「彎腰」
    leg = None
    for hip, ank, kne in ((23, 27, 25), (24, 28, 26)):
        if vis(hip) > 0.4 and vis(ank) > 0.4:
            leg = limb_tilt(hip, ank)
            break
        if vis(hip) > 0.4 and vis(kne) > 0.4:
            leg = limb_tilt(hip, kne)
            break

    if leg is not None:
        fallen = leg > 45.0                 # 腿也水平 → 真的躺/跌；彎腰時腿直立→否
    else:
        # 看不到腿 → 用上半身整體寬高比輔助(躺平會變寬扁)
        keys = [i for i in (0, 11, 12, 23, 24) if vis(i) > 0.4]
        xs = [lms[i].x for i in keys]
        ys = [lms[i].y for i in keys]
        aspect = (max(xs) - min(xs)) / ((max(ys) - min(ys)) + 1e-6)
        fallen = aspect > 1.3

    return fallen, round(torso, 1)


def compute_obstacles(depth_frame, flip):
    """從深度算出每個水平方向的最近障礙距離(公尺)，0=該方向無障礙。
    只取畫面中段高度(避開地板/天花板)。"""
    d = np.asanyarray(depth_frame.get_data()).astype(np.float32)
    d *= depth_frame.get_units()           # 轉公尺
    h, w = d.shape
    band = d[int(0.35 * h):int(0.78 * h), :]   # 中段高度帶
    band = np.where(band > 0.0, band, np.inf)  # 無效→inf
    col = band.min(axis=0)                      # 每行最近距離 (w,)
    step = w // OBST_COLS
    col = col[:step * OBST_COLS].reshape(OBST_COLS, step).min(axis=1)
    out = [
        round(float(v), 2) if OBST_MIN <= v <= OBST_MAX else 0.0
        for v in col
    ]
    if flip:                               # 配合鏡像顯示，左右對調
        out.reverse()
    return out


def compute_obstacle_alert(depth_m_disp, regions, thr):
    """在多個框選範圍(顯示座標的多邊形)內找最近有效深度；任一範圍最近深度<=門檻即警報。
    regions=空清單 → 以整個畫面為單一範圍。
    回傳 (overall_hit, overall_min 公尺, per=[{hit, dist} 每個範圍])。"""
    h, w = depth_m_disp.shape
    polys = regions if regions else [[[0, 0], [w, 0], [w, h], [0, h]]]
    per = []
    o_hit, o_min = False, 0.0
    for poly in polys:
        pts = np.array(poly, dtype=np.int32)
        if pts.shape[0] < 3:               # 點太少 → 無效範圍
            per.append({"hit": False, "dist": 0.0})
            continue
        mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(mask, [pts], 1)
        vals = depth_m_disp[(mask > 0) & (depth_m_disp > 0.1)]  # 範圍內有效深度
        if vals.size == 0:
            per.append({"hit": False, "dist": 0.0})
            continue
        mn = round(float(vals.min()), 2)
        hit = mn <= thr
        per.append({"hit": hit, "dist": mn})
        o_hit = o_hit or hit
        if o_min == 0.0 or mn < o_min:
            o_min = mn
    return o_hit, o_min, per


# IMU 用獨立管線在背景讀(與影像管線分開，避免互相干擾)
_imu = {"roll": 0.0}
_imu_lock = threading.Lock()


def imu_thread():
    """獨立管線持續讀加速度計，更新相機 roll(繞光軸傾角，度)。直立≈0。"""
    try:
        pipe = rs.pipeline()
        cfg = rs.config()
        cfg.enable_stream(rs.stream.accel, rs.format.motion_xyz32f, 100)
        pipe.start(cfg)
    except Exception as e:
        print("IMU 不可用(沒有就跳過自動水平):", e, flush=True)
        return
    print("IMU 已啟動(自動水平可用)", flush=True)
    while True:
        try:
            f = pipe.wait_for_frames(2000)
            af = f.first_or_default(rs.stream.accel)
            if af:
                m = af.as_motion_frame().get_motion_data()
                roll = math.degrees(math.atan2(m.x, -m.y))
                with _imu_lock:
                    _imu["roll"] = round(roll, 1)
        except Exception:
            continue


def read_roll():
    with _imu_lock:
        return _imu["roll"]


# 顯示轉換：水平校正(IMU roll, EMA 平滑) + 把整張畫面置中放進正方形畫布。
# 正方形邊長 = 畫面對角線(640x480→800)，旋轉校正時整張畫面都不會被切到。
LEVEL_SIGN = -1                 # 旋轉方向（若校正方向相反改成 +1）
DISP = 800                      # 預設正方形邊長 = 640x480 對角線
ZOOM = 1.0                      # 預設縮放（1.0=原生，完整放進全畫面）
_roll_ema = {"v": 0.0}


def display_transform(roll, w, h, c_level, zoom, disp):
    """彩色/深度共用的顯示轉換 2x3 仿射矩陣：
    水平校正(可選) + 縮放 zoom + 把來源中心對到 disp×disp 正方形中心。"""
    if c_level:
        _roll_ema["v"] += (roll - _roll_ema["v"]) * 0.2
        angle = LEVEL_SIGN * _roll_ema["v"]
    else:
        angle = 0.0
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, zoom)
    M[0, 2] += disp / 2.0 - w / 2.0     # 來源中心 → 輸出正方形中心
    M[1, 2] += disp / 2.0 - h / 2.0
    return M


def warp_point(M, x, y):
    """用 2x3 仿射矩陣轉換單一點 (x,y)。"""
    return (M[0, 0] * x + M[0, 1] * y + M[0, 2],
            M[1, 0] * x + M[1, 1] * y + M[1, 2])


# ---- 深度後處理（參考 PCL 前處理：去噪 / 邊緣保留平滑 / 補洞）----
_filters = None
_depth_range = {"near": 0.3, "far": 4.0}


def filter_depth(depth_frame):
    """RealSense 標準深度濾鏡鏈：
      spatial(邊緣保留平滑) → temporal(去時間閃爍) → hole_filling(補洞)。
    相當於 PCL 的 FastBilateralFilter + 時間平均 + 補面前處理，
    可大幅降低深度雜訊與破洞，量測與點雲都更穩。"""
    global _filters
    if _filters is None:
        sp = rs.spatial_filter()
        sp.set_option(rs.option.filter_magnitude, 2)
        sp.set_option(rs.option.filter_smooth_alpha, 0.5)
        sp.set_option(rs.option.filter_smooth_delta, 20)
        sp.set_option(rs.option.holes_fill, 2)
        tp = rs.temporal_filter()
        tp.set_option(rs.option.filter_smooth_alpha, 0.4)
        tp.set_option(rs.option.filter_smooth_delta, 20)
        hl = rs.hole_filling_filter()
        hl.set_option(rs.option.holes_fill, 1)
        _filters = (sp, tp, hl)
    sp, tp, hl = _filters
    df = sp.process(depth_frame)
    df = tp.process(df)
    df = hl.process(df)
    return df.as_depth_frame()


def depth_colormap(depth_frame):
    """自動範圍的深度彩色圖：依畫面實際深度範圍(2~98 百分位, EMA 平滑)拉伸
    色階，讓深淺差異明顯；破洞顯示黑色。回傳 (BGR, near, far)。"""
    dm = np.asanyarray(depth_frame.get_data()).astype(np.float32)
    dm *= depth_frame.get_units()
    valid = dm[(dm > 0.1) & (dm < 8.0)]
    if valid.size > 200:
        near = float(np.percentile(valid, 2))
        far = max(float(np.percentile(valid, 98)), near + 0.2)
        _depth_range["near"] += (near - _depth_range["near"]) * 0.1
        _depth_range["far"] += (far - _depth_range["far"]) * 0.1
    near, far = _depth_range["near"], _depth_range["far"]
    norm = np.clip((dm - near) / (far - near), 0.0, 1.0)
    vis = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_JET)
    vis[dm <= 0.1] = 0          # 破洞黑色
    return vis, near, far


# ---- 即時 3D 點雲（下採樣 → 反投影 → 打包二進位給網頁 Three.js）----
PC_STRIDE = 4                   # 每 4 像素取 1 點(160x120≈最多1.9萬點)


def build_pointcloud(depth_frame, color_frame, flip,
                     stride=PC_STRIDE, zmin=0.15, zmax=4.0):
    """彩色點雲打包：[uint32 N][N*3 float32 xyz][N*3 uint8 rgb]。
    座標轉成 Three.js 慣例(Y 上、Z 朝觀者)，單位公尺。"""
    intr = color_frame.profile.as_video_stream_profile().intrinsics
    d = np.asanyarray(depth_frame.get_data()).astype(np.float32)
    d *= depth_frame.get_units()
    c = np.asanyarray(color_frame.get_data())
    d = d[::stride, ::stride]
    c = c[::stride, ::stride]
    h, w = d.shape
    uu, vv = np.meshgrid(np.arange(w, dtype=np.float32) * stride,
                         np.arange(h, dtype=np.float32) * stride)
    ok = (d > zmin) & (d < zmax)
    z = d[ok]
    x = (uu[ok] - intr.ppx) * z / intr.fx
    y = (vv[ok] - intr.ppy) * z / intr.fy
    if flip:
        x = -x
    pts = np.stack([x, -y, -z], axis=1).astype(np.float32)  # 相機系→Three.js
    rgb = np.ascontiguousarray(c[ok][:, ::-1])              # BGR→RGB
    return (np.uint32(pts.shape[0]).tobytes()
            + pts.tobytes() + rgb.tobytes())


def process_frame(state, cfg, frames, align, pc, hands, pose, ts):
    roll = read_roll()              # 由獨立 IMU 執行緒提供
    frames = align.process(frames)
    depth_frame = frames.get_depth_frame()
    color_frame = frames.get_color_frame()
    if not depth_frame or not color_frame:
        return

    with cfg.lock:
        c_pose, c_hands, c_depth, c_flip = cfg.pose, cfg.hands, cfg.depth, cfg.flip
        c_dfilter = cfg.dfilter
        c_measure = cfg.measure
        c_pick = cfg.pick
        c_detect = cfg.detect
        tmpl = cfg.template
        c_teach = cfg.teach_request
        c_level = cfg.level
        c_zoom, c_disp = cfg.zoom, cfg.disp
        c_inspect = cfg.inspect
        c_ibox, c_ithr = cfg.inspect_box, cfg.inspect_thr
        c_iref = cfg.inspect_ref
        c_pcloud = cfg.pcloud
        c_obstacle = cfg.obstacle
        c_oregions, c_odist = list(cfg.obstacle_regions), cfg.obstacle_dist
        c_face, c_face_thr = cfg.face, cfg.face_thr
        c_face_engine, c_face_sim = cfg.face_engine, cfg.face_sim
        c_face_enroll = cfg.face_enroll; cfg.face_enroll = None
        c_face_delete = cfg.face_delete; cfg.face_delete = None
        c_face_clear = cfg.face_clear; cfg.face_clear = False
        do_scan = cfg.scan
        cfg.scan = False
        do_snapshot = cfg.snapshot
        cfg.snapshot = False

    # 深度後處理濾鏡（去噪/補洞）→ 量測、點雲、深度圖一起受益
    if c_dfilter:
        try:
            depth_frame = filter_depth(depth_frame)
        except Exception:
            pass

    color = np.asanyarray(color_frame.get_data())
    if c_flip:
        color = cv2.flip(color, 1)
    h, w = color.shape[:2]
    rgb = cv2.cvtColor(color, cv2.COLOR_BGR2RGB)
    mpimg = gu.to_mp_image(rgb)

    status = {"person": False, "dist": 0.0, "fingers": 0,
              "gesture": "-", "finger_states": [False] * 5,
              "fallen": False, "tilt": 0.0, "roll": roll,
              "measure": None, "picked": False,
              "has_template": bool(tmpl), "detect": c_detect,
              "n_views": len(tmpl) if tmpl else 0,
              "matched": False, "match_via": "", "inspect": None}

    if c_pose:
        pres = pose.detect_for_video(mpimg, ts)
        if pres.pose_landmarks:
            # 多人偵測 → 用深度挑主體、剔除「兩人接成一個」的怪骨架
            lms, pdist = pick_person(pres.pose_landmarks, depth_frame,
                                     w, h, c_flip)
            if lms is not None:
                status["person"] = True
                gu.draw_landmarks(color, lms, gu.POSE_CONNECTIONS,
                                  color=(255, 180, 0), pt_color=(0, 0, 255))
                if pdist <= 0:                    # 軀幹無深度 → 退而用鼻尖
                    nose = lms[0]
                    nx, ny = int(nose.x * w), int(nose.y * h)
                    dx = (w - 1 - nx) if c_flip else nx
                    pdist = depth_at(depth_frame, dx, ny)
                status["dist"] = pdist
                status["fallen"], status["tilt"] = detect_fallen(lms)

    if c_hands:
        hres = hands.detect_for_video(mpimg, ts)
        parts = []
        for i, hand_lm in enumerate(hres.hand_landmarks):
            gu.draw_landmarks(color, hand_lm, gu.HAND_CONNECTIONS)
            cnt, fingers = gu.count_fingers(hand_lm)
            gesture = gu.guess_gesture(cnt, fingers)
            status["fingers"] += cnt
            parts.append(gesture)
            # 只記錄第一隻手的每指狀態（給前端 debug 顯示）
            if i == 0:
                status["finger_states"] = [bool(x) for x in fingers]
        if parts:
            status["gesture"] = " | ".join(parts)

    # 顯示轉換：水平校正(可選) + 縮放 + 置中放進正方形（彩色/深度/量測共用）
    M_disp = display_transform(roll, w, h, c_level, c_zoom, c_disp)
    color = cv2.warpAffine(color, M_disp, (c_disp, c_disp),
                           flags=cv2.INTER_LINEAR)

    # 障礙物檢測：在多個框選範圍(顯示座標)內找最近深度，任一<=門檻 → 警報。
    # 範圍視覺由前端 canvas 疊圖負責繪製(座標同為顯示空間，與此處偵測一致)。
    if c_obstacle:
        od = np.asanyarray(depth_frame.get_data()).astype(np.float32)
        od *= depth_frame.get_units()           # 轉公尺
        if c_flip:                              # 與顯示同方向：先鏡像
            od = cv2.flip(od, 1)
        od = cv2.warpAffine(od, M_disp, (c_disp, c_disp),
                            flags=cv2.INTER_NEAREST)  # 再套用顯示轉換 → 與框座標一致
        o_hit, o_min, o_per = compute_obstacle_alert(od, c_oregions, c_odist)
        status["obstacle"] = {"on": True, "hit": o_hit, "dist": o_min,
                              "thr": c_odist, "regions": c_oregions,
                              "per": o_per}
    else:
        status["obstacle"] = {"on": False, "hit": False, "dist": 0.0,
                              "thr": c_odist, "regions": c_oregions, "per": []}

    # 人臉辨識：偵測 → 比對已註冊樣本 → 在臉上標名字。資料存 faces/，重開可用。
    if c_face_clear:
        fu.DB.clear(); status["msg"] = "已清除所有人臉資料"
    if c_face_delete:
        fu.DB.delete(c_face_delete); status["msg"] = f"已刪除「{c_face_delete}」的人臉資料"
    faces_out = []
    fu.DB.set_engine(c_face_engine)                       # 切換引擎(內部改變才重載)
    fu.DB.thr_lbph, fu.DB.thr_arc = c_face_thr, c_face_sim
    if c_face or c_face_enroll:
        dets = fu.detect(color)                           # 顯示座標偵測 → 框對齊串流
        if c_face_enroll:                                 # 一次性：擷取最大那張臉
            if dets:
                ok, msg = fu.DB.enroll(c_face_enroll, color, dets[0])
            else:
                msg = "沒偵測到人臉，正對鏡頭、靠近一點再擷取"
            status["msg"] = msg
        if c_face:
            labels = []
            for d in dets:
                name, score = fu.DB.recognize(color, d)
                x, y, bw, bh = d["box"]
                col = (0, 220, 0) if name else (0, 170, 255)   # 綠=認得 橙=未知
                cv2.rectangle(color, (x, y), (x + bw, y + bh), col, 2)
                labels.append((x + 2, max(0, y - 28), name if name else "未知", col))
                faces_out.append({"name": name or "未知",
                                  "score": score, "box": [x, y, bw, bh]})
            if labels:
                fu.draw_labels(color, labels)
    status["faces"] = faces_out
    status["faces_db"] = fu.DB.people()
    status["face_engine"] = fu.DB.effective()             # 實際使用引擎(可能退回 lbph)
    status["arc_available"] = fu.DB.arc_available()

    # 物體量測（點選 or 自動辨識 → 3D 點雲 + PCA OBB；水平校正後座標系）
    if c_measure:
        intr = color_frame.profile.as_video_stream_profile().intrinsics
        # 深度轉成與顯示同方向：先鏡像、再水平校正（內參同步轉換）
        depth_m = np.asanyarray(depth_frame.get_data()).astype(np.float32)
        depth_m *= depth_frame.get_units()
        ppx = (w - 1 - intr.ppx) if c_flip else intr.ppx
        ppy = intr.ppy
        if c_flip:
            depth_m = cv2.flip(depth_m, 1)
        depth_m = cv2.warpAffine(depth_m, M_disp, (c_disp, c_disp),
                                 flags=cv2.INTER_NEAREST)
        ppx, ppy = warp_point(M_disp, ppx, ppy)
        fx_d, fy_d = intr.fx * c_zoom, intr.fy * c_zoom   # 縮放後焦距同步

        # 需要乾淨(無疊加)的顯示座標灰階，供 ORB 教學或辨識使用
        gray = None
        if c_detect or c_teach:
            clean = np.asanyarray(color_frame.get_data())
            if c_flip:
                clean = cv2.flip(clean, 1)
            clean = cv2.warpAffine(clean, M_disp, (c_disp, c_disp))
            gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)

        # 決定目標種子：自動辨識(有樣板) 優先，否則手動點選
        seed, via, quad = None, "", None
        if c_detect and tmpl:                    # tmpl 是視角清單(list)
            # 多視角辨識 + 遲滯：命中累積到 DET_CONFIRM 才確認；確認後容忍漏失
            f_seed, _, _, f_quad, f_view = find_taught(gray, tmpl)
            if f_seed is not None:
                _track["hits"] = min(_track["hits"] + 1, DET_CONFIRM)
                _track["miss"] = 0
                if _track["hits"] >= DET_CONFIRM:
                    _track["active"] = True
                _track["seed"], _track["quad"] = f_seed, f_quad
                _track["dist"] = f_view["dist"]
            else:
                _track["miss"] += 1
                if _track["miss"] > DET_LOSE:
                    _track_reset()
            if _track["active"]:                 # 命中用新位置，漏失沿用上一個
                seed, quad, via = _track["seed"], _track["quad"], "ORB"
        elif not c_detect:
            seed = c_pick
            _track_reset()

        # 量測：優先 3D 點雲；自動辨識時 3D 失敗則用 RGB 輪廓備援
        m, mask, rgb_only = None, None, False
        if seed is not None:
            mask = segment_click(depth_m, seed)
            if mask is not None and int(mask.sum()) >= 400:
                m = measure_mask_3d(depth_m, mask, fx_d, fy_d, ppx, ppy)
        if m is None and quad is not None:        # ORB 已認出但 3D 量測失敗
            m = measure_rgb(quad, depth_m, fx_d, fy_d, _track["dist"])
            rgb_only = m is not None

        # 追蹤中：本幀有量到就更新快取；漏失就沿用上一筆(撐住，避免閃爍)
        if c_detect and _track["active"]:
            if m is not None:
                _track["m"], _track["rgb"] = m, rgb_only
            elif _track["m"] is not None:
                m, rgb_only, mask = _track["m"], _track["rgb"], None

        # 教學請求：把目前物體存成樣板（深度分割失敗則用點擊方框，純 RGB 特徵）
        if c_teach:
            tmask = mask
            if tmask is None and c_pick is not None:
                tmask = np.zeros((c_disp, c_disp), bool)
                bx, by = c_pick
                s = 90
                tmask[max(0, by - s):by + s, max(0, bx - s):bx + s] = True
            tdist = m["dist"] if m is not None else 0.5
            tdims = ((m["length"], m["width"], m["height"])
                     if m is not None else (0.0, 0.0, 0.0))
            tnew = (build_template(gray, tmask, tdims, tdist)
                    if tmask is not None and tmask.sum() > 50 else None)
            if tnew is not None and tnew["n_orb"] >= MIN_ORB:
                with cfg.lock:               # 累積成多視角清單
                    views = list(cfg.template) if cfg.template else []
                    views.append(tnew)
                    if len(views) > MAX_VIEWS:
                        views = views[-MAX_VIEWS:]
                    cfg.template = views
                    nv = len(views)
                _track_reset()                   # 新增視角 → 重新確認
                status["msg"] = f"已新增視角 {nv} (ORB特徵 {tnew['n_orb']})"
                status["has_template"] = True
                status["n_views"] = nv
            elif tnew is not None:
                status["msg"] = ("教學失敗：特徵太少，請對準有圖案/文字的面，"
                                 "光線充足、距離 0.4~0.6m")
            else:
                status["msg"] = "教學失敗：請先點選一個物體"
            with cfg.lock:
                cfg.teach_request = False

        if m is not None:
            # 數值平滑(EMA)：辨識模式下減少抖動
            L, Wd, Hh, Dd = m["length"], m["width"], m["height"], m["dist"]
            if c_detect and _track["active"]:
                e = _track["ema"]
                for k, v in (("L", L), ("W", Wd), ("H", Hh), ("D", Dd)):
                    e[k] = v if e.get(k) is None else e[k] + (v - e[k]) * 0.4
                L, Wd, Hh, Dd = e["L"], e["W"], e["H"], e["D"]
            status["measure"] = {
                "length": round(L, 4), "width": round(Wd, 4),
                "height": round(Hh, 4), "tilt": round(m["tilt"], 1),
                "dist": round(Dd, 3), "n_pts": m["n_pts"],
            }
            status["picked"] = True
            status["matched"] = bool(c_detect)
            status["match_via"] = (via + ("/RGB" if rgb_only else "/3D")) \
                if c_detect else ""
            if rgb_only:
                # RGB 輪廓量測：畫四邊形(黃) + 中心
                cv2.polylines(color, [m["quad"]], True, (0, 255, 255), 2)
                cv2.circle(color, tuple(m["center_2d"]), 4, (0, 0, 255), -1)
            else:
                # 3D：物體輪廓(橘) + 包圍盒(綠) + 中心(紅)
                if mask is not None:             # 撐住的快取幀沒有 mask
                    cnts, _ = cv2.findContours(mask.astype(np.uint8),
                                               cv2.RETR_EXTERNAL,
                                               cv2.CHAIN_APPROX_SIMPLE)
                    cv2.drawContours(color, cnts, -1, (0, 160, 255), 1)
                cor = m["corners_2d"]
                for i, j in OBB_EDGES:
                    cv2.line(color, tuple(cor[i]), tuple(cor[j]), (0, 255, 0), 2)
                cv2.circle(color, tuple(m["center_2d"]), 4, (0, 0, 255), -1)
            tag = (f"MATCH:{via}" + ("(RGB)" if rgb_only else "(3D)")) \
                if c_detect else ""
            lines = [f"L {L*100:.1f}cm", f"W {Wd*100:.1f}cm"]
            if Hh > 0:
                lines.append(f"H {Hh*100:.1f}cm")
            lines += [f"tilt {m['tilt']:.0f}deg", f"dist {Dd:.2f}m"]
            yy = 28
            if tag:
                cv2.putText(color, tag, (12, yy),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                yy += 24
            for tline in lines:
                cv2.putText(color, tline, (12, yy),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                yy += 24
            # 手動模式：簡單追蹤（自動辨識每幀重找，不需追蹤）
            if not c_detect and mask is not None:
                ys2, xs2 = np.nonzero(mask)
                nseed = (int(xs2.mean()), int(ys2.mean()))
                with cfg.lock:
                    if cfg.pick == c_pick:
                        cfg.pick = nseed
        else:
            if c_detect:
                hint = ("TEACH AN OBJECT FIRST" if tmpl is None
                        else "SEARCHING... object not in view")
            else:
                hint = ("TARGET LOST - click again" if c_pick is not None
                        else "CLICK AN OBJECT TO MEASURE")
            cv2.putText(color, hint, (12, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    # 參考框定位品檢（特徵比對追蹤物體，忽略背景）
    if c_inspect and c_ibox is not None:
        cl = np.asanyarray(color_frame.get_data())
        if c_flip:
            cl = cv2.flip(cl, 1)
        cl = cv2.warpAffine(cl, M_disp, (c_disp, c_disp))
        gimg = cv2.cvtColor(cl, cv2.COLOR_BGR2GRAY)
        if c_iref is None:                            # 畫框後擷取一次參考
            c_iref = inspect_capture_ref(gimg, c_ibox)
            if c_iref is not None:
                with cfg.lock:
                    cfg.inspect_ref = c_iref
                _insp_reset()                         # 新參考 → 重置平滑
        status["inspect"] = run_inspect(gimg, c_ibox, c_iref, c_ithr, color)

    # 障礙小地圖（每個方向最近障礙距離）
    status["obstacles"] = compute_obstacles(depth_frame, c_flip)

    # 跌倒自動截圖存證（存含骨架的當下畫面）
    if do_snapshot:
        try:
            os.makedirs(FALL_DIR, exist_ok=True)
            fn = os.path.join(FALL_DIR,
                              f"fall_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
            ok2, buf = cv2.imencode(".jpg", color, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok2:
                with open(fn, "wb") as fp:   # 用 open 避免中文路徑問題
                    fp.write(buf.tobytes())
                status["msg"] = f"已存跌倒截圖 {fn}"
                print(status["msg"], flush=True)
        except Exception as e:
            print("存跌倒截圖失敗:", e, flush=True)

    if c_depth:
        depth_vis, dn, df_ = depth_colormap(depth_frame)
        cv2.putText(depth_vis, f"{dn:.2f}-{df_:.2f}m", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        if c_flip:
            depth_vis = cv2.flip(depth_vis, 1)
        depth_vis = cv2.warpAffine(depth_vis, M_disp, (c_disp, c_disp),
                                   flags=cv2.INTER_NEAREST)
        color = np.hstack((color, depth_vis))

    if do_scan:
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            pc.map_to(color_frame)
            pts = pc.calculate(depth_frame)
            fn = os.path.join(OUT_DIR,
                              f"scan_{time.strftime('%Y%m%d_%H%M%S')}.ply")
            pts.export_to_ply(fn, color_frame)
            status["msg"] = f"已存點雲 {fn}"
            print(status["msg"])
        except Exception as e:
            print("存點雲失敗:", e)

    # 即時 3D 點雲（檢視器開啟時每 2 幀更新一次,約 15fps）
    if c_pcloud and ts % 2 == 0:
        try:
            state.pc = build_pointcloud(depth_frame, color_frame, c_flip)
        except Exception:
            pass

    ok, jpeg = cv2.imencode(".jpg", color, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if ok:
        state.latest = {"jpeg": jpeg.tobytes(), "status": status}  # 整包換


class State:
    """相機輸出與設定（REST 共用）"""
    def __init__(self):
        self.cfg = Config()              # 單一共用設定
        self.latest = {"jpeg": None, "status": {}}   # 最新影像/狀態
        self.pc = None                   # 最新點雲二進位
        self.lock = threading.Lock()


# ---------- REST API（FastAPI，與 TCP 並存）----------
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def make_api(state):
    from fastapi import FastAPI, Request, Response
    from fastapi.responses import StreamingResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    api = FastAPI(title="D435i Vision REST API")
    api.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    @api.get("/")
    def index():
        try:
            with open(os.path.join(WEB_DIR, "index.html"),
                      "r", encoding="utf-8") as f:
                return Response(content=f.read(), media_type="text/html")
        except FileNotFoundError:
            return Response("web/index.html 不存在", status_code=404)

    @api.get("/logo.svg")
    def logo():
        try:
            with open(os.path.join(WEB_DIR, "ching-tech.svg"), "rb") as f:
                return Response(content=f.read(), media_type="image/svg+xml")
        except FileNotFoundError:
            return Response(status_code=404)

    def set_cfg(d):
        c = state.cfg
        with c.lock:
            for k in ("pose", "hands", "depth", "flip", "dfilter",
                      "measure", "level", "detect", "inspect", "pcloud",
                      "obstacle", "face"):
                if k in d:
                    setattr(c, k, bool(d[k]))
            if "obstacle_dist" in d:
                c.obstacle_dist = float(max(0.2, min(6.0, d["obstacle_dist"])))
            if "face_thr" in d:
                c.face_thr = float(max(20, min(130, d["face_thr"])))
            if "face_sim" in d:
                c.face_sim = float(max(0.1, min(0.9, d["face_sim"])))
            if "face_engine" in d:
                c.face_engine = "arcface" if d["face_engine"] == "arcface" else "lbph"
            if "zoom" in d:
                c.zoom = float(max(0.3, min(3.0, d["zoom"])))
            if "disp" in d:
                c.disp = int(max(400, min(1200, d["disp"])))
            if "inspect_thr" in d:
                c.inspect_thr = float(max(0, min(100, d["inspect_thr"])))

    @api.get("/status")
    def status():
        return JSONResponse(state.latest.get("status", {}))

    @api.get("/measure")
    def measure():
        return JSONResponse((state.latest.get("status") or {}).get("measure"))

    @api.get("/frame.jpg")
    def frame():
        jb = state.latest.get("jpeg")
        if jb is None:
            return Response(status_code=503)
        return Response(content=jb, media_type="image/jpeg")

    @api.get("/stream")
    def stream():
        def gen():
            while True:
                jb = state.latest.get("jpeg")
                if jb:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                           + jb + b"\r\n")
                time.sleep(1.0 / 30)
        return StreamingResponse(
            gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    @api.post("/config")
    async def config(req: Request):
        set_cfg(await req.json())
        return {"ok": True}

    @api.post("/action/pick")
    async def pick(req: Request):
        d = await req.json()
        with state.cfg.lock:
            state.cfg.pick = (int(d["u"]), int(d["v"]))
        return {"ok": True}

    @api.post("/action/clear_pick")
    def clear_pick():
        with state.cfg.lock:
            state.cfg.pick = None
        return {"ok": True}

    @api.post("/action/teach")
    def teach():
        with state.cfg.lock:
            state.cfg.teach_request = True
        return {"ok": True}

    @api.post("/action/detect")
    async def detect(req: Request):
        d = await req.json()
        with state.cfg.lock:
            state.cfg.detect = bool(d.get("on", True))
        return {"ok": True}

    @api.post("/action/clear_template")
    def clear_template():
        with state.cfg.lock:
            state.cfg.template = None
            state.cfg.detect = False
        return {"ok": True}

    @api.post("/scan")
    def scan():
        with state.cfg.lock:
            state.cfg.scan = True
        return {"ok": True, "note": "已觸發，完成路徑見 /status 的 msg"}

    @api.post("/inspect/box")
    async def inspect_box(req: Request):
        d = await req.json()
        with state.cfg.lock:
            state.cfg.inspect_box = (int(d["x0"]), int(d["y0"]),
                                     int(d["x1"]), int(d["y1"]))
            state.cfg.inspect_ref = None
            state.cfg.inspect = True
        return {"ok": True}

    @api.post("/inspect/clear")
    def inspect_clear():
        with state.cfg.lock:
            state.cfg.inspect_box = None
            state.cfg.inspect_ref = None
        return {"ok": True}

    @api.post("/obstacle/regions")
    async def obstacle_regions(req: Request):
        d = await req.json()
        regions = []
        for poly in (d.get("regions") or []):
            pts = [[int(p[0]), int(p[1])] for p in poly if len(p) >= 2]
            if len(pts) >= 3:              # 至少三點才算有效範圍
                regions.append(pts)
        with state.cfg.lock:
            state.cfg.obstacle_regions = regions
        return {"ok": True, "n": len(regions)}

    @api.post("/obstacle/clear")
    def obstacle_clear():
        with state.cfg.lock:
            state.cfg.obstacle_regions = []
        return {"ok": True}

    @api.post("/face/enroll")
    async def face_enroll(req: Request):
        d = await req.json()
        name = (d.get("name") or "").strip()
        if not name:
            return {"ok": False, "msg": "需要名字"}
        with state.cfg.lock:
            state.cfg.face = True            # 註冊需開著偵測才看得到臉框
            state.cfg.face_enroll = name
        return {"ok": True}

    @api.post("/face/delete")
    async def face_delete(req: Request):
        d = await req.json()
        with state.cfg.lock:
            state.cfg.face_delete = (d.get("name") or "").strip() or None
        return {"ok": True}

    @api.post("/face/clear")
    def face_clear():
        with state.cfg.lock:
            state.cfg.face_clear = True
        return {"ok": True}

    @api.post("/action/fall_snapshot")
    def fall_snapshot():
        with state.cfg.lock:
            state.cfg.snapshot = True
        return {"ok": True}

    @api.get("/pointcloud.bin")
    def pointcloud():
        if state.pc is None:
            return Response(status_code=503)
        return Response(content=state.pc,
                        media_type="application/octet-stream")

    return api


def pick_free_port(start, tries=10):
    """回傳第一個可綁定的埠（避免舊行程殘留占用 8000 時整個掛掉）。"""
    import socket
    for p in range(start, start + tries):
        try:
            # 注意：不可用 SO_REUSEADDR——Windows 下會誤綁到已被監聽的埠
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((API_HOST, p))
            s.close()
            return p
        except OSError:
            print(f"埠 {p} 被占用，改試 {p + 1} ...", flush=True)
    return start


def run_api(state, port):
    """在背景執行緒跑 uvicorn（不在主執行緒，需停用 signal handler）。"""
    try:
        import uvicorn
        cfg = uvicorn.Config(make_api(state), host=API_HOST, port=port,
                             log_level="warning")
        server = uvicorn.Server(cfg)
        server.install_signal_handlers = lambda: None
        server.run()
    except Exception as e:
        print("REST API 啟動失敗(略過):", e, flush=True)


def open_camera():
    """啟動相機；若無回應(常見於反覆重啟後 USB stall)，自動硬體重置後重試"""
    for attempt in range(2):
        pipe = rs.pipeline()
        cfg_rs = rs.config()
        cfg_rs.enable_stream(rs.stream.depth, W, H, rs.format.z16, FPS)
        cfg_rs.enable_stream(rs.stream.color, W, H, rs.format.bgr8, FPS)
        profile = pipe.start(cfg_rs)
        # High Accuracy 預設：只保留高信心深度，減少反光/近距離的錯誤點
        try:
            ds = profile.get_device().first_depth_sensor()
            if ds.supports(rs.option.visual_preset):
                ds.set_option(rs.option.visual_preset,
                              float(rs.rs400_visual_preset.high_accuracy))
        except Exception as e:
            print("設定 High Accuracy 預設失敗(略過):", e, flush=True)
        ok = 0
        for _ in range(15):          # 暖機並偵測是否真的有畫面
            try:
                pipe.wait_for_frames(3000)
                ok += 1
            except RuntimeError:
                pass
            if ok >= 5:
                return pipe
        # 無回應 → 硬體重置後重試
        print("相機無回應，執行硬體重置 ...", flush=True)
        try:
            pipe.stop()
        except RuntimeError:
            pass
        try:
            rs.context().query_devices()[0].hardware_reset()
        except Exception:
            pass
        time.sleep(5)
    return pipe   # 盡力而為，交給主迴圈


def main():
    # 先載入模型（耗時數秒），再開相機，避免開相機後出現「沒 poll」的空檔導致串流 stall
    print("載入 MediaPipe 模型中 ...", flush=True)
    hands = gu.create_hand_landmarker(max_hands=2)
    pose = gu.create_pose_landmarker()

    print("啟動相機 ...", flush=True)
    pipe = open_camera()
    align = rs.align(rs.stream.color)
    pc = rs.pointcloud()

    # IMU 獨立管線(自動水平)，與影像管線分開
    threading.Thread(target=imu_thread, daemon=True).start()

    state = State()
    port = pick_free_port(API_PORT)
    threading.Thread(target=run_api, args=(state, port), daemon=True).start()
    print(f"REST API 就緒 http://{API_HOST}:{port} "
          f"(/status /stream /frame.jpg /config /scan ...)", flush=True)

    def _open_browser():
        time.sleep(2.0)                  # 等伺服器起來再開
        try:
            webbrowser.open(f"http://localhost:{port}/")
        except Exception:
            pass
    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"統一介面: http://localhost:{port}/", flush=True)

    ts = 0
    try:
        # 相機持續讀取並處理（REST 隨時有最新影像可取）
        while True:
            try:
                frames = pipe.wait_for_frames(10000)
            except RuntimeError:
                print("相機讀取逾時，重試 ...", flush=True)
                continue
            ts += 1
            process_frame(state, state.cfg, frames, align, pc,
                          hands, pose, ts)
    finally:
        pipe.stop()
        hands.close()
        pose.close()


if __name__ == "__main__":
    main()
