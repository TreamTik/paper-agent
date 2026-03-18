"""
scripts/backfill_sha256.py
一次性脚本：为 data/cache/ 下所有 paper 类型且缺少 sha256 的状态文件补充哈希值。
运行：python scripts/backfill_sha256.py
"""

import sys
import json
import hashlib
from pathlib import Path

ROOT       = Path(__file__).parent.parent
STATES_DIR = ROOT / "data" / "cache"
PDFS_DIR   = ROOT / "data" / "pdfs"


def compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    state_files = sorted(STATES_DIR.glob("*.json"))
    updated = skipped_no_pdf = skipped_already = skipped_non_paper = 0

    for sf in state_files:
        try:
            state = json.loads(sf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [WARN] read error {sf.name}: {e}")
            continue

        # 只处理 paper 类型
        if state.get("type", "paper") != "paper":
            skipped_non_paper += 1
            continue

        # 已有非空 sha256 则跳过
        if state.get("sha256", ""):
            skipped_already += 1
            print(f"  [SKIP] already has sha256: {sf.name}")
            continue

        # 找对应 PDF
        pdf_filename = state.get("pdf_filename", "")
        pdf_path     = PDFS_DIR / pdf_filename
        if not pdf_path.exists():
            # 尝试用 stem 匹配（文件名可能有截断差异）
            stem = state.get("stem", "")
            candidates = list(PDFS_DIR.glob(f"{stem}*"))
            if candidates:
                pdf_path = candidates[0]
            else:
                skipped_no_pdf += 1
                print(f"  [MISS] no PDF found, skip: {sf.name}  (expected: {pdf_filename})")
                continue

        sha256 = compute_sha256(pdf_path)
        state["sha256"] = sha256
        sf.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        updated += 1
        print(f"  [OK] {sf.name}")
        print(f"       PDF   : {pdf_path.name}")
        print(f"       SHA256: {sha256[:16]}...")

    print()
    print(f"Done: updated={updated} | already_had_sha256={skipped_already} | "
          f"no_pdf={skipped_no_pdf} | non_paper={skipped_non_paper}")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
