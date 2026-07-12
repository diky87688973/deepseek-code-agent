# -*- coding: utf-8 -*-
"""GitHub API 工具：管理 Issues / Releases / Topics / Pull Requests 等远程操作。

用法：
  python tools/github_api.py --action get_repo --repo owner/repo
  python tools/github_api.py --action login
  python tools/github_api.py --action list_prs --repo owner/repo
  python tools/github_api.py --action create_pr --repo owner/repo --title "..." --head fork:branch --base main
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# GitHub OAuth 设备登录 Client ID（GitHub CLI 官方公开 ID）
_CLIENT_ID = "178c6fc778ccc68e1d6a"


def _gh_err(message: str, err_type: str = "GitHubError", **data_extra: Any) -> Dict[str, Any]:
    data = dict(data_extra) if data_extra else None
    return {
        "ok": False,
        "data": data,
        "error": {"type": err_type, "message": str(message)},
    }


def _gh_ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    json_body: Any = None,
    form_body: Optional[Dict[str, str]] = None,
    timeout: float = 15,
) -> Any:
    """HTTP 请求并解析 JSON（标准库 urllib）。"""
    hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
    body_bytes: Optional[bytes] = None
    if json_body is not None:
        body_bytes = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
    elif form_body is not None:
        body_bytes = urllib.parse.urlencode(form_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as ex:
        detail = ""
        try:
            detail = ex.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(ex.reason)
        raise RuntimeError(f"HTTP {ex.code}: {detail or ex.reason}") from ex
    except urllib.error.URLError as ex:
        raise RuntimeError(str(ex.reason)) from ex

    if not raw.strip():
        return {}
    return json.loads(raw)


def _find_token() -> str:
    """查找 GitHub Token。优先级：GH_TOKEN 环境变量 > gh_token.txt"""
    env = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if env.strip():
        return env.strip()
    root = os.path.dirname(os.path.dirname(__file__))
    data_root = (
        os.environ.get("AGENT_DATA_ROOT", "").strip()
        or os.environ.get("DATA_ROOT", "").strip()
    )
    candidates = [os.path.join(root, "gh_token.txt")]
    if data_root:
        candidates.append(os.path.join(data_root, "gh_token.txt"))
    for p in candidates:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    return ""


def _save_token(token: str) -> None:
    root = os.path.dirname(os.path.dirname(__file__))
    data_root = (
        os.environ.get("AGENT_DATA_ROOT", "").strip()
        or os.environ.get("DATA_ROOT", "").strip()
    )
    candidates = [os.path.join(root, "gh_token.txt")]
    if data_root:
        candidates.append(os.path.join(data_root, "gh_token.txt"))
    for p in candidates:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(token)
        except Exception:
            pass


def _api(repo: str, path: str, method: str = "GET", data: Any = None) -> Dict[str, Any]:
    token = _find_token()
    if not token:
        return _gh_err(
            "GitHub Token 未找到。请执行 --action login",
            "LoginRequired",
            action="login_required",
        )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{repo}{path}"
    try:
        payload = _http_json(
            url,
            method=method,
            headers=headers,
            json_body=data if method in ("POST", "PUT", "PATCH") else None,
        )
        return _gh_ok(payload)
    except Exception as e:
        return _gh_err(str(e), e.__class__.__name__)


def cmd_login() -> Dict[str, Any]:
    """发起 GitHub 设备登录"""
    try:
        data = _http_json(
            "https://github.com/login/device/code",
            method="POST",
            headers={"Accept": "application/json"},
            form_body={
                "client_id": _CLIENT_ID,
                "scope": "repo,read:org,admin:public_key",
            },
        )
        user_code = data["user_code"]
        device_code = data["device_code"]
        verification_uri = data["verification_uri"]
        interval = int(data.get("interval", 5))

        print(f"\n🔑 验证码: {user_code}")
        print(f"🌐 打开: {verification_uri}")
        print(f"⏳ 输入验证码后等待自动完成...\n")

        try:
            import webbrowser

            webbrowser.open(verification_uri)
        except Exception:
            pass

        for _attempt in range(120):
            time.sleep(interval)
            result = _http_json(
                "https://github.com/login/oauth/access_token",
                method="POST",
                headers={"Accept": "application/json"},
                form_body={
                    "client_id": _CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            if "access_token" in result:
                _save_token(result["access_token"])
                return _gh_ok({"message": "登录成功！Token 已保存。"})
            error = result.get("error", "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error == "expired_token":
                return _gh_err("验证码已过期，请重新运行。", "ExpiredToken")
            return _gh_err(str(result), "OAuthError")
        return _gh_err("等待超时(10分钟)。", "TimeoutError")
    except Exception as e:
        return _gh_err(str(e), e.__class__.__name__)


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


def cmd_list_prs(repo: str, state: str = "open") -> Dict[str, Any]:
    """列出仓库的 Pull Requests。"""
    return _api(repo, f"/pulls?state={state}&per_page=20")


def cmd_get_pr(repo: str, pr_number: int) -> Dict[str, Any]:
    """获取单个 PR 详情。"""
    return _api(repo, f"/pulls/{pr_number}")


def cmd_create_pr(repo: str, title: str, head: str, base: str, body: str = "") -> Dict[str, Any]:
    """创建 Pull Request。"""
    return _api(repo, "/pulls", "POST", {
        "title": title,
        "head": head,
        "base": base,
        "body": body,
    })


def cmd_close_pr(repo: str, pr_number: int) -> Dict[str, Any]:
    """关闭 Pull Request（state = closed）。"""
    return _api(repo, f"/pulls/{pr_number}", "PATCH", {"state": "closed"})


def cmd_update_pr(repo: str, pr_number: int, title: Optional[str] = None,
                  body: Optional[str] = None, state: Optional[str] = None) -> Dict[str, Any]:
    """更新 PR 标题/描述/状态。传 None 的字段不修改。"""
    payload: Dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if state is not None:
        payload["state"] = state
    if not payload:
        return _gh_err("至少提供一个要更新的字段", "ValueError")
    return _api(repo, f"/pulls/{pr_number}", "PATCH", payload)


def agent_main(
    *,
    action: str = "",
    repo: str = "",
    title: Optional[str] = None,
    body: Optional[str] = None,
    state: Optional[str] = None,
    topics: Optional[str] = None,
    pr_number: int = 0,
    head: Optional[str] = None,
    base: Optional[str] = None,
) -> Dict[str, Any]:
    """GitHub API 工具入口（agent 调用）。"""
    action = (action or "").strip().lower()
    repo = (repo or "").strip().strip("/")
    if not action:
        return _gh_err("action 必填", "ValueError")
    if action != "login" and not repo:
        return _gh_err("repo 必填，格式: owner/repo", "ValueError")

    if action == "login":
        return cmd_login()
    if action == "get_repo":
        return cmd_get_repo(repo)
    if action == "list_issues":
        return cmd_list_issues(repo, state or "open")
    if action == "create_issue":
        return cmd_create_issue(repo, title or "", body or "")
    if action == "list_releases":
        return cmd_list_releases(repo)
    if action == "set_topics":
        topics_list = [t.strip() for t in (topics or "").split(",") if t.strip()]
        return cmd_set_topics(repo, topics_list)
    if action == "list_prs":
        return cmd_list_prs(repo, state or "open")
    if action == "get_pr":
        return cmd_get_pr(repo, pr_number)
    if action == "create_pr":
        return cmd_create_pr(repo, title or "", head or "", base or "", body or "")
    if action == "close_pr":
        return cmd_close_pr(repo, pr_number)
    if action == "update_pr":
        return cmd_update_pr(repo, pr_number, title, body, state)
    return _gh_err(f"未知 action: {action}", "ValueError")




