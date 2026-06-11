# D435i 視覺量測系統

Intel RealSense D435i 深度相機的整合視覺系統：**3D 物體量測、定位品檢、教學辨識、人體/手勢偵測**。
單一 Python 後端 + 瀏覽器統一介面（REST API / MJPEG）。

## 快速開始

```powershell
pip install -r requirements.txt
./start.ps1        # 或 python vision_server.py
```

啟動後自動開啟瀏覽器 → **http://localhost:8000/**（手機同網段可連 `http://<本機IP>:8000/`）

## 功能

| 功能 | 說明 |
|---|---|
| 📐 物體量測 | 點選畫面物體 → 深度分割 → 3D 點雲 PCA 包圍盒 → 長/寬/高/水平傾斜/距離 |
| 📦 定位品檢 | 拖曳畫參考框 → ORB 特徵追蹤物體 → 位移/歪斜評分(0~100)、OK/NG 判定 |
| 🎯 教學辨識 | 多視角 ORB 樣板(各角度各教一次) + 四道驗證 → 物體入畫面自動辨識量測 |
| 👁 人體偵測 | 多人骨架 + 深度挑主體、跌倒偵測 + 警報彈窗 + 自動截圖(`falls/`) |
| ✋ 手勢控制 | 手指數 → 模擬機器人(前進/後退/轉向/避障雷達) |
| 🚧 障礙物檢測 | 在畫面畫**多個任意形狀範圍**(矩形拖曳/多邊形點擊/手繪圈選) → 範圍內最近物體 ≤ 觸發距離時該框亮紅 + 跳提醒視窗(持續到障礙離開);各範圍即時顯示距離 |
| 📸 點雲擷取 | 一鍵存彩色點雲 `.ply`(`scans/`) |
| ⚙ 顯示 | IMU 自動水平校正、縮放/畫布滑桿、深度濾鏡(去噪/補洞)、自動範圍深度圖 |

## 架構

```
vision_server.py（唯一程式，獨佔相機）
  ├─ 相機主迴圈：取像 → 推論/量測 → state.latest(最新影像+狀態)
  ├─ imu_thread：加速度計 → roll(自動水平)
  └─ FastAPI(背景執行緒, :8000)
       ├─ GET  /            統一網頁介面(web/index.html)
       ├─ GET  /stream      MJPEG 即時影像
       ├─ GET  /frame.jpg   單張影像
       ├─ GET  /status /measure   狀態/量測 JSON
       ├─ POST /config      {pose,hands,depth,flip,dfilter,measure,level,
       │                     detect,inspect,zoom,disp,inspect_thr,
       │                     obstacle,obstacle_dist}
       ├─ POST /action/pick {u,v} · /clear_pick · /teach · /detect
       │       /clear_template · /fall_snapshot
       ├─ POST /inspect/box {x0,y0,x1,y1} · /inspect/clear
       ├─ POST /obstacle/regions {regions:[[[x,y],…],…]} · /obstacle/clear
       └─ POST /scan        擷取點雲
```

互動文件：http://localhost:8000/docs

## 檔案

| 檔案 | 用途 |
|---|---|
| `vision_server.py` | 後端核心(相機 + 運算 + REST + 網頁) |
| `web/index.html` | 統一網頁介面(單檔,零建置) |
| `gesture_utils.py` | MediaPipe 封裝(手/姿態、手指計數) |
| `start.ps1` | 一鍵啟動 |
| `scan_3d.py` / `robot_vision.py` / `realsense_gui.py` | 選用 REST 客戶端小工具 |
| `models/` | MediaPipe 模型 |
| `scans/` `falls/` | 點雲輸出 / 跌倒截圖 |
| `robot_ui/` | (已棄用) 舊 Rust 桌面 UI,可整個刪除 |

## 介面操作

- **情境模式**：📐量測 / 📦品檢 / 👁監看 / ○待機 一鍵切換
- **量測**：點畫面上的物體；**品檢**：拖曳畫參考框,移動物體看 OK/NG 分數
- **教學**：點選物體 → ➕新增視角(各角度重複) → 開自動辨識
- **障礙檢測**：開🚧障礙 → 選畫法(▭矩形拖曳 / ⬠多邊形點擊,雙擊或點回起點收尾 / ✏手繪圈選) → 在畫面畫一或多個範圍;拉「觸發距離」設門檻,右側清單可單獨刪除或一鍵清除(不畫＝整個畫面)
- 面板標題可點擊收合；模擬機器人區塊預設收合(展開才運算)
- 鍵盤(機器人)：↑↓←→ 移動、空白停、R 歸位

## 常用調校(vision_server.py)

| 參數 | 位置 | 作用 |
|---|---|---|
| `segment_click(tol=0.03)` | 點選分割 | 深度連通容差,物體貼桌面外漏時調小 |
| `run_inspect(maxdisp/oob_frac/ema/hyst)` | 品檢 | 評分斜率/超框判定/平滑/判定死區 |
| `MIN_GOOD/MIN_INLIER/MIN_APPEAR` | 辨識 | 越大越嚴(不誤認)、越小越易認 |
| `DET_CONFIRM/DET_LOSE` | 辨識遲滯 | 防閃爍的確認/容忍幀數 |
| `LEVEL_SIGN` | 水平校正 | 校正方向相反時改成 +1 |
