"""
scan_3d.py  —  透過 REST API 擷取點雲（不直接開相機）

改版：相機由 vision_server.py 獨佔，本工具改成 REST 客戶端，
可與 Rust UI / 其他工具同時使用同一台相機。

執行前：先啟動  python vision_server.py
操作：  [s] 擷取點雲（由伺服器存成 scans/*.ply）   [q]/Esc 離開
"""

import time
import numpy as np
import cv2
import requests

API = "http://127.0.0.1:8000"


def get_frame():
    try:
        r = requests.get(f"{API}/frame.jpg", timeout=2)
        if r.status_code != 200:
            return None
        return cv2.imdecode(np.frombuffer(r.content, np.uint8),
                            cv2.IMREAD_COLOR)
    except requests.RequestException:
        return None


def main():
    print(f"後端: {API}（請先啟動 vision_server.py）")
    print("操作: [s] 擷取點雲(伺服器存 .ply)  [q]/Esc 離開")
    try:
        requests.post(f"{API}/config", json={"depth": True}, timeout=2)
    except requests.RequestException:
        print("⚠ 連不到後端，請先啟動 vision_server.py")

    while True:
        frame = get_frame()
        if frame is None:
            time.sleep(0.1)
            continue
        cv2.putText(frame, "[s] save ply   [q] quit", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imshow("D435i 3D Scan (REST)", frame)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord('q'), 27):
            break
        if k == ord('s'):
            try:
                requests.post(f"{API}/scan", timeout=2)
                time.sleep(0.4)
                msg = requests.get(f"{API}/status", timeout=2).json().get("msg")
                print("擷取:", msg or "(已觸發)")
            except requests.RequestException as e:
                print("擷取失敗:", e)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
