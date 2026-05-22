# -*- coding: utf-8 -*-
"""GitHub API 工具：管理 Issues / Releases / Topics 等远程操作。

与 git_workspace（公司 GitLab 操作）互补：
  - git_workspace: 公司 GitLab 仓库操作
  - github_api:   GitHub 远程 Issues/Releases/Topics

usage: github_api.py [-h] --action {login,get_repo,list_issues,create_issue,list_releases,set_topics}
                     [--title TITLE] [--body BODY] [--state STATE] [--topics TOPICS]

示例：
  python tools/github_api.py --action login                    # 设备登录（首次使用）
  python tools/github_api.py --action get_repo                 # 获取仓库信息
  python tools/github_api.py --action list_issues --state open  # 列出 Issue
  python tools/github_api.py --action create_issue --title "Bug" --body "description"
  python tools/github_api.py --action list_releases             # 列出 Release
  python tools/github_api.py --action set_topics --topics "ai-agent,code-agent"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

_TOKEN_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gh_token.txt")
_ALT_TOKEN = "D:/AI_DATA_ROOT/gh_token.txt"
_REPO = "/repos/diky87688973/deepseek-code-agent"
_CLIENT_ID = "178c6fc778ccc68e1d6a"  # GitHub CLI client ID


def _load_token() -> str:
    for p in (_TOKEN_PATH, _ALT_TOKEN):
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    return ""


def _save_token(token: str) -> None:
    for p in (_TOKEN_PATH, _ALT_TOKEN):
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(token)
        except Exception:
            pass


def _api(path: str, method: str = "GET", data: Any = None) -> Dict[str, Any]:
    token = _load_token()
    if not token:
        return {"ok": False, "action": "login_required",
                "error": "GitHub Token 未找到。请执行: python tools/github_api.py --action login"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com{path}"
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=15)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=data, timeout=15)
        elif method == "PUT":
            r = requests.put(url, headers=headers, json=data, timeout=15)
        else:
            return {"ok": False, "error": f"不支持的 HTTP 方法: {method}"}
        r.raise_for_status()
        return {"ok": True, "data": r.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_login() -> Dict[str, Any]:
    """发起 GitHub 设备登录流程"""
    try:
        r = requests.post(
            "https://github.com/login/device/code",
            headers={"Accept": "application/json"},
            data={"client_id": _CLIENT_ID, "scope": "repo,read:org,admin:public_key"},
            timeout=15
        )
        data = r.json()
        user_code = data["user_code"]
        device_code = data["device_code"]
        verification_uri = data["verification_uri"]
        interval = data.get("interval", 5)

        print(f"\n🔑 验证码: {user_code}")
        print(f"🌐 打开: {verification_uri}")
        print(f"⏳ 输入验证码后等待自动完成...\n")
        
        # 尝试打开浏览器
        try:
            import webbrowser
            webbrowser.open(verification_uri)
        except Exception:
            pass

        # 轮询等待授权
        for attempt in range(120):
            time.sleep(interval)
            r2 = requests.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": _CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
                },
                timeout=15
            )
            result = r2.json()
            if "access_token" in result:
                token = result["access_token"]
                _save_token(token)
                return {"ok": True, "message": "登录成功！Token 已保存。"}
            error = result.get("error", "")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            elif error == "expired_token":
                return {"ok": False, "error": "验证码已过期，请重新运行。"}
            else:
                return {"ok": False, "error": str(result)}

        return {"ok": False, "error": "等待超时(10分钟)，请重新运行。"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_get_repo() -> Dict[str, Any]:
    return _api(_REPO)


def cmd_list_issues(state: str = "open") -> Dict[str, Any]:
    return _api(f"{_REPO}/issues?state={state}&per_page=20")


def cmd_create_issue(title: str, body: str = "") -> Dict[str, Any]:
    return _api(f"{_REPO}/issues", "POST", {"title": title, "body": body})


def cmd_list_releases() -> Dict[str, Any]:
    return _api(f"{_REPO}/releases?per_page=10")


def cmd_set_topics(topics: List[str]) -> Dict[str, Any]:
    return _api(f"{_REPO}/topics", "PUT", {"names": topics})


def agent_main(
    *,
    action: str = "",
    title: Optional[str] = None,
    body: Optional[str] = None,
    state: Optional[str] = None,
    topics: Optional[str] = None,
) -> Dict[str, Any]:
    """GitHub API 工具入口（agent 调用）。"""
    action = (action or "").strip().lower()
    if not action:
        return {"ok": False, "error": "action 必填"}

    if action == "login":
        return cmd_login()
    elif action == "get_repo":
        return cmd_get_repo()
    elif action == "list_issues":
        return cmd_list_issues(state or "open")
    elif action == "create_issue":
        return cmd_create_issue(title or "", body or "")
    elif action == "list_releases":
        return cmd_list_releases()
    elif action == "set_topics":
        topics_list = [t.strip() for t in (topics or "").split(",") if t.strip()]
        return cmd_set_topics(topics_list)
    else:
        return {"ok": False, "error": f"未知 action: {action}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub API 工具")
    parser.add_argument("--action", required=True,
                        choices=["login", "get_repo", "list_issues", "create_issue",
                                 "list_releases", "set_topics"],
                        help="操作类型")
    parser.add_argument("--title", default="", help="Issue 标题（create_issue）")
    parser.add_argument("--body", default="", help="Issue 描述（create_issue）")
    parser.add_argument("--state", default="open", help="Issue 状态筛选（list_issues）")
    parser.add_argument("--topics", default="", help="Topics 列表逗号分隔（set_topics）")
    args = parser.parse_args()

    if args.action == "login":
        result = cmd_login()
    elif args.action == "get_repo":
        result = cmd_get_repo()
    elif args.action == "list_issues":
        result = cmd_list_issues(args.state)
    elif args.action == "create_issue":
        result = cmd_create_issue(args.title, args.body)
    elif args.action == "list_releases":
        result = cmd_list_releases()
    elif args.action == "set_topics":
        topics = [t.strip() for t in args.topics.split(",") if t.strip()]
        result = cmd_set_topics(topics)
    else:
        result = {"ok": False, "error": f"未知 action: {args.action}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
