# -*- coding: utf-8 -*-
"""GitHub API 工具：管理 Issues / Releases / Topics 等远程操作。

用法：
  python tools/github_api.py --action get_repo --repo owner/repo
  python tools/github_api.py --action login
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

# GitHub OAuth 设备登录 Client ID（GitHub CLI 官方公开 ID）
_CLIENT_ID = "178c6fc778ccc68e1d6a"


def _find_token() -> str:
    """查找 GitHub Token。优先级：GH_TOKEN 环境变量 > gh_token.txt"""
    env = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if env.strip():
        return env.strip()
    root = os.path.dirname(os.path.dirname(__file__))
    for p in [os.path.join(root, "gh_token.txt"), "D:/AI_DATA_ROOT/gh_token.txt"]:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    return ""


def _save_token(token: str) -> None:
    root = os.path.dirname(os.path.dirname(__file__))
    for p in [os.path.join(root, "gh_token.txt"), "D:/AI_DATA_ROOT/gh_token.txt"]:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(token)
        except Exception:
            pass


def _api(repo: str, path: str, method: str = "GET", data: Any = None) -> Dict[str, Any]:
    token = _find_token()
    if not token:
        return {"ok": False, "action": "login_required",
                "error": "GitHub Token 未找到。请执行 --action login"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{repo}{path}"
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
    """发起 GitHub 设备登录"""
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

        try:
            import webbrowser
            webbrowser.open(verification_uri)
        except Exception:
            pass

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
                _save_token(result["access_token"])
                return {"ok": True, "message": "登录成功！Token 已保存。"}
            error = result.get("error", "")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            elif error == "expired_token":
                return {"ok": False, "error": "验证码已过期，请重新运行。"}
            return {"ok": False, "error": str(result)}
        return {"ok": False, "error": "等待超时(10分钟)。"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_get_repo(repo: str) -> Dict[str, Any]:
    return _api(repo, "")


def cmd_list_issues(repo: str, state: str = "open") -> Dict[str, Any]:
    return _api(repo, f"/issues?state={state}&per_page=20")


def cmd_create_issue(repo: str, title: str, body: str = "") -> Dict[str, Any]:
    return _api(repo, "/issues", "POST", {"title": title, "body": body})


def cmd_list_releases(repo: str) -> Dict[str, Any]:
    return _api(repo, "/releases?per_page=10")


def cmd_set_topics(repo: str, topics: List[str]) -> Dict[str, Any]:
    return _api(repo, "/topics", "PUT", {"names": topics})


def agent_main(
    *,
    action: str = "",
    repo: str = "",
    title: Optional[str] = None,
    body: Optional[str] = None,
    state: Optional[str] = None,
    topics: Optional[str] = None,
) -> Dict[str, Any]:
    """GitHub API 工具入口（agent 调用）。"""
    action = (action or "").strip().lower()
    repo = (repo or "").strip().strip("/")
    if not action:
        return {"ok": False, "error": "action 必填"}
    if action != "login" and not repo:
        return {"ok": False, "error": "repo 必填，格式: owner/repo"}

    if action == "login":
        return cmd_login()
    elif action == "get_repo":
        return cmd_get_repo(repo)
    elif action == "list_issues":
        return cmd_list_issues(repo, state or "open")
    elif action == "create_issue":
        return cmd_create_issue(repo, title or "", body or "")
    elif action == "list_releases":
        return cmd_list_releases(repo)
    elif action == "set_topics":
        topics_list = [t.strip() for t in (topics or "").split(",") if t.strip()]
        return cmd_set_topics(repo, topics_list)
    else:
        return {"ok": False, "error": f"未知 action: {action}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub API 工具")
    parser.add_argument("--action", required=True,
                        choices=["login", "get_repo", "list_issues", "create_issue",
                                 "list_releases", "set_topics"],
                        help="操作类型")
    parser.add_argument("--repo", default="", help="仓库名 owner/repo（login 可省略）")
    parser.add_argument("--title", default="", help="Issue 标题")
    parser.add_argument("--body", default="", help="Issue 描述")
    parser.add_argument("--state", default="open", help="Issue 状态筛选，默认 open")
    parser.add_argument("--topics", default="", help="Topics 逗号分隔")
    args = parser.parse_args()

    result = agent_main(
        action=args.action,
        repo=args.repo,
        title=args.title,
        body=args.body,
        state=args.state,
        topics=args.topics,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
