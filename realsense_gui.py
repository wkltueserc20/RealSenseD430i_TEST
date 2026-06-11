"""
realsense_gui.py  —  REST 控制台（Tkinter，不直接開相機）

改版：相機由 vision_server.py 獨佔，本介面改成 REST 客戶端，
可與 Rust UI 同時連到同一台相機。提供：即時影像、點選物體量測、
開關設定、放大/畫布滑桿、教學/辨識、擷取點雲、即時狀態。

執行前：先啟動  python vision_server.py
執行：    python realsense_gui.py
"""

import io
import requests
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

API = "http://127.0.0.1:8000"
DISP_W = 600                      # 影像顯示寬度(像素)


class App:
    def __init__(self, root):
        self.root = root
        root.title("D435i REST 控制台")
        self.scale = 1.0

        self.vars = {k: tk.BooleanVar(value=v) for k, v in {
            "pose": False, "hands": False, "depth": False, "dfilter": True,
            "flip": True, "measure": False, "level": True, "detect": False,
        }.items()}
        self.zoom = tk.DoubleVar(value=1.0)
        self.disp = tk.IntVar(value=800)

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)
        self.video = ttk.Label(main, cursor="cross")
        self.video.grid(row=0, column=0, sticky="nw")
        self.video.bind("<Button-1>", self.on_click)

        p = ttk.Frame(main, padding=(12, 0))
        p.grid(row=0, column=1, sticky="n")

        labels = {"pose": "偵測人", "hands": "偵測手勢", "depth": "深度圖",
                  "dfilter": "深度濾鏡", "flip": "鏡像", "measure": "物體量測",
                  "level": "IMU 自動水平", "detect": "自動辨識"}
        for k, txt in labels.items():
            ttk.Checkbutton(p, text=txt, variable=self.vars[k],
                            command=self.push_cfg).pack(anchor="w")

        ttk.Label(p, text="放大倍率").pack(anchor="w", pady=(8, 0))
        ttk.Scale(p, from_=0.5, to=2.5, variable=self.zoom, orient="horizontal",
                  length=180, command=lambda e: self.push_cfg()).pack(fill="x")
        ttk.Label(p, text="畫布 (px)").pack(anchor="w")
        ttk.Scale(p, from_=480, to=1100, variable=self.disp, orient="horizontal",
                  length=180, command=lambda e: self.push_cfg()).pack(fill="x")

        for txt, fn in [("📸 擷取點雲 (.ply)", self.scan),
                        ("🎯 教學/新增視角", self.teach),
                        ("🗑 清除樣板", self.clear_tmpl),
                        ("✖ 清除選取", self.clear_pick)]:
            ttk.Button(p, text=txt, command=fn).pack(fill="x", pady=2)

        box = ttk.LabelFrame(p, text="即時狀態", padding=8)
        box.pack(fill="x", pady=8)
        self.lbl = {}
        for k in ("人", "距離", "手勢", "量測", "辨識", "訊息"):
            self.lbl[k] = ttk.Label(box, text=f"{k}: -")
            self.lbl[k].pack(anchor="w")

        ttk.Label(p, text="(點影像可選取要量測的物體)").pack(anchor="w")

        self.push_cfg()
        self.update_video()
        self.update_status()

    # ---- REST ----
    def post(self, path, body=None):
        try:
            requests.post(f"{API}{path}", json=body or {}, timeout=2)
        except requests.RequestException as e:
            print("POST 失敗", path, e)

    def push_cfg(self):
        body = {k: v.get() for k, v in self.vars.items()}
        body["zoom"] = round(self.zoom.get(), 2)
        body["disp"] = int(self.disp.get())
        self.post("/config", body)

    def scan(self):
        self.post("/scan")

    def teach(self):
        self.post("/action/teach")

    def clear_tmpl(self):
        self.post("/action/clear_template")

    def clear_pick(self):
        self.post("/action/clear_pick")

    def on_click(self, e):
        if self.scale > 0:
            self.post("/action/pick",
                      {"u": int(e.x / self.scale), "v": int(e.y / self.scale)})

    # ---- 輪詢 ----
    def update_video(self):
        try:
            r = requests.get(f"{API}/frame.jpg", timeout=2)
            if r.status_code == 200:
                im = Image.open(io.BytesIO(r.content))
                self.scale = DISP_W / im.size[0]
                im = im.resize((DISP_W, int(im.size[1] * self.scale)))
                self.imgtk = ImageTk.PhotoImage(im)
                self.video.config(image=self.imgtk)
        except requests.RequestException:
            pass
        self.root.after(40, self.update_video)

    def update_status(self):
        try:
            s = requests.get(f"{API}/status", timeout=2).json()
            self.lbl["人"].config(
                text=f"人: {'✅ 有人' if s.get('person') else '⬜ 無'}")
            self.lbl["距離"].config(text=f"距離: {s.get('dist', 0):.2f} m")
            self.lbl["手勢"].config(
                text=f"手勢: {s.get('fingers', 0)} {s.get('gesture', '-')}")
            m = s.get("measure")
            self.lbl["量測"].config(text=(
                f"量測: {m['length']*100:.1f}×{m['width']*100:.1f}cm "
                f"@{m['dist']:.2f}m" if m else "量測: -"))
            self.lbl["辨識"].config(
                text=f"辨識: {s.get('match_via', '-') if s.get('matched') else '-'}")
            self.lbl["訊息"].config(text=f"訊息: {(s.get('msg') or '')[:28]}")
        except requests.RequestException:
            pass
        self.root.after(250, self.update_status)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
