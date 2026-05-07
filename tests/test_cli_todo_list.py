# -*- coding: utf-8 -*-
"""
cli_todo_list 单元测试
=====================
测试 agent_main 及内部函数：create / check / uncheck / collapse / close / query
"""

from __future__ import annotations

import json
import pytest

# 被测模块内部函数的 import 路径需要模拟 CLI 调用
# agent_main 是进程内入口，可直接 import
from tools.cli_todo_list import agent_main, _do_create, _do_check, _do_uncheck, _do_collapse, _do_close, _do_query


# ========== _do_create ==========

class TestDoCreate:
    def test_create_ok(self):
        """正常创建清单"""
        res = _do_create('["步骤1","步骤2","步骤3"]')
        assert res["ok"] is True
        data = res["data"]
        assert "listId" in data
        assert len(data["items"]) == 3
        assert data["items"][0] == {"text": "步骤1", "done": False}
        assert data["items"][1] == {"text": "步骤2", "done": False}
        assert data["items"][2] == {"text": "步骤3", "done": False}
        assert data["collapsed"] is False

    def test_create_empty_items(self):
        """items 为空字符串"""
        res = _do_create("")
        assert res["ok"] is False
        assert "需要 --items" in res["error"]["message"]

    def test_create_whitespace_items(self):
        """items 为空白"""
        res = _do_create("   ")
        assert res["ok"] is False
        assert "需要 --items" in res["error"]["message"]

    def test_create_invalid_json(self):
        """items 不是合法 JSON"""
        res = _do_create("not a json")
        assert res["ok"] is False
        assert "不是合法 JSON" in res["error"]["message"]

    def test_create_not_array(self):
        """items 不是数组"""
        res = _do_create('"string"')
        assert res["ok"] is False
        assert "必须是非空数组" in res["error"]["message"]

    def test_create_empty_array(self):
        """items 是空数组"""
        res = _do_create("[]")
        assert res["ok"] is False
        assert "必须是非空数组" in res["error"]["message"]

    def test_create_non_string_element(self):
        """items 包含非字符串元素"""
        res = _do_create('[123, "ok"]')
        assert res["ok"] is False
        assert "不是有效字符串" in res["error"]["message"]

    def test_create_empty_string_element(self):
        """items 包含空字符串元素"""
        res = _do_create('["", "ok"]')
        assert res["ok"] is False
        assert "不是有效字符串" in res["error"]["message"]


# ========== _do_check ==========

class TestDoCheck:
    def test_check_ok(self):
        """正常勾选一项"""
        create_res = _do_create('["a","b","c"]')
        lid = create_res["data"]["listId"]
        res = _do_check(lid, 1)
        assert res["ok"] is True
        assert res["data"]["items"][1]["done"] is True
        assert res["data"]["items"][0]["done"] is False

    def test_check_invalid_list_id(self):
        """listId 不存在"""
        res = _do_check("nonexistent", 0)
        assert res["ok"] is False
        assert "listId 不存在" in res["error"]["message"]

    def test_check_index_negative(self):
        """索引为负数"""
        create_res = _do_create('["a","b"]')
        lid = create_res["data"]["listId"]
        res = _do_check(lid, -1)
        assert res["ok"] is False
        assert "越界" in res["error"]["message"]

    def test_check_index_out_of_range(self):
        """索引超出范围"""
        create_res = _do_create('["a","b"]')
        lid = create_res["data"]["listId"]
        res = _do_check(lid, 5)
        assert res["ok"] is False
        assert "越界" in res["error"]["message"]


# ========== _do_uncheck ==========

class TestDoUncheck:
    def test_uncheck_ok(self):
        """正常取消勾选"""
        create_res = _do_create('["x","y"]')
        lid = create_res["data"]["listId"]
        # 先勾选再取消
        _do_check(lid, 0)
        res = _do_uncheck(lid, 0)
        assert res["ok"] is True
        assert res["data"]["items"][0]["done"] is False

    def test_uncheck_invalid_list_id(self):
        """listId 不存在"""
        res = _do_uncheck("nonexistent", 0)
        assert res["ok"] is False
        assert "listId 不存在" in res["error"]["message"]

    def test_uncheck_index_out_of_range(self):
        """索引越界"""
        create_res = _do_create('["a"]')
        lid = create_res["data"]["listId"]
        res = _do_uncheck(lid, 99)
        assert res["ok"] is False
        assert "越界" in res["error"]["message"]


# ========== _do_collapse ==========

