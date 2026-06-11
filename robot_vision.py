"""
robot_vision.py  —  透過 REST API 顯示視覺狀態（不直接開相機）

改版：相機由 vision_server.py 獨佔，本工具改成 REST 客戶端。
顯示伺服器影像（已含骨架/手勢疊加）並疊上狀態文字。

執行前：先啟動  python vision_server.py
操作：  [q]/Esc 離開
"""

import time
import numpy as np
import cv2
import requests

API = "http://127.0.0.1:8000"


def main():
    print(f"後端: {API}（請先啟動 vision_server.py）  [q]/Esc 離開")
    try:
        requests.post(f"{API}/config", json={"pose": True, "hands": True},
                      timeout=2)
    except requests.RequestException:
        print("⚠ 連不到後端，請先啟動 vision_server.py")

    last, t_status = {}, 0.0
    while True:
        try:
            r = requests.get(f"{API}/frame.jpg", timeout=2)
            frame = (cv2.imdecode(np.frombuffer(r.content, np.uint8),
                                  cv2.IMREAD_COLOR)
                     if r.status_code == 200 else None)
        except requests.RequestException:
            frame = None
        if frame is None:
            time.sleep(0.1)
            continue

        now = time.time()
        if now - t_status > 0.2:
            try:
                last = requests.get(f"{API}/status", timeout=2).json()
            except requests.RequestException:
                pass
            t_status = now

        s = last
        lines = [
            f"Person: {'YES' if s.get('person') else 'no'}",
            f"Dist  : {s.get('dist', 0):.2f} m",
            f"Finger: {s.get('fingers', 0)}  {s.get('gesture', '-')}",
            f"Fallen: {'!!! FALL' if s.get('fallen') else 'no'}",
        ]
        y = 24
        for ln in lines:
            cv2.putText(frame, ln, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 255, 0), 2)
            y += 26
        cv2.imshow("Robot Vision (REST)", frame)
        if (cv2.waitKey(1) & 0xFF) in (ord('q'), 27):
            break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
