# -*- coding: utf-8 -*-
"""可灵 Kling AI 全能力工具 — 官方 HTTP API。

支持全部可灵 API 能力：
  视频：text2video, image2video, multimodal2video, multi_image2video,
        motion_control, video_extend, lip_sync, avatar
  图像：text2image, image2image, omni_image, image_upscale, virtual_try_on
  音频：text2audio
  查询：query_result（通用，自动匹配查询路径）

"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import agent_common as ac

_AGENT_CONFIG: dict = {}
_CONFIG_LOADED = False


def _get_cfg(key: str, default: str = "") -> str:
    global _AGENT_CONFIG, _CONFIG_LOADED
    if not _CONFIG_LOADED:
        try:
            from util.config_loader import load_config
            _AGENT_CONFIG = load_config(verbose=False)
        except Exception:
            _AGENT_CONFIG = {}
        _CONFIG_LOADED = True
    return str(_AGENT_CONFIG.get(key, default) or default)


# ── API 端点映射 ──────────────────────────────────────────
_API_ENDPOINTS: Dict[str, str] = {
    "text2video":        "/v1/videos/text2video",
    "image2video":       "/v1/videos/image2video",
    "multimodal2video":  "/v1/videos/omni-video",
    "multi_image2video": "/v1/videos/multi-image2video",
    "motion_control":    "/v1/videos/motion-control",
    "video_extend":      "/v1/videos/video-extend",
    "lip_sync":          "/v1/videos/advanced-lip-sync",
    "avatar":            "/v1/videos/avatar/image2video",
    "text2image":        "/v1/images/generations",
    "image2image":       "/v1/images/generations",
    "multi_image2image": "/v1/images/multi-image2image",
    "omni_image":        "/v1/images/omni-image",
    "virtual_try_on":    "/v1/images/kolors-virtual-try-on",
    "text2audio":        "/v1/audio/text-to-audio",
}
_QUERY_PATHS: Dict[str, str] = {
    "text2video":        "/v1/videos/text2video",
    "image2video":       "/v1/videos/image2video",
    "multimodal2video":  "/v1/videos/omni-video",
    "multi_image2video": "/v1/videos/multi-image2video",
    "motion_control":    "/v1/videos/motion-control",
    "video_extend":      "/v1/videos/video-extend",
    "lip_sync":          "/v1/videos/advanced-lip-sync",
    "avatar":            "/v1/videos/avatar/image2video",
    "text2image":        "/v1/images/generations",
    "image2image":       "/v1/images/generations",
    "multi_image2image": "/v1/images/multi-image2image",
    "omni_image":        "/v1/images/omni-image",
    "virtual_try_on":    "/v1/images/kolors-virtual-try-on",
    "text2audio":        "/v1/audio/text-to-audio",
}
_GENERATE_ACTIONS = set(_API_ENDPOINTS.keys())


def _generate_jwt() -> str:
    ak = _get_cfg("AGENT_KLING_API_KEY")
    sk = _get_cfg("AGENT_KLING_SECRET_KEY")
    if not ak or not sk:
        raise ValueError("config.ini [kling] api_key / secret_key 未配置")
    now = int(time.time())
    payload = {"iss": ak, "exp": now + 3600, "nbf": now - 5, "iat": now - 5}
    hdr = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    pld = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(hmac.new(sk.encode(), f"{hdr}.{pld}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{hdr}.{pld}.{sig}"


def _kling_request(method: str, path: str, body: Optional[dict] = None, timeout: int = 120) -> dict:
    base_url = _get_cfg("AGENT_KLING_API_BASE_URL")
    if not base_url:
        return {"ok": False, "data": None, "error": {"type": "ConfigError", "message": "config.ini [kling] api_base_url 未配置"}}
    url = f"{base_url.rstrip('/')}{path}"
    token = _generate_jwt()
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("code") == 0:
            return ac.ok(result)
        return {"ok": False, "data": result, "error": {"type": "KlingAPIError", "code": str(result.get("code")), "message": result.get("message", "未知错误")}}
    except urllib.error.HTTPError as e:
        bt = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
        try:
            msg = json.loads(bt).get("message", bt) if bt else ""
        except json.JSONDecodeError:
            msg = bt
        return {"ok": False, "data": {"http_code": e.code}, "error": {"type": "KlingAPIError", "message": f"HTTP {e.code}: {msg}"}}
    except urllib.error.URLError as e:
        return ac.err(ConnectionError(f"连接失败: {e.reason}"))
    except Exception as e:
        return ac.err(e)


def _action_create_task(action: str, extra_body: dict) -> dict:
    endpoint = _API_ENDPOINTS.get(action)
    if not endpoint:
        return ac.err(ValueError(f"不支持的操作: {action}"))
    r = _kling_request("POST", endpoint, extra_body, timeout=120)
    if r.get("ok") and isinstance(r.get("data"), dict):
        d = r["data"].get("data", {})
        r["data"]["action"] = action
        r["data"]["task_id"] = d.get("task_id", "")
        r["data"]["task_status"] = d.get("task_status", "")
        prompt = extra_body.get("prompt", "")
        _save_create_meta(d.get("task_id", ""), prompt, extra_body, action)
        msg_parts = [f"🎬 **{_action_label(action)}** 任务已提交！"]
        msg_parts.append(f"操作: {action}")
        msg_parts.append(f"任务ID: `{d.get('task_id', '')}`")
        msg_parts.append(f"状态: `{d.get('task_status', '')}`")
        msg_parts.append(f"注意: 不要频繁调用工具查询状态！用户明确要查再查！！！")
        r["data"]["message"] = "  \n".join(msg_parts)
    return r


def _action_label(action: str) -> str:
    labels = {
        "text2video": "文生视频", "image2video": "图生视频",
        "multimodal2video": "多模态视频", "multi_image2video": "多图参考生视频",
        "motion_control": "动作控制", "video_extend": "视频延长",
        "lip_sync": "对口型", "avatar": "数字人",
        "text2image": "文生图", "image2image": "图生图",
        "omni_image": "Omni 图像", "multi_image2image": "多图参考生图", "virtual_try_on": "虚拟试穿",
        "text2audio": "文生音效",
    }
    return labels.get(action, action)


def _action_query_result(task_id: str = "", external_task_id: str = "",
                         auto_download: bool = True,
                         query_action: str = "") -> dict:
    action = query_action or _guess_action_from_task_id(task_id)
    query_path = _QUERY_PATHS.get(action, "/v1/videos/text2video")
    if not task_id:
        return ac.err(ValueError("查询结果需要提供 task_id"))
    endpoint = f"{query_path}/{task_id}"
    r = _kling_request("GET", endpoint, timeout=30)
    if r.get("ok") and isinstance(r.get("data"), dict):
        d = r["data"].get("data", {})
        r["data"]["action"] = "query_result"
        r["data"]["task_id"] = d.get("task_id", "")
        r["data"]["task_status"] = d.get("task_status", "")
        r["data"]["query_action"] = action
        tr = d.get("task_result", {})
        ret_task_id = d.get("task_id", "") or task_id or ""
        if ret_task_id:
            _save_result_meta(ret_task_id, r["data"])
        videos = tr.get("videos", [])
        images = tr.get("images", [])
        medias = videos or images
        if medias:
            urls = [m.get("url", "") for m in medias if m.get("url")]
            r["data"]["media_urls"] = urls
            if urls:
                md_parts = [f"✅ **生成成功！**\n"]
                for u in urls:
                    if auto_download:
                        lp = _download_media(u, ret_task_id, is_video=bool(videos))
                        if lp:
                            md_parts.append(f"📁 已保存: `{lp}`")
                            try:
                                _lp_path = Path(lp)
                                _fname = _lp_path.name
                                _rel_url = f"/workspace/kling_tasks/{ret_task_id}/{_fname}"
                                if videos:
                                    md_parts.append(f"![播放视频]({_rel_url})")
                                else:
                                    md_parts.append(f"![图片]({_rel_url})")
                            except Exception:
                                pass
                r["data"]["message"] = "\n\n".join(md_parts)
        elif d.get("task_status") == "failed":
            r["data"]["message"] = f"❌ 任务失败: {d.get('task_status_msg', '未知错误')}"
        else:
            r["data"]["message"] = f"⏳ 任务状态: {d.get('task_status', 'unknown')}，请稍后重试查询\n\n注意: 不要频繁调用工具查询状态！用户明确要查再查！！！"
    return r


def _guess_action_from_task_id(task_id: str) -> str:
    if not task_id:
        return "text2video"
    try:
        from pathlib import Path
        ws_str = _get_cfg("AGENT_WORKSPACE_DIR")
        if ws_str:
            meta_dir = Path(ws_str) / "kling_tasks" / task_id
            result_file = meta_dir / f"kling_{task_id}_result.json"
            if result_file.exists():
                data = json.loads(result_file.read_text(encoding="utf-8"))
                act = data.get("action", "")
                if act in _API_ENDPOINTS:
                    return act
            for f in meta_dir.glob("kling_*_*.md"):
                parts = f.stem.split("_", 2)
                if len(parts) >= 3:
                    candidate = parts[2]
                    if candidate in _API_ENDPOINTS:
                        return candidate
    except Exception:
        pass
    return "text2video"


def _download_media(url: str, task_id: str, is_video: bool = True) -> str:
    try:
        from pathlib import Path
        ws_str = _get_cfg("AGENT_WORKSPACE_DIR")
        if not ws_str:
            return ""
        task_dir = Path(ws_str) / "kling_tasks" / (task_id or "unknown")
        task_dir.mkdir(parents=True, exist_ok=True)
        ext = ".mp4" if is_video else ".png"
        fname = f"kling_{task_id}{ext}"
        local_path = task_dir / fname
        if local_path.exists():
            return str(local_path)
        urllib.request.urlretrieve(url, str(local_path))
        return str(local_path)
    except Exception:
        return ""


def _save_result_meta(task_id: str, result: dict):
    try:
        from pathlib import Path
        ws_str = _get_cfg("AGENT_WORKSPACE_DIR")
        if not ws_str or not task_id:
            return
        task_dir = Path(ws_str) / "kling_tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        json_path = task_dir / f"kling_{task_id}_result.json"
        if not json_path.exists():
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _save_create_meta(task_id: str, prompt: str, body: dict, action: str):
    if not task_id:
        return
    try:
        from pathlib import Path
        ws_str = _get_cfg("AGENT_WORKSPACE_DIR")
        if not ws_str:
            return
        task_dir = Path(ws_str) / "kling_tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        md_path = task_dir / f"kling_{task_id}_{action}.md"
        if not md_path.exists():
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"# 任务 {task_id}\n\n## 操作: {action}\n\n## 请求参数\n\n```json\n{json.dumps(body, ensure_ascii=False, indent=2)}\n```\n\n## Prompt\n\n{prompt}\n")
    except Exception:
        pass


def agent_main(*, action: str = "text2video", prompt: str = "",
               model_name: str = "kling-v1", duration: str = "5",
               mode: str = "std", sound: str = "off",
               aspect_ratio: str = "16:9",
               negative_prompt: str = "", cfg_scale: float = 0.5,
               multi_shot: bool = False, shot_type: str = "",
               multi_prompt: Optional[list] = None,
               task_id: str = "", external_task_id: str = "",
               callback_url: str = "",
               camera_control: Optional[dict] = None,
               watermark_enabled: Optional[bool] = None,
               image_url: str = "",
               image_list: Optional[list] = None,
               num_images: int = 1,
               video_id: str = "",
               audio_url: str = "",
               query_action: str = "",
               run_type: str = "") -> dict:
    try:
        if not _get_cfg("AGENT_KLING_API_KEY") or not _get_cfg("AGENT_KLING_SECRET_KEY"):
            return {"ok": False, "data": None, "error": {"type": "ConfigError", "message": "config.ini [kling] api_key/secret_key 未配置"}}
        if action in _GENERATE_ACTIONS and run_type == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，禁止执行生成操作（会消耗积分）。请先切换为 Execute 模式或得到用户明确授权后再执行。"}}
        if action == "query_result":
            return _action_query_result(task_id, external_task_id, query_action=query_action)
        if action in _GENERATE_ACTIONS:
            body = _build_body_for_action(action, prompt, model_name, duration, mode, sound,
                                          aspect_ratio, negative_prompt, cfg_scale,
                                          multi_shot, shot_type, multi_prompt,
                                          external_task_id, callback_url, camera_control,
                                          watermark_enabled, image_url, image_list,
                                          num_images, video_id, audio_url)
            if action == "text2video":
                return _action_text2video(prompt, model_name, duration, mode, sound, aspect_ratio,
                                          negative_prompt, cfg_scale, multi_shot, shot_type,
                                          multi_prompt, external_task_id, callback_url,
                                          camera_control, watermark_enabled)
            return _action_create_task(action, body)
        return ac.err(ValueError(f"不支持的 action：{action}"))
    except Exception as e:
        return ac.err(e)


def _build_body_for_action(action, prompt, model_name, duration, mode, sound,
                           aspect_ratio, negative_prompt, cfg_scale,
                           multi_shot, shot_type, multi_prompt,
                           external_task_id, callback_url, camera_control,
                           watermark_enabled, image_url, image_list,
                           num_images, video_id, audio_url) -> dict:
    if action == "text2video":
        body = {"model_name": model_name, "prompt": prompt, "duration": duration,
                "mode": mode, "sound": sound, "aspect_ratio": aspect_ratio, "cfg_scale": cfg_scale}
        if negative_prompt: body["negative_prompt"] = negative_prompt
        if multi_shot: body["multi_shot"] = True
        if shot_type: body["shot_type"] = shot_type
        if multi_prompt: body["multi_prompt"] = multi_prompt
        if external_task_id: body["external_task_id"] = external_task_id
        if callback_url: body["callback_url"] = callback_url
        if camera_control: body["camera_control"] = camera_control
        if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
        return body
    if action == "image2video":
        return _build_image2video_body(prompt, image_url, image_list, model_name, duration, mode,
                                       aspect_ratio, negative_prompt, cfg_scale, camera_control,
                                       watermark_enabled, callback_url)
    if action in ("multimodal2video", "omni_image"):
        return _build_multimodal_body(prompt, image_list, model_name, duration, mode, callback_url)
    if action == "multi_image2video":
        return _build_multi_image2video_body(prompt, image_list, model_name, duration, mode, aspect_ratio, callback_url)
    if action == "motion_control":
        return _build_motion_control_body(prompt, image_url, image_list, model_name, duration, mode, camera_control, callback_url)
    if action == "video_extend":
        return _build_video_extend_body(video_id, duration)
    if action == "lip_sync":
        return _build_lip_sync_body(video_id, audio_url, prompt)
    if action == "avatar":
        return _build_avatar_body(prompt, image_url, model_name, mode, callback_url)
    if action == "text2image":
        return _build_text2image_body(prompt, model_name, aspect_ratio, num_images, negative_prompt, callback_url)
    if action == "image2image":
        return _build_image2image_body(prompt, image_url, image_list, model_name, aspect_ratio, num_images, callback_url)
    if action == "multi_image2image":
        return _build_image2image_multi_body(prompt, image_list, model_name, aspect_ratio, 1, callback_url)
    if action == "virtual_try_on":
        return _build_try_on_body(image_url, image_list)
    if action == "text2audio":
        return _build_text2audio_body(prompt, duration)
    raise ValueError(f"不支持的操作: {action}")


def _build_image2video_body(prompt, image_url, image_list, model_name, duration, mode,
                            aspect_ratio, negative_prompt, cfg_scale, camera_control,
                            watermark_enabled, callback_url):
    if not image_url and not image_list:
        raise ValueError("图生视频需要提供 image_url 或 image_list")
    body = _base_video_body(prompt, model_name, duration, mode, aspect_ratio, negative_prompt, cfg_scale, callback_url)
    if image_url: body["image_url"] = image_url
    if image_list: body["image_list"] = image_list
    if camera_control: body["camera_control"] = camera_control
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    return body


def _build_image2image_multi_body(prompt, image_list, model_name, aspect_ratio, num_images, callback_url):
    if not image_list:
        raise ValueError("多图参考生图需要提供图片列表")
    body: dict = {"image_list": image_list}
    if prompt: body["prompt"] = prompt
    if model_name: body["model_name"] = model_name
    if aspect_ratio: body["aspect_ratio"] = aspect_ratio
    if callback_url: body["callback_url"] = callback_url
    body["n"] = max(1, int(num_images))
    return body


def _build_multimodal_body(prompt, image_list, model_name, duration, mode, callback_url):
    if not prompt and not image_list:
        raise ValueError("多模态需要提供 prompt 或 image_list")
    body: dict = {}
    if prompt: body["prompt"] = prompt
    if image_list: body["image_list"] = image_list
    if model_name: body["model_name"] = model_name
    if duration: body["duration"] = duration
    if mode: body["mode"] = mode
    if callback_url: body["callback_url"] = callback_url
    return body


def _build_multi_image2video_body(prompt, image_list, model_name, duration, mode, aspect_ratio, callback_url):
    if not image_list or len(image_list) < 2:
        raise ValueError("多图参考生视频至少需要 2 张图")
    body = _base_video_body(prompt, model_name, duration, mode, aspect_ratio, "", 0.5, callback_url)
    body["image_list"] = image_list
    return body


def _build_motion_control_body(prompt, image_url, image_list, model_name, duration, mode, camera_control, callback_url):
    if not image_url and not image_list:
        raise ValueError("动作控制需要提供图片")
    body = _base_video_body(prompt, model_name, duration, mode, "16:9", "", 0.5, callback_url)
    if image_url: body["image_url"] = image_url
    if image_list: body["image_list"] = image_list
    if camera_control: body["camera_control"] = camera_control
    return body


def _build_video_extend_body(video_id, duration):
    if not video_id:
        raise ValueError("视频延长需要提供 video_id")
    return {"video_id": video_id, "duration": duration or "5"}


def _build_lip_sync_body(video_id, audio_url, prompt):
    if not video_id or not audio_url:
        raise ValueError("对口型需要提供 video_id 和 audio_url")
    body = {"video_id": video_id, "audio_url": audio_url}
    if prompt: body["prompt"] = prompt
    return body


def _build_avatar_body(prompt, image_url, model_name, mode, callback_url):
    if not image_url:
        raise ValueError("数字人需要提供 image_url")
    body = {"image_url": image_url, "model_name": model_name or "kling-v1", "mode": mode or "pro"}
    if prompt: body["prompt"] = prompt
    if callback_url: body["callback_url"] = callback_url
    return body


def _build_text2image_body(prompt, model_name, aspect_ratio, num_images, negative_prompt, callback_url):
    if not prompt:
        raise ValueError("文生图需要提供 prompt")
    body: dict = {"prompt": prompt, "n": max(1, int(num_images))}
    if model_name: body["model_name"] = model_name
    if aspect_ratio: body["aspect_ratio"] = aspect_ratio
    if negative_prompt: body["negative_prompt"] = negative_prompt
    if callback_url: body["callback_url"] = callback_url
    return body


def _build_image2image_body(prompt, image_url, image_list, model_name, aspect_ratio, num_images, callback_url):
    if not prompt:
        raise ValueError("文生图/图生图需要提供 prompt")
    if not image_url and not image_list:
        raise ValueError("图生图需要提供原图")
    body: dict = {"prompt": prompt, "n": max(1, int(num_images))}
    if image_url:
        body["image"] = image_url  # 官方API参数名为 image
        body["image_reference"] = "subject"  # 默认人物长相参考
    if image_list:
        body["image_list"] = image_list
    if model_name: body["model_name"] = model_name
    if aspect_ratio: body["aspect_ratio"] = aspect_ratio
    if callback_url: body["callback_url"] = callback_url
    return body


def _build_try_on_body(image_url, image_list):
    if not image_url and not image_list:
        raise ValueError("虚拟试穿需要提供人物图 + 服装图")
    body: dict = {}
    if image_url: body["person_image_url"] = image_url
    if image_list:
        for img in image_list:
            if isinstance(img, dict):
                if img.get("type") == "person":
                    body.setdefault("person_image_url", img.get("url", ""))
                elif img.get("type") == "garment":
                    body.setdefault("garment_image_url", img.get("url", ""))
    if not body.get("person_image_url") or not body.get("garment_image_url"):
        raise ValueError("虚拟试穿需要同时提供人物图(person)和服装图(garment)")
    return body


def _build_text2audio_body(prompt, duration):
    if not prompt:
        raise ValueError("文生音效需要提供 prompt")
    body: dict = {"prompt": prompt}
    if duration: body["duration"] = duration
    return body


def _base_video_body(prompt, model_name, duration, mode, aspect_ratio, negative_prompt, cfg_scale, callback_url):
    body: dict = {"prompt": prompt, "model_name": model_name, "duration": duration,
                  "mode": mode, "aspect_ratio": aspect_ratio, "cfg_scale": cfg_scale}
    if negative_prompt: body["negative_prompt"] = negative_prompt
    if callback_url: body["callback_url"] = callback_url
    return body


def _action_text2video(prompt: str, model_name: str = "kling-v1", duration: str = "5",
                       mode: str = "std", sound: str = "off", aspect_ratio: str = "16:9",
                       negative_prompt: str = "", cfg_scale: float = 0.5,
                       multi_shot: bool = False, shot_type: str = "",
                       multi_prompt: Optional[list] = None,
                       external_task_id: str = "", callback_url: str = "",
                       camera_control: Optional[dict] = None,
                       watermark_enabled: Optional[bool] = None) -> dict:
    if not prompt and not multi_shot:
        return ac.err(ValueError("缺少 prompt 参数"))
    body = {"model_name": model_name, "prompt": prompt, "duration": duration,
            "mode": mode, "sound": sound, "aspect_ratio": aspect_ratio, "cfg_scale": cfg_scale}
    if negative_prompt: body["negative_prompt"] = negative_prompt
    if multi_shot:
        body["multi_shot"] = True
        if shot_type: body["shot_type"] = shot_type
        if multi_prompt:
            for mp in multi_prompt:
                if isinstance(mp, dict) and len(mp.get("prompt", "")) > 512:
                    mp["prompt"] = mp["prompt"][:512]
            body["multi_prompt"] = multi_prompt
    if external_task_id: body["external_task_id"] = external_task_id
    if callback_url: body["callback_url"] = callback_url
    if camera_control: body["camera_control"] = camera_control
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    r = _kling_request("POST", "/v1/videos/text2video", body, timeout=120)
    if r.get("ok") and isinstance(r.get("data"), dict):
        d = r["data"].get("data", {})
        r["data"]["action"] = "text2video"
        r["data"]["task_id"] = d.get("task_id", "")
        r["data"]["task_status"] = d.get("task_status", "")
        _save_create_meta(d.get("task_id", ""), prompt, body, "text2video")
    return r


def main() -> None:
    p = argparse.ArgumentParser(description="可灵 Kling AI 全能力工具")
    p.add_argument("--action", default="text2video", help="操作类型")
    p.add_argument("--prompt", default="", help="提示词")
    p.add_argument("--model_name", default="kling-v1", help="模型名称")
    p.add_argument("--duration", default="5", help="时长(秒)")
    p.add_argument("--mode", default="std", help="模式：std/pro/4k")
    p.add_argument("--sound", default="off", help="声音：on/off")
    p.add_argument("--aspect_ratio", default="16:9", help="画面比例")
    p.add_argument("--negative_prompt", default="", help="负向提示词")
    p.add_argument("--cfg_scale", type=float, default=0.5, help="自由度")
    p.add_argument("--multi_shot", action="store_true", help="多镜头")
    p.add_argument("--shot_type", default="", help="分镜方式")
    p.add_argument("--task_id", default="", help="查询任务ID")
    p.add_argument("--external_task_id", default="", help="自定义任务ID")
    p.add_argument("--image_url", default="", help="图片URL")
    p.add_argument("--num_images", type=int, default=1, help="生成图片数量")
    p.add_argument("--video_id", default="", help="视频ID")
    p.add_argument("--audio_url", default="", help="音频URL")
    p.add_argument("--query_action", default="", help="查询时指定操作类型")
    p.add_argument("--callback_url", default="", help="任务回调URL")
    p.add_argument("--watermark_enabled", action="store_true", help="是否生成水印")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(action=args.action, prompt=args.prompt, model_name=args.model_name,
                   duration=args.duration, mode=args.mode, sound=args.sound,
                   aspect_ratio=args.aspect_ratio, negative_prompt=args.negative_prompt,
                   cfg_scale=args.cfg_scale, multi_shot=args.multi_shot,
                   shot_type=args.shot_type, task_id=args.task_id,
                   external_task_id=args.external_task_id,
                   image_url=args.image_url, num_images=args.num_images,
                   video_id=args.video_id, audio_url=args.audio_url,
                   query_action=args.query_action)
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok") and isinstance(r.get("data"), dict):
            d = r["data"]
            print(f"操作: {d.get('action')}")
            if d.get("task_id"):
                print(f"任务ID: {d.get('task_id', '')}")
                print(f"状态: {d.get('task_status', '')}")
            msg = d.get("message", "")
            if msg:
                print(msg)
        err = r.get("error") or {}
        if err:
            print(f"错误: {err.get('message', '')}", file=sys.stderr)


if __name__ == "__main__":
    main()
