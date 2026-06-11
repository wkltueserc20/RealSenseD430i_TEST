"""
face_eval.py — 看 ArcFace 在 faces/ 上的「餘弦分離度」

讀 faces/ 裡每個人的所有樣本 → 用 ArcFace 算 embedding →
  • 同一人(不同樣本/角度)兩兩餘弦
  • 不同人 兩兩餘弦
印出統計,並建議一個分割門檻。餘弦越大越像(embedding 已 L2 正規化 → 點積=餘弦)。

用法:  python face_eval.py
需求:  已裝 onnxruntime + models/arcface.onnx;faces/ 至少 2 人、每人多張較有意義。
"""
import itertools
import numpy as np
import face_utils as fu


def stats(xs):
    a = np.asarray(xs, dtype=float)
    if a.size == 0:
        return (0, float("nan"), float("nan"), float("nan"))
    return (a.size, float(a.min()), float(a.mean()), float(a.max()))


def main():
    fu.DB.set_engine("arcface")              # 觸發重載 → 建好 embedding gallery
    if not fu.DB.arc_available():
        print("✗ ArcFace 不可用：需 `pip install onnxruntime` + models/arcface.onnx")
        print("  (LBPH 用的是『距離』而非餘弦，本腳本是給 ArcFace 看的)")
        return

    names, gal = fu.DB.names, fu.DB.gallery   # gal: id -> [emb,…](已正規化)
    people = [(names.get(fid, str(fid)), embs) for fid, embs in gal.items() if embs]
    if not people:
        print("faces/ 沒有資料。先到 UI 的『🙂 人臉』註冊幾個人(每人多張、換角度)。")
        return

    print(f"模型：{fu.ARC_MODEL}")
    print("已註冊：")
    for nm, embs in people:
        print(f"  • {nm}  —  {len(embs)} 張樣本")
    print()

    # ---- 同一人 ----
    print("【同一人 · 不同樣本】餘弦相似度(越高越好)")
    all_intra = []
    for nm, embs in people:
        pair = [float(np.dot(a, b)) for a, b in itertools.combinations(embs, 2)]
        all_intra += pair
        n, mn, me, mx = stats(pair)
        if n:
            print(f"  {nm:<10} n={n:>3}   min {mn:.3f}   mean {me:.3f}   max {mx:.3f}")
        else:
            print(f"  {nm:<10} 只有 1 張 → 無法兩兩比(請多採幾張)")
    n, mn, me, mx = stats(all_intra)
    if n:
        print(f"  ── 全部同人  n={n}  min {mn:.3f}  mean {me:.3f}  max {mx:.3f}")
    print()

    # ---- 不同人 ----
    print("【不同人】餘弦相似度(越低越好)")
    all_inter = []
    for (n1, e1), (n2, e2) in itertools.combinations(people, 2):
        pair = [float(np.dot(a, b)) for a in e1 for b in e2]
        all_inter += pair
        _, _, me2, mx2 = stats(pair)
        print(f"  {n1} ↔ {n2:<10} mean {me2:.3f}   max {mx2:.3f}")
    n, mn, me, mx = stats(all_inter)
    if n:
        print(f"  ── 全部不同人  n={n}  min {mn:.3f}  mean {me:.3f}  max {mx:.3f}")
    print()

    # ---- 結論 / 建議門檻 ----
    print("【結論】")
    if all_intra and all_inter:
        lo, hi = min(all_intra), max(all_inter)
        gap = lo - hi
        print(f"  同人最低 {lo:.3f}  vs  不同人最高 {hi:.3f}   間隙 {gap:+.3f}")
        if gap > 0:
            print(f"  ✅ 完全分得開！建議門檻 ≈ {(lo + hi) / 2:.2f}(落在兩者中間最安全)")
        else:
            cands = sorted({round(x, 3) for x in all_intra + all_inter})
            best_t, best_acc = fu.DEFAULT_SIM_ARC, 0.0
            tot = len(all_intra) + len(all_inter)
            for t in cands:
                acc = (sum(x >= t for x in all_intra) +
                       sum(x < t for x in all_inter)) / tot
                if acc > best_acc:
                    best_acc, best_t = acc, t
            print(f"  ⚠ 有重疊。建議多採幾張不同角度/光線的樣本。")
            print(f"     此資料的最佳分割門檻 ≈ {best_t:.2f}(正確率 {best_acc * 100:.0f}%)")
    elif not all_inter:
        print("  只有一個人 → 看不到『不同人』分離度。再註冊至少一人才有對照。")
    else:
        print("  每個人都只有 1 張 → 看不到『同人』分離度。請多採幾張。")
    print(f"\n  目前 UI 預設的 ArcFace 相似度門檻 = {fu.DEFAULT_SIM_ARC}")


if __name__ == "__main__":
    main()
