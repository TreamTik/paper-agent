#!/usr/bin/env python3
"""
scripts/rename_refs.py
将 data/inbox/ 中形如 {arxiv_id}.pdf 的文件批量改名为论文真实标题。

用法（在项目根目录执行）：
    python scripts/rename_refs.py

依赖：纯标准库，无需额外安装。
"""
import os
import re
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

REFS_DIR  = Path(__file__).parent.parent / "data" / "inbox"
ARXIV_PAT = re.compile(r"^(\d{4}\.\d{4,5})\.pdf$")
NS        = {"atom": "http://www.w3.org/2005/Atom"}


def sanitize(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name.strip()[:120]


def fetch_title(arxiv_id: str, retries: int = 4) -> str | None:
    url = "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id)
    
    # 1. 添加 User-Agent 伪装，避免被 arXiv API 随机拦截
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    req = urllib.request.Request(url, headers=headers)
    
    wait = 3.0  # 初始等待秒数，失败后指数翻倍
    
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                xml_data = resp.read()
            
            root = ET.fromstring(xml_data)
            entry = root.find("atom:entry", NS)
            
            # API 正常返回了 XML，但没找到这个 ID 对应的论文
            if entry is None:
                return None
                
            title_el = entry.find("atom:title", NS)
            return title_el.text.strip().replace("\n", " ") if title_el is not None else None
            
        except urllib.error.HTTPError as e:
            # 2. 捕获所有 HTTP 错误（包含 429, 503, 403 等）并重试
            print(f"\n  ⚠ HTTP {e.code} ({e.reason})，等待 {wait:.0f}s 后重试...", end=" ", flush=True)
            time.sleep(wait)
            wait *= 2
        except Exception as e:
            # 3. 捕获所有基础网络异常（超时、连接被拒等）并重试
            print(f"\n  ⚠ 网络异常 ({e})，等待 {wait:.0f}s 后重试...", end=" ", flush=True)
            time.sleep(wait)
            wait *= 2
            
    print(f"\n  ❌ 重试 {retries} 次后最终失败")
    return None


def main():
    if not REFS_DIR.exists():
        print(f"目录不存在：{REFS_DIR}")
        return

    pdfs = [f for f in os.listdir(REFS_DIR) if ARXIV_PAT.match(f)]
    if not pdfs:
        print("data/inbox/ 中没有形如 {arxiv_id}.pdf 的文件。")
        return

    print(f"发现 {len(pdfs)} 个文件，开始查询标题…\n")
    for fname in sorted(pdfs):
        arxiv_id = ARXIV_PAT.match(fname).group(1)
        print(f"查询 {arxiv_id} …", end=" ", flush=True)
        title = fetch_title(arxiv_id)
        if title:
            new_name = sanitize(title) + ".pdf"
            src = REFS_DIR / fname
            dst = REFS_DIR / new_name
            if dst.exists():
                print(f"⚠  目标已存在，跳过 → {new_name}")
            else:
                src.rename(dst)
                print(f"✅ → {new_name}")
        else:
            print("❌ 无法获取标题，保持原名")
        time.sleep(3.0)   # arxiv API 建议间隔 ≥ 3s


if __name__ == "__main__":
    main()
