# -*- coding: utf-8 -*-
"""即梦 Dreamina 文生图/文生视频工具 — 封装 dreamina CLI。

通过 agent_main 进程内调用，输出结构化结果供前端渲染卡片。
支持：登录/登录检查/登出/余额查询/文生图/文生视频/图生视频/查询结果/任务列表
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import agent_common as ac

# ── dreamina CLI 路径 ──
_DREAMINA_CLI: Optional[Path] = None


def _resolve_dreamina_cli() -> Path:
    global _DREAMINA_CLI
    if _DREAMINA_CLI is not None:
        return _DREAMINA_CLI

    # 优先从统一配置读取路径
    _cfg_path = ""
    try:
        from util.config_loader import load_config
        _cfg = load_config(verbose=False)
        _cfg_path = str(_cfg.get("AGENT_DREAMINA_CLI_PATH", "") or "")
    except Exception:
        pass

    # 查找候选路径（从配置优先，其次是环境变量和 PATH）
    candidates = [
        Path(_cfg_path) if _cfg_path else None,
        Path(os.environ.get("DREAMINA_CLI_PATH", "")),
    ]
    # 检查 PATH
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p.strip():
            candidates.append(Path(p.strip()) / "dreamina.exe")
            candidates.append(Path(p.strip()) / "dreamina")
    # 检查 HOME 目录
    home = Path.home()
    for sub in [".dreamina_cli", ".local/bin", "bin"]:
        candidates.append(home / sub / "dreamina.exe")
        candidates.append(home / sub / "dreamina")

    for p in candidates:
        if p and p.is_file():
            _DREAMINA_CLI = p
            return p

    raise FileNotFoundError(
        "未找到 dreamina CLI。请先安装：\n"
        "  1. 打开终端执行:\n"
        "     curl -s https://jimeng.jianying.com/cli | bash\n"
        "  2. 或手动下载到 D:/AI_DATA_ROOT/dreamina/dreamina.exe"
    )


def _run_dreamina(args: List[str], timeout: int = 120) -> dict:
    """运行 dreamina CLI，返回结构化结果。"""
    cli = _resolve_dreamina_cli()
    try:
        result = subprocess.run(
            [str(cli)] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return ac.err(TimeoutError(f"dreamina 命令超时（>{timeout}s）"))
    except FileNotFoundError as e:
        return ac.err(e)
    except Exception as e:
        return ac.err(e)

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    exit_code = result.returncode

    # 解析 JSON 输出
    parsed_json = None
    if stdout:
        for line in stdout.split("\n"):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed_json = json.loads(line)
                except json.JSONDecodeError:
                    pass

    return {
        "ok": exit_code == 0,
        "data": {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "json": parsed_json,
        },
        "error": None if exit_code == 0 else {
            "type": "DreaminaCLIError",
            "message": stderr or stdout or f"exit_code={exit_code}",
        },
    }


def _action_login() -> dict:
    """发起 OAuth Device Flow 登录。"""
    r = _run_dreamina(["login", "--headless"])
    if not r["ok"]:
        return r
    stdout = r["data"]["stdout"]
    verification_uri = ""
    user_code = ""
    device_code = ""
    expires_in = 300
    for line in stdout.split("\n"):
        line = line.strip()
        # verification_uri 行格式为 "key: value"，值中可能包含 =，必须用 : 分割
        if line.startswith("verification_uri:"):
            verification_uri = line.split(":", 1)[1].strip()
        elif line.startswith("verification_uri="):
            verification_uri = line.split("=", 1)[1].strip()
        elif "user_code" in line and "=" in line:
            user_code = line.split("=", 1)[1].strip()
        elif "user_code" in line and ":" in line:
            user_code = line.split(":", 1)[1].strip()
        elif "device_code" in line and "=" in line:
            device_code = line.split("=", 1)[1].strip()
        elif "device_code" in line and ":" in line:
            device_code = line.split(":", 1)[1].strip()
        elif "expires_in" in line and "=" in line:
            try:
                expires_in = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    if not verification_uri or not user_code or not device_code:
        return {
            "ok": False,
            "data": {"raw_output": stdout},
            "error": {
                "type": "LoginParseError",
                "message": f"无法解析登录信息，原始输出：\n{stdout[:2000]}",
            },
        }
    # 构建授权链接消息
    from urllib.parse import unquote
    auth_url = unquote(verification_uri)

    msg_parts = []
    msg_parts.append(
        f"请点击下方链接打开即梦授权页，然后用手机 **抖音App** 扫码完成授权：\n\n"
        f"👉 **[点此打开即梦授权页]({auth_url})**\n\n"
        f"🔑 **用户代码**：`{user_code}`"
    )
    msg_parts.append('完成授权后，告诉我"已登录"，我来检查登录状态。')

    return ac.ok({
        "action": "login",
        "status": "awaiting_login",
        "verification_uri": verification_uri,
        "user_code": user_code,
        "device_code": device_code,
        "expires_in": expires_in,
        "message": "\n\n".join(msg_parts),
    })


def _action_check_login(device_code: str, poll: int = 5) -> dict:
    """检查 OAuth 登录状态。"""
    if not device_code:
        return ac.err(ValueError("缺少 device_code 参数"))
    r = _run_dreamina(["login", "checklogin", "--device_code", device_code, "--poll", str(poll)])
    stdout = r["data"]["stdout"]
    stderr = r["data"]["stderr"]
    is_success = "[DREAMINA:LOGIN_SUCCESS]" in stdout or "[DREAMINA:LOGIN_REUSED]" in stdout
    is_pending = "[DREAMINA:LOGIN_PENDING]" in stdout or "pending" in stdout.lower()
    if is_success:
        return ac.ok({
            "action": "check_login",
            "status": "logged_in",
            "message": "✅ Dreamina 已登录成功，可以开始使用生成功能！",
        })
    elif is_pending:
        return ac.ok({
            "action": "check_login",
            "status": "pending",
            "message": "⏳ 等待用户扫码授权中，请完成扫码后重试...",
            "device_code": device_code,
        })
    else:
        msg = stderr[:500] if stderr else f"登录状态未知：{stdout[:500]}"
        return {"ok": False, "data": {"action": "check_login", "status": "unknown", "stdout": stdout[:1000]}, "error": {"type": "LoginCheckError", "message": msg}}


def _action_logout() -> dict:
    """登出。"""
    _run_dreamina(["logout"])
    return ac.ok({"action": "logout", "status": "logged_out", "message": "已登出 Dreamina。"})


def _action_user_credit() -> dict:
    """查询余额。"""
    r = _run_dreamina(["user_credit"])
    stdout = r["data"]["stdout"]
    return {
        "ok": r["ok"],
        "data": {"action": "user_credit", "raw": stdout[:2000], "message": f"```\n{stdout[:2000]}\n```" if stdout else "无法获取余额信息。"},
        "error": r["error"],
    }


def _action_generate(gen_type: str = "text2image", prompt: str = "", ratio: str = "16:9", resolution: str = "2k", model_version: str = "", count: int = 1) -> dict:
    """文生图/文生视频。"""
    if not prompt:
        return ac.err(ValueError("缺少 prompt 参数"))
    args = [gen_type, "--prompt", prompt, "--ratio", ratio, "--resolution_type", resolution]
    if model_version:
        args.extend(["--model_version", model_version])
    r = _run_dreamina(args, timeout=300)
    return _parse_submit_response(r, gen_type)


def _action_query_result(submit_id: str) -> dict:
    """查询异步生成结果。"""
    if not submit_id:
        return ac.err(ValueError("缺少 submit_id 参数"))
    r = _run_dreamina(["query_result", "--submit_id", submit_id], timeout=60)
    stdout = r["data"]["stdout"]
    gen_status = ""
    media_urls = []
    fail_reason = ""
    for line in stdout.split("\n"):
        ls = line.strip()
        if "gen_status=" in ls:
            gen_status = ls.split("=", 1)[1].strip()
        if "media_url=" in ls or "url=" in ls:
            url = ls.split("=", 1)[1].strip()
            if url:
                media_urls.append(url)
        if "fail_reason=" in ls:
            fail_reason = ls.split("=", 1)[1].strip()
        if ls.startswith("{"):
            try:
                j = json.loads(ls)
                if isinstance(j, dict):
                    gen_status = j.get("gen_status") or j.get("genStatus") or gen_status
                    fail_reason = j.get("fail_reason") or j.get("failReason") or fail_reason
                    urls = j.get("media_urls") or j.get("mediaUrls") or j.get("urls") or []
                    if isinstance(urls, list):
                        media_urls.extend(urls)
                    elif isinstance(urls, str):
                        media_urls.append(urls)
            except json.JSONDecodeError:
                pass
    if not r["ok"]:
        return r
    data = {"action": "query_result", "submit_id": submit_id, "gen_status": gen_status or "unknown", "media_urls": media_urls, "fail_reason": fail_reason, "stdout": stdout[:2000]}
    if gen_status == "success" and media_urls:
        md_parts = [f"✅ **生成成功！**\n\n任务ID：`{submit_id}`\n"]
        for url in media_urls:
            if any(url.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]):
                md_parts.append(f"![生成结果]({url})")
            elif any(url.lower().endswith(ext) for ext in [".mp4", ".mov", ".webm", ".avi"]):
                md_parts.append(f"🎬 [查看视频]({url})")
            else:
                md_parts.append(f"📎 [查看媒体]({url})")
        data["message"] = "\n\n".join(md_parts)
    elif gen_status == "fail":
        data["message"] = f"❌ **生成失败**：{fail_reason or '未知原因'}"
    elif gen_status == "querying":
        data["message"] = f"⏳ **生成中**，请稍后重试查询。任务ID：`{submit_id}`"
    else:
        data["message"] = f"当前状态：{gen_status}\n\n```\n{stdout[:2000]}\n```"
    return ac.ok(data)


def _action_list_task(gen_status: str = "", limit: int = 20) -> dict:
    """查看历史任务列表。"""
    args = ["list_task"]
    if gen_status:
        args.extend(["--gen_status", gen_status])
    if limit:
        args.extend(["--limit", str(limit)])
    r = _run_dreamina(args)
    stdout = r["data"]["stdout"]
    return {"ok": r["ok"], "data": {"action": "list_task", "raw": stdout[:5000], "message": f"```\n{stdout[:5000]}\n```" if stdout else "暂无任务记录。"}, "error": r["error"]}


def _action_image2video(images: str, prompt: str = "", model_version: str = "") -> dict:
    """图生视频。"""
    if not images:
        return ac.err(ValueError("缺少 images 参数"))
    img = Path(images)
    if not img.is_file():
        return ac.err(FileNotFoundError(f"图片文件不存在：{images}"))
    args = ["image2video", "--images", images]
    if prompt:
        args.extend(["--prompt", prompt])
    if model_version:
        args.extend(["--model_version", model_version])
    r = _run_dreamina(args, timeout=300)
    return _parse_submit_response(r, "image2video")


def _action_image2image(images: str, prompt: str = "", ratio: str = "1:1") -> dict:
    """图生图。"""
    if not images:
        return ac.err(ValueError("缺少 images 参数"))
    img = Path(images)
    if not img.is_file():
        return ac.err(FileNotFoundError(f"图片文件不存在：{images}"))
    args = ["image2image", "--images", images, "--ratio", ratio]
    if prompt:
        args.extend(["--prompt", prompt])
    r = _run_dreamina(args, timeout=300)
    return _parse_submit_response(r, "image2image")


def _parse_submit_response(r: dict, action_name: str) -> dict:
    """解析生成提交命令的响应。"""
    stdout = r["data"]["stdout"]
    submit_id = ""
    gen_status = ""
    fail_reason = ""
    for line in stdout.split("\n"):
        ls = line.strip()
        if "submit_id=" in ls:
            submit_id = ls.split("=", 1)[1].strip()
        if "gen_status=" in ls:
            gen_status = ls.split("=", 1)[1].strip()
        if "fail_reason=" in ls:
            fail_reason = ls.split("=", 1)[1].strip()
        if ls.startswith("{"):
            try:
                j = json.loads(ls)
                if isinstance(j, dict):
                    submit_id = j.get("submit_id") or j.get("submitId") or submit_id
                    gen_status = j.get("gen_status") or j.get("genStatus") or gen_status
                    fail_reason = j.get("fail_reason") or j.get("failReason") or fail_reason
            except json.JSONDecodeError:
                pass
    if not r["ok"] and not submit_id:
        return r
    if gen_status == "fail":
        return {"ok": False, "data": {"action": action_name, "submit_id": submit_id}, "error": {"type": "GenerateFail", "message": f"生成失败：{fail_reason or '未知原因'}"}}
    data = {"action": action_name, "submit_id": submit_id, "gen_status": gen_status or "querying", "stdout": stdout[:2000]}
    if submit_id and gen_status != "fail":
        data["message"] = f"🎬 任务已提交！\n\n**任务ID**：`{submit_id}`\n**状态**：`{gen_status or 'querying'}`\n\n可用 `query_result --submit_id={submit_id}` 查询生成结果。"
    else:
        data["message"] = f"输出：\n```\n{stdout[:2000]}\n```"
    return ac.ok(data)


# ── 主入口 ──

def agent_main(
    *,
    action: str = "user_credit",
    prompt: str = "",
    gen_type: str = "text2image",
    submit_id: str = "",
    device_code: str = "",
    poll: int = 5,
    ratio: str = "16:9",
    resolution_type: str = "2k",
    model_version: str = "",
    count: int = 1,
    images: str = "",
    gen_status: str = "",
    limit: int = 20,
) -> dict:
    """Agent 进程内入口。"""
    try:
        action_map = {
            "login": lambda: _action_login(),
            "check_login": lambda: _action_check_login(device_code, poll),
            "logout": lambda: _action_logout(),
            "user_credit": lambda: _action_user_credit(),
            "generate": lambda: _action_generate(gen_type, prompt, ratio, resolution_type, model_version, count),
            "text2image": lambda: _action_generate("text2image", prompt, ratio, resolution_type, model_version, count),
            "text2video": lambda: _action_generate("text2video", prompt, ratio, resolution_type, model_version, count),
            "query_result": lambda: _action_query_result(submit_id),
            "list_task": lambda: _action_list_task(gen_status, limit),
            "image2video": lambda: _action_image2video(images, prompt, model),
            "image2image": lambda: _action_image2image(images, prompt, ratio),
        }
        handler = action_map.get(action)
        if handler is None:
            return ac.err(ValueError(f"不支持的 action：{action!r}。支持：{', '.join(sorted(action_map.keys()))}"))
        return handler()
    except Exception as e:
        return ac.err(e)




