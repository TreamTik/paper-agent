"""
scripts/migrate_data.py
将 data_my/ 迁移为新目录结构 data_new/，完成以下映射：
  data_my/states/       -> data_new/cache/
  data_my/refs_pending/ -> data_new/inbox/
  data_my/notes/*.md    -> data_new/notes/papers/*.md   (平铺 -> papers 子目录)
  data_my/pdfs/         -> data_new/pdfs/
  data_my/config/       -> data_new/config/

同时修正 cache/ 下所有 JSON 的 note_path：
  将路径中的 data  替换为 data_new，保持绝对路径有效。

用法（在项目根目录执行）：
    python scripts/migrate_data.py

完成后检查 data_new/，确认无误后可手动 rename data_new -> data。
"""

import json
import shutil
import sys
from pathlib import Path

ROOT     = Path(__file__).parent.parent
SRC      = ROOT / "data_my"
DST      = ROOT / "data_new"


# ── 目录映射：src 子目录 → dst 子目录 ────────────────────────────────────────
DIR_MAP = {
    "states":       "cache",
    "refs_pending": "inbox",
    "pdfs":         "pdfs",
    "config":       "config",
}
# notes 单独处理（需要平铺 → papers/ 子目录）


def copy_dir(src: Path, dst: Path):
    """递归复制 src 到 dst（覆盖已有文件）。"""
    if not src.exists():
        print(f"  [SKIP] 源目录不存在：{src.name}/")
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel  = item.relative_to(src)
        dest = dst / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
    print(f"  [OK]   {src.name}/ → {dst.relative_to(ROOT)}/  ({sum(1 for _ in src.rglob('*') if _.is_file())} 个文件)")


def migrate_notes(src_notes: Path, dst_notes: Path):
    """
    将 src_notes/ 下的 .md 文件复制到 dst_notes/papers/。
    子目录（ideas/, maps/ 等）原样复制到 dst_notes/。
    """
    if not src_notes.exists():
        print(f"  [SKIP] 源目录不存在：notes/")
        return

    n_flat = n_subdir = 0

    # 平铺在 notes/ 根目录的 .md → notes/papers/
    papers_dir = dst_notes / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    for f in src_notes.glob("*.md"):
        shutil.copy2(f, papers_dir / f.name)
        n_flat += 1

    # 子目录原样复制（ideas/, maps/, contradictions/, chats/, scout/ 等）
    for subdir in src_notes.iterdir():
        if subdir.is_dir():
            copy_dir(subdir, dst_notes / subdir.name)
            n_subdir += 1

    print(f"  [OK]   notes/ — {n_flat} 篇论文报告 → notes/papers/，{n_subdir} 个子目录原样复制")


def fix_note_paths(cache_dir: Path, old_seg: str, new_seg: str):
    """
    遍历 cache_dir/*.json，将 note_path 中 old_seg 替换为 new_seg。
    同时替换正斜杠和反斜杠两种形式。
    """
    fixed = skipped = 0
    for jf in cache_dir.glob("*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [WARN] 读取失败 {jf.name}: {e}")
            continue

        old_path = data.get("note_path", "")
        if not old_path:
            skipped += 1
            continue

        # 同时处理 Windows 反斜杠 和 正斜杠
        new_path = old_path.replace(old_seg, new_seg).replace(
            old_seg.replace("\\", "/"), new_seg.replace("\\", "/")
        )
        if new_path != old_path:
            data["note_path"] = new_path
            jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            fixed += 1
        else:
            skipped += 1

    print(f"  [OK]   note_path 修正：{fixed} 个已更新，{skipped} 个无需修改")


def main():
    if not SRC.exists():
        print(f"错误：源目录不存在 → {SRC}")
        sys.exit(1)

    if DST.exists():
        answer = input(f"目标目录 {DST.name}/ 已存在，覆盖？[y/N] ").strip().lower()
        if answer != "y":
            print("已取消。")
            sys.exit(0)
        shutil.rmtree(DST)
        print(f"  已删除旧 {DST.name}/")

    print(f"\n开始迁移 {SRC.name}/ → {DST.name}/\n")

    # 1. 复制各目录（含重命名）
    for src_name, dst_name in DIR_MAP.items():
        copy_dir(SRC / src_name, DST / dst_name)

    # 2. 处理 notes（平铺 → papers/ 子目录）
    migrate_notes(SRC / "notes", DST / "notes")

    # 3. 修正 JSON 中的 note_path（\data\ → \data_new\）
    print()
    old_seg = str(ROOT / "data") + "\\"          # e.g. C:\...\paper-agent\data\
    new_seg = str(ROOT / "data_new") + "\\"      # e.g. C:\...\paper-agent\data_new\
    print(f"修正 note_path：")
    print(f"  {old_seg}  →  {new_seg}")
    fix_note_paths(DST / "cache", old_seg, new_seg)

    print(f"\n迁移完成！目录：{DST}")
    print()
    print("后续操作：")
    print(f"  1. 检查 {DST.name}/ 内容无误")
    print(f"  2. 将当前 data/ 备份（可选）")
    print(f"  3. 执行：rename data_new data  （或直接覆盖 data/）")


if __name__ == "__main__":
    main()