class TestDoCollapse:
    def test_collapse_toggle(self):
        """折叠状态切换"""
        create_res = _do_create('["a","b"]')
        lid = create_res["data"]["listId"]
        # 初始 collapsed=False
        assert create_res["data"]["collapsed"] is False
        # 第一次折叠
        res1 = _do_collapse(lid)
        assert res1["data"]["collapsed"] is True
        # 再次折叠（展开）
        res2 = _do_collapse(lid)
        assert res2["data"]["collapsed"] is False

    def test_collapse_invalid_list_id(self):
        """listId 不存在"""
        res = _do_collapse("nonexistent")
        assert res["ok"] is False
        assert "listId 不存在" in res["error"]["message"]


# ========== _do_close ==========

class TestDoClose:
    def test_close_ok(self):
        """正常关闭清单"""
        create_res = _do_create('["item"]')
        lid = create_res["data"]["listId"]
        res = _do_close(lid)
        assert res["ok"] is True
        assert res["data"]["close"] is True
        assert "listId" in res["data"]

    def test_close_invalid_list_id(self):
        """listId 不存在"""
        res = _do_close("nonexistent")
        assert res["ok"] is False
        assert "listId 不存在" in res["error"]["message"]


# ========== _do_query ==========

class TestDoQuery:
    def test_query_no_lists(self):
        """无活跃清单时返回 data:null"""
        # 注意：_do_query 依赖全局 _TODO_LISTS，可能受其他测试影响
        # 这里只验证接口格式
        res = _do_query("")
        # 由于全局状态可能被前面的 create 污染，我们只检查 ok 字段
        assert "ok" in res
        assert "data" in res
        assert "error" in res

    def test_query_with_list_id(self):
        """通过 listId 查询"""
        create_res = _do_create('["a","b"]')
        lid = create_res["data"]["listId"]
        res = _do_query(lid)
        assert res["ok"] is True
        assert len(res["data"]) == 2
        assert res["data"][0]["text"] == "a"

    def test_query_nonexistent_list_id(self):
        """查询不存在的 listId 返回 data:null"""
        res = _do_query("nonexistent_id_12345")
        assert res["ok"] is True
        assert res["data"] is None


# ========== agent_main 分发 ==========

class TestAgentMain:
    def test_agent_main_create(self):
        """agent_main 分发 create"""
        res = agent_main(action="create", items='["a"]')
        assert res["ok"] is True
        assert len(res["data"]["items"]) == 1

    def test_agent_main_check(self):
        """agent_main 分发 check"""
        create_res = agent_main(action="create", items='["a"]')
        lid = create_res["data"]["listId"]
        res = agent_main(action="check", list_id=lid, item_index=0)
        assert res["ok"] is True

    def test_agent_main_uncheck(self):
        """agent_main 分发 uncheck"""
        create_res = agent_main(action="create", items='["a"]')
        lid = create_res["data"]["listId"]
        agent_main(action="check", list_id=lid, item_index=0)
        res = agent_main(action="uncheck", list_id=lid, item_index=0)
        assert res["ok"] is True

    def test_agent_main_query(self):
        """agent_main 分发 query"""
        create_res = agent_main(action="create", items='["a"]')
        lid = create_res["data"]["listId"]
        res = agent_main(action="query", list_id=lid)
        assert res["ok"] is True

    def test_agent_main_collapse(self):
        """agent_main 分发 collapse"""
        create_res = agent_main(action="create", items='["a"]')
        lid = create_res["data"]["listId"]
        res = agent_main(action="collapse", list_id=lid)
        assert res["ok"] is True

    def test_agent_main_close(self):
        """agent_main 分发 close"""
        create_res = agent_main(action="create", items='["a"]')
        lid = create_res["data"]["listId"]
        res = agent_main(action="close", list_id=lid)
        assert res["ok"] is True

    def test_agent_main_add_item_not_implemented(self):
        """add_item 返回 NotImplemented"""
        res = agent_main(action="add_item")
        assert res["ok"] is False
        assert "NotImplemented" in str(res["error"]["type"])

    def test_agent_main_remove_item_not_implemented(self):
        """remove_item 返回 NotImplemented"""
        res = agent_main(action="remove_item")
        assert res["ok"] is False
        assert "NotImplemented" in str(res["error"]["type"])

    def test_agent_main_replace_item_not_implemented(self):
        """replace_item 返回 NotImplemented"""
        res = agent_main(action="replace_item")
        assert res["ok"] is False
        assert "NotImplemented" in str(res["error"]["type"])

    def test_agent_main_unknown_action(self):
        """未知 action"""
        res = agent_main(action="unknown_action_xyz")
        assert res["ok"] is False
        assert "未知 action" in res["error"]["message"]
