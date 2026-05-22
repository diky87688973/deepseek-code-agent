# -*- coding: utf-8 -*-
"""GitHub 工具：供其他 Agent 调用，无需重新登录。

用法：
    from util.github_ops import gh_api_get, gh_api_post, gh_api_put
    
    # 获取仓库信息
    data = gh_api_get("/repos/diky87688973/deepseek-code-agent")
    
    # 创建 Issue
    gh_api_post("/repos/diky87688973/deepseek-code-agent/issues", {
        "title": "Bug Report",
        "body": "description"
    })
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import requests


_TOKEN_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gh_token.txt")
_ALT_TOKEN_PATH = "D:/AI_DATA_ROOT/gh_token.txt"


def _load_token() -> str:
    for p in (_TOKEN_PATH, _ALT_TOKEN_PATH):
        if os.path.isfile(p):
            with open(p) as f:
                return f.read().strip()
    raise FileNotFoundError(
        "GitHub Token 未找到。请先运行 util/github_auth.py 进行设备登录。"
    )


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_load_token()}",
        "Accept": "application/vnd.github.v3+json",
    }


_API_BASE = "https://api.github.com"


def gh_api_get(path: str) -> Any:
    r = requests.get(_API_BASE + path, headers=_headers())
    r.raise_for_status()
    return r.json()


def gh_api_post(path: str, data: Dict[str, Any]) -> Any:
    r = requests.post(_API_BASE + path, headers=_headers(), json=data)
    r.raise_for_status()
    return r.json()


def gh_api_put(path: str, data: Dict[str, Any]) -> Any:
    r = requests.put(_API_BASE + path, headers=_headers(), json=data)
    r.raise_for_status()
    return r.json()


def gh_api_delete(path: str) -> None:
    r = requests.delete(_API_BASE + path, headers=_headers())
    r.raise_for_status()


def get_repo() -> Dict[str, Any]:
    return gh_api_get("/repos/diky87688973/deepseek-code-agent")


def list_issues(state: str = "open") -> list:
    return gh_api_get(f"/repos/diky87688973/deepseek-code-agent/issues?state={state}")


def create_issue(title: str, body: str = "") -> Dict[str, Any]:
    return gh_api_post("/repos/diky87688973/deepseek-code-agent/issues", {
        "title": title,
        "body": body,
    })


def list_releases() -> list:
    return gh_api_get("/repos/diky87688973/deepseek-code-agent/releases")


def set_topics(topics: list[str]) -> list[str]:
    r = gh_api_put("/repos/diky87688973/deepseek-code-agent/topics", {"names": topics})
    return r.get("names", [])
