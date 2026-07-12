# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path


class TestDeleteFileRecycleRoot(unittest.TestCase):
    def test_bootstrap_aligns_delete_file_and_file_ops(self):
        # 重新加载会受已 import 影响；以当前进程宿主配置为准
        from agent_v4.bootstrap import DATA_ROOT, RECYCLE_ROOT
        import delete_file as df
        import file_ops as fo

        self.assertEqual(RECYCLE_ROOT, Path(os.environ["AGENT_RECYCLE_ROOT"]).resolve())
        self.assertEqual(df._trash_root.resolve(), RECYCLE_ROOT)
        self.assertEqual(fo._recycle_bin_root().resolve(), RECYCLE_ROOT)
        self.assertTrue(str(RECYCLE_ROOT).endswith("AI_安全删除回收站") or "AI_安全删除回收站" in str(RECYCLE_ROOT))

    def test_delete_moves_into_recycle_root(self):
        from agent_v4.bootstrap import RECYCLE_ROOT
        import delete_file as df

        td = Path(tempfile.mkdtemp())
        src = td / "del_me.txt"
        src.write_text("x", encoding="utf-8")
        r = df.agent_main(path=str(src), dry_run=False, run_type="execute")
        self.assertTrue(r.get("ok"), r)
        moved = Path(r["data"]["moved_to"])
        self.assertFalse(src.exists())
        self.assertTrue(moved.is_file())
        self.assertEqual(Path(r["data"]["trash_root"]).resolve(), RECYCLE_ROOT)
        try:
            moved.relative_to(RECYCLE_ROOT)
        except ValueError:
            self.fail(f"moved_to not under RECYCLE_ROOT: {moved} vs {RECYCLE_ROOT}")


if __name__ == "__main__":
    unittest.main()
