# -*- coding: utf-8 -*-
r"""source_hash 独立测试脚本，验证知识库元数据设计的正确性。

运行方式：
    cd d:\pythonProject\report-generation
    python app\test\test_source_hash.py

前提：
    pip install pandas openpyxl
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from memory.session_store import SessionStore


PASS = 0
FAIL = 0


def _msg(ok: bool, description: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {description}")
    else:
        FAIL += 1
        print(f"  ❌ {description}")


def _compute_sha256(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _make_excel(file_path: str, modality: str, module: str, finding: str) -> None:
    import pandas as pd
    pd.DataFrame([{
        "检查类型": modality,
        "一级目录": "测试",
        "模块名称": module,
        "影像学表现": finding,
        "影像学意见": finding,
    }]).to_excel(file_path, index=False)


def main():
    global PASS, FAIL
    tmpdir = tempfile.mkdtemp(prefix="test_source_hash_")
    db_path = os.path.join(tmpdir, "sessions.db")

    print("=" * 60)
    print("测试 source_hash 知识库元数据设计")
    print("=" * 60)

    # ── 测试 1：Catalog 字段完整性 ──────────────────────────
    print("\n📋 测试 1：新建 KB 时 source_hash 写入 SQLite catalog")
    store = SessionStore(db_path)
    source1 = os.path.join(tmpdir, "CT颅脑平扫.xlsx")
    _make_excel(source1, "CT", "颅脑平扫", "未见异常。")
    expected_hash = _compute_sha256(source1)

    store.create_knowledge_base(
        "kb_catalog_1", "CT颅脑平扫", status="uploaded",
        source_path=source1,
        source_name="CT颅脑平扫.xlsx",
        source_hash=expected_hash,
    )
    kb = store.get_knowledge_base("kb_catalog_1")
    _msg(kb is not None, "get_knowledge_base 返回非空")
    _msg(kb["kb_id"] == "kb_catalog_1", f"kb_id: {kb['kb_id']}")
    _msg(kb["kb_name"] == "CT颅脑平扫", f"kb_name: {kb['kb_name']}")
    _msg(kb["source_name"] == "CT颅脑平扫.xlsx", f"source_name: {kb['source_name']}")
    _msg(kb["source_hash"] == expected_hash, f"source_hash 正确: {kb['source_hash'][:16]}...")
    _msg(len(kb["source_hash"]) == 64, f"source_hash 长度=64: {len(kb['source_hash'])}")

    # ── 测试 2：同名同内容，生成不同 kb_id ──────────────────
    print("\n📋 测试 2：同名同内容 → 生成不同 kb_id")
    store2 = SessionStore(os.path.join(tmpdir, "sessions2.db"))
    source2 = os.path.join(tmpdir, "report.xlsx")
    _make_excel(source2, "CT", "测试", "无异常。")
    hash2 = _compute_sha256(source2)

    kb_a = store2.create_knowledge_base(
        "kb_001", "Report", status="uploaded",
        source_path=source2, source_name="report.xlsx", source_hash=hash2,
    )
    kb_b = store2.create_knowledge_base(
        "kb_002", "Report", status="uploaded",
        source_path=source2, source_name="report.xlsx", source_hash=hash2,
    )
    _msg(kb_a["kb_id"] != kb_b["kb_id"], f"不同 kb_id: {kb_a['kb_id']} vs {kb_b['kb_id']}")
    _msg(kb_a["source_name"] == kb_b["source_name"] == "report.xlsx", "source_name 相同")
    _msg(kb_a["source_hash"] == kb_b["source_hash"] == hash2, "source_hash 相同（同内容）")

    # ── 测试 3：同名不同内容，生成不同 kb_id，hash 不同 ─────
    print("\n📋 测试 3：同名不同内容 → 不同 kb_id，hash 不同")
    store3 = SessionStore(os.path.join(tmpdir, "sessions3.db"))
    source_a = os.path.join(tmpdir, "report_a.xlsx")
    source_b = os.path.join(tmpdir, "report_b.xlsx")
    _make_excel(source_a, "CT", "测试A", "内容A。")
    _make_excel(source_b, "MR", "测试B", "内容B。")
    hash_a = _compute_sha256(source_a)
    hash_b = _compute_sha256(source_b)

    # 确认两个文件内容不同导致 hash 不同
    _msg(hash_a != hash_b, f"不同内容 → 不同 hash: {hash_a[:16]}... vs {hash_b[:16]}...")

    kb_x = store3.create_knowledge_base(
        "kb_x", "Report", status="uploaded",
        source_path=source_a, source_name="report.xlsx", source_hash=hash_a,
    )
    kb_y = store3.create_knowledge_base(
        "kb_y", "Report", status="uploaded",
        source_path=source_b, source_name="report.xlsx", source_hash=hash_b,
    )
    _msg(kb_x["kb_id"] != kb_y["kb_id"], "不同 kb_id")
    _msg(kb_x["source_name"] == kb_y["source_name"] == "report.xlsx", "source_name 相同")
    _msg(kb_x["source_hash"] != kb_y["source_hash"], "source_hash 不同（不同内容）")

    # ── 测试 4：rebuild 保持 kb_id，更新 hash ─────────────────
    print("\n📋 测试 4：rebuild 保持原 kb_id，更新 source_hash")
    store4 = SessionStore(os.path.join(tmpdir, "sessions4.db"))
    source4 = os.path.join(tmpdir, "rebuild.xlsx")
    _make_excel(source4, "CT", "原始", "原始内容。")
    old_hash = _compute_sha256(source4)

    store4.create_knowledge_base(
        "kb_rebuild", "Report", status="uploaded",
        source_path=source4, source_name="rebuild.xlsx", source_hash=old_hash,
    )
    kb_before = store4.get_knowledge_base("kb_rebuild")
    _msg(kb_before["source_hash"] == old_hash, "初始 hash 正确")

    # 修改文件内容
    os.remove(source4)
    _make_excel(source4, "MR", "更新", "更新内容。")
    new_hash = _compute_sha256(source4)
    _msg(new_hash != old_hash, f"修改后 hash 变化: {old_hash[:16]}... → {new_hash[:16]}...")

    # 模拟 rebuild：更新 hash
    store4.update_knowledge_base("kb_rebuild", source_hash=new_hash)
    kb_after = store4.get_knowledge_base("kb_rebuild")
    _msg(kb_after["kb_id"] == "kb_rebuild", "kb_id 保持不变")
    _msg(kb_after["source_hash"] == new_hash, "source_hash 已更新")
    _msg(kb_after["source_hash"] != old_hash, "source_hash 与旧值不同")

    # ── 测试 5：new KB 同名但生成新 kb_id ────────────────────
    print("\n📋 测试 5：新建知识库，同名文件也生成新 kb_id")
    store5 = SessionStore(os.path.join(tmpdir, "sessions5.db"))
    source5 = os.path.join(tmpdir, "same_name.xlsx")
    _make_excel(source5, "CT", "测试", "内容。")
    hash5 = _compute_sha256(source5)

    kb1 = store5.create_knowledge_base(
        "kb_new_001", "Report", status="uploaded",
        source_path=source5, source_name="same_name.xlsx", source_hash=hash5,
    )
    kb2 = store5.create_knowledge_base(
        "kb_new_002", "Report", status="uploaded",
        source_path=source5, source_name="same_name.xlsx", source_hash=hash5,
    )
    _msg(kb1["kb_id"] != kb2["kb_id"], f"新建 KB 生成不同 kb_id: {kb1['kb_id']} vs {kb2['kb_id']}")
    _msg(kb1["source_name"] == kb2["source_name"], "source_name 相同")
    _msg(kb1["source_hash"] == kb2["source_hash"], "source_hash 相同（同内容）")

    # ── 测试 6：list_knowledge_bases 返回 source_hash ────────
    print("\n📋 测试 6：list_knowledge_bases 返回 source_name 和 source_hash")
    all_kbs = store.list_knowledge_bases()
    _msg(len(all_kbs) >= 1, f"至少 1 个知识库: {len(all_kbs)} 个")
    first = all_kbs[0]
    _msg("source_name" in first, "返回字段包含 source_name")
    _msg("source_hash" in first, "返回字段包含 source_hash")
    _msg(isinstance(first.get("source_hash"), str), "source_hash 是字符串")

    # ── 测试 7：hash 相同跳过重建逻辑 ────────────────────────
    print("\n📋 测试 7：hash 相同 → 内容未变化（可跳过重建）")
    store7 = SessionStore(os.path.join(tmpdir, "sessions7.db"))
    source7 = os.path.join(tmpdir, "skip.xlsx")
    _make_excel(source7, "CT", "跳过", "跳过内容。")
    hash7 = _compute_sha256(source7)

    store7.create_knowledge_base(
        "kb_skip", "Skip", status="active",
        source_path=source7, source_name="skip.xlsx", source_hash=hash7,
    )
    kb7 = store7.get_knowledge_base("kb_skip")
    recomputed = _compute_sha256(source7)
    _msg(recomputed == kb7["source_hash"], "hash 未变化，可跳过重建")

    # ── 汇总 ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"结果: {PASS} 通过 / {FAIL} 失败 / {total} 总计")
    if FAIL == 0:
        print("🎉 全部测试通过！")
    else:
        print(f"⚠️ 有 {FAIL} 个测试失败，请检查。")
    print("=" * 60)

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())