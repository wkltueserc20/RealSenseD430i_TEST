"""
face_eval.py — 看 ArcFace 在 faces/ 上的「餘弦分離度」(命令列版)

讀 faces/ 每個人的樣本 → ArcFace embedding → 印出
  • 同一人(不同樣本/角度)兩兩餘弦   (越高越好)
  • 不同人 兩兩餘弦                   (越低越好)
並建議一個分割門檻。網頁版同一份邏輯在「🙂 人臉」面板的「📊 分析分離度」。

用法:  python face_eval.py
"""
import face_utils as fu


def main():
    r = fu.DB.eval_separation()
    if not r["available"]:
        print("✗ ArcFace 不可用：需 `pip install onnxruntime` + models/arcface.onnx")
        return
    if not r["people"]:
        print("faces/ 沒有資料。先到 UI 的『🙂 人臉』註冊幾個人(每人多張、換角度)。")
        return

    print("已註冊：")
    for p in r["people"]:
        print(f"  • {p['name']}  —  {p['samples']} 張樣本")

    print("\n【同一人 · 不同樣本】餘弦(越高越好)")
    for it in r["intra"]:
        if it.get("n"):
            print(f"  {it['name']:<10} n={it['n']:>3}   min {it['min']:.3f}   "
                  f"mean {it['mean']:.3f}   max {it['max']:.3f}")
        else:
            print(f"  {it['name']:<10} 只有 1 張 → 無法兩兩比")
    if r["intra_all"]:
        a = r["intra_all"]
        print(f"  ── 全部同人  n={a['n']}  min {a['min']:.3f}  mean {a['mean']:.3f}  max {a['max']:.3f}")

    print("\n【不同人】餘弦(越低越好)")
    for it in r["inter"]:
        print(f"  {it['a']} ↔ {it['b']:<10} mean {it['mean']:.3f}   max {it['max']:.3f}")
    if r["inter_all"]:
        a = r["inter_all"]
        print(f"  ── 全部不同人  n={a['n']}  min {a['min']:.3f}  mean {a['mean']:.3f}  max {a['max']:.3f}")

    print("\n【結論】")
    s = r["separation"]
    if not s:
        print("  人數/樣本不足(需≥2人、且至少一人≥2張)才看得到分離度。")
    elif s["separable"]:
        print(f"  ✅ 完全分得開！同人最低 {s['intra_min']} > 不同人最高 {s['inter_max']}")
        print(f"     建議門檻 ≈ {s['suggested']}")
    else:
        print(f"  ⚠ 有重疊：同人最低 {s['intra_min']} ≤ 不同人最高 {s['inter_max']}")
        print(f"     建議多採幾張不同角度/光線;此資料最佳門檻 ≈ {s['suggested']}"
              f"(正確率 {s['acc'] * 100:.0f}%)")
    print(f"\n  目前 UI 預設的 ArcFace 相似度門檻 = {r['default_thr']}")


if __name__ == "__main__":
    main()
