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
    "video_to_audio":    "/v1/audio/video-to-audio",
    "tts":               "/v1/audio/tts",
    "image_expansion":   "/v1/images/editing/expand",
    "ai_multi_shot":     "/v1/general/ai-multi-shot",
    "image_recognize":   "/v1/videos/image-recognize",
    "custom_voice_create": "/v1/general/custom-voices",
    "custom_voice_delete": "/v1/general/delete-voices",
    "video_effect":       "/v1/videos/effects",
    "element_create":     "/v1/general/advanced-custom-elements",
}
_ACCOUNT_INFO_PATH = "/account/costs"
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
    "video_to_audio":    "/v1/audio/video-to-audio",
    "tts":               "/v1/audio/tts",
    "image_expansion":   "/v1/images/editing/expand",
    "ai_multi_shot":     "/v1/general/ai-multi-shot",
    "image_recognize":   "/v1/videos/image-recognize",
    "custom_voice_create": "/v1/general/custom-voices",
    "custom_voice_delete": "/v1/general/delete-voices",
    "video_effect":       "/v1/videos/effects",
    "element_create":     "/v1/general/advanced-custom-elements",
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
        "video_to_audio": "视频配音效",
        "account_info": "账号信息查询",
        "tts": "语音合成",
        "image_expansion": "扩图",
        "ai_multi_shot": "智能补全主体图",
        "image_recognize": "图像识别",
        "custom_voice_create": "创建自定义音色",
        "custom_voice_delete": "删除自定义音色",
        "video_effect": "视频特效",
        "element_create": "创建主体",
        "query_result": "查询结果",
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
        audios = tr.get("audios", [])
        medias = videos or images or audios
        if medias:
            urls = []
            for m in medias:
                url = m.get("url", "") or m.get("url_mp3", "") or m.get("url_wav", "")
                if url:
                    urls.append(url)
            r["data"]["media_urls"] = urls
            if urls:
                md_parts = [f"✅ **生成成功！**\n"]
                for u in urls:
                    if auto_download:
                        lp = _download_media(u, ret_task_id, is_video=bool(videos), is_audio=bool(audios))
                        if lp:
                            md_parts.append(f"📁 已保存: `{lp}`")
                            try:
                                _lp_path = Path(lp)
                                _fname = _lp_path.name
                                _rel_url = f"/workspace/kling_tasks/{ret_task_id}/{_fname}"
                                if videos:
                                    md_parts.append(f"![播放视频]({_rel_url})")
                                elif audios:
                                    md_parts.append(f"🔊 [播放音频]({_rel_url})")
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


def _download_media(url: str, task_id: str, is_video: bool = True, is_audio: bool = False) -> str:
    try:
        from pathlib import Path
        ws_str = _get_cfg("AGENT_WORKSPACE_DIR")
        if not ws_str:
            return ""
        task_dir = Path(ws_str) / "kling_tasks" / (task_id or "unknown")
        task_dir.mkdir(parents=True, exist_ok=True)
        if is_audio:
            ext = ".mp3"
        elif is_video:
            ext = ".mp4"
        else:
            ext = ".png"
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


def _action_account_info(start_time: str = "", end_time: str = "") -> dict:
    """查询账号下资源包列表及余量（免费接口）。"""
    import time
    now_ms = int(time.time() * 1000)
    params = {}
    params["start_time"] = start_time or str(now_ms - 86400000 * 30)  # 默认近30天
    params["end_time"] = end_time or str(now_ms)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    r = _kling_request("GET", f"{_ACCOUNT_INFO_PATH}?{qs}", timeout=30)
    if r.get("ok"):
        d = r.get("data", {})
        r["data"] = {
            "action": "account_info",
            "data": d.get("data", d),
        }
    return r


def agent_main(*, action: str = "text2video", prompt: str = "",
               model_name: str = "kling-v3", duration: str = "5",
               mode: str = "std", sound: str = "off",
               aspect_ratio: str = "16:9",
               negative_prompt: str = "", cfg_scale: float = 0.5,
               multi_shot: bool = False, shot_type: str = "",
               multi_prompt: Optional[list] = None,
               task_id: str = "", external_task_id: str = "",
               callback_url: str = "",
               camera_control: Optional[dict] = None,
               watermark_enabled: Optional[bool] = None,
               image_url: str = "", image_tail: str = "",
               image_list: Optional[list] = None,
               num_images: int = 1,
               video_id: str = "", video_url: str = "",
               audio_url: str = "",
               element_list: Optional[list] = None,
               voice_list: Optional[list] = None,
               keep_original_sound: str = "",
               character_orientation: str = "",
               resolution: str = "",
               scene_image: str = "",
               style_image: str = "",
               sound_effect_prompt: str = "",
               bgm_prompt: str = "",
               up_ratio: float = 0.1,
               down_ratio: float = 0.1,
               left_ratio: float = 0.1,
               right_ratio: float = 0.1,
               asmr_mode: bool = False,
               result_type: str = "",
               series_amount: str = "",
               voice_id: str = "",
               voice_language: str = "",
               voice_speed: float = 1.0,
               query_action: str = "",
               run_type: str = "") -> dict:
    try:
        if not _get_cfg("AGENT_KLING_API_KEY") or not _get_cfg("AGENT_KLING_SECRET_KEY"):
            return {"ok": False, "data": None, "error": {"type": "ConfigError", "message": "config.ini [kling] api_key/secret_key 未配置"}}
        if action in _GENERATE_ACTIONS and run_type == "plan":
            return {"ok": False, "data": None, "error": {"type": "ModeConflict", "message": "当前为 Plan 模式，禁止执行生成操作（会消耗积分）。请先切换为 Execute 模式或得到用户明确授权后再执行。"}}
        if action == "account_info":
            return _action_account_info(start_time="", end_time="")
        if action == "query_result":
            return _action_query_result(task_id, external_task_id, query_action=query_action)
        if action == "image_recognize":
            if not image_url:
                return ac.err(ValueError("图像识别需要提供 image_url"))
            r = _kling_request("POST", "/v1/videos/image-recognize", {"image": image_url}, timeout=30)
            if r.get("ok"):
                d = r.get("data", {})
                r["data"] = {"action": "image_recognize", "data": d.get("data", d.get("task_result", d))}
            return r
        if action == "custom_voice_create":
            if not image_url and not video_id:
                return ac.err(ValueError("创建自定义音色需要提供 voice_url(image_url) 或 video_id"))
            body = {}
            if image_url: body["voice_url"] = image_url
            if video_id: body["video_id"] = video_id
            body["voice_name"] = prompt or "自定义音色"
            if callback_url: body["callback_url"] = callback_url
            if external_task_id: body["external_task_id"] = external_task_id
            r = _kling_request("POST", "/v1/general/custom-voices", body, timeout=120)
            if r.get("ok"):
                d = r.get("data", {})
                r["data"] = {"action": "custom_voice_create", "task_id": d.get("data", {}).get("task_id", ""), "task_status": d.get("data", {}).get("task_status", "")}
            return r
        if action == "custom_voice_delete":
            if not task_id:
                return ac.err(ValueError("删除自定义音色需要提供 task_id(voice_id)"))
            r = _kling_request("POST", "/v1/general/delete-voices", {"voice_id": task_id}, timeout=30)
            return r
        if action in _GENERATE_ACTIONS:
            # 硬编码：单分镜提示词不得少于450字，不足直接拒绝
            _prompts_to_check = []
            if prompt:
                _prompts_to_check.append(prompt)
            if multi_prompt:
                _prompts_to_check.extend(multi_prompt)
            for pi, pp in enumerate(_prompts_to_check):
                if len(pp) < 450:
                    _label = f"第{pi+1}个分镜" if len(_prompts_to_check) > 1 else "分镜"
                    return {"ok": False, "data": None, "error": {"type": "PromptTooShort", "message": f"{_label}提示词内容仅{len(pp)}字，不满足最少450字要求。请丰富细节（景别、焦段、光圈、景深、焦点运动、构图、光位、背景、动作、转场、环境一致性、物理检查等12维专业描述），补充后再提交。"}}
            body = _build_body_for_action(action, prompt, model_name, duration, mode, sound,
                                          aspect_ratio, negative_prompt, cfg_scale,
                                          multi_shot, shot_type, multi_prompt,
                                          external_task_id, callback_url, camera_control,
                                          watermark_enabled, image_url, image_list,
                                          num_images, video_id, audio_url,
                                          image_tail, element_list, voice_list,
                                          video_url, keep_original_sound, character_orientation,
                                          scene_image, style_image,
                                          sound_effect_prompt, bgm_prompt, asmr_mode,
                                          up_ratio, down_ratio, left_ratio, right_ratio,
                                          resolution, result_type, series_amount,
                                          voice_id, voice_language, voice_speed)
            if action == "text2video":
                return _action_text2video(prompt, model_name, duration, mode, sound, aspect_ratio,
                                          negative_prompt, cfg_scale, multi_shot, shot_type,
                                          multi_prompt, external_task_id, callback_url,
                                          camera_control, watermark_enabled,
                                          element_list, voice_list)
            return _action_create_task(action, body)
        return ac.err(ValueError(f"不支持的 action：{action}"))
    except Exception as e:
        return ac.err(e)


def _build_body_for_action(action, prompt, model_name, duration, mode, sound,
                           aspect_ratio, negative_prompt, cfg_scale,
                           multi_shot, shot_type, multi_prompt,
                           external_task_id, callback_url, camera_control,
                           watermark_enabled, image_url, image_list,
                           num_images, video_id, audio_url,
                           image_tail="", element_list=None, voice_list=None,
                           video_url="", keep_original_sound="", character_orientation="",
                           scene_image="", style_image="",
                           sound_effect_prompt="", bgm_prompt="", asmr_mode=False,
                           up_ratio=0.1, down_ratio=0.1, left_ratio=0.1, right_ratio=0.1,
                           resolution="", result_type="", series_amount="",
                           voice_id="", voice_language="", voice_speed=1.0) -> dict:
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
        if element_list: body["element_list"] = element_list
        if voice_list: body["voice_list"] = voice_list
        return body
    if action == "image2video":
        return _build_image2video_body(prompt, image_url, image_list, image_tail, model_name, duration, mode,
                                       aspect_ratio, negative_prompt, cfg_scale, camera_control, sound,
                                       watermark_enabled, callback_url, external_task_id,
                                       multi_shot, shot_type, multi_prompt, element_list, voice_list)
    if action == "multimodal2video":
        return _build_multimodal_body(prompt, image_list, model_name, duration, mode, sound, aspect_ratio,
                                       multi_shot, shot_type, multi_prompt, element_list, None,
                                       callback_url, external_task_id, watermark_enabled, voice_list)
    if action == "omni_image":
        return _build_omni_image_body(prompt, image_list, element_list, model_name, aspect_ratio, num_images,
                                       resolution, result_type, series_amount, callback_url, external_task_id, watermark_enabled)
    if action == "multi_image2video":
        return _build_multi_image2video_body(prompt, image_list, model_name, duration, mode, aspect_ratio,
                                             negative_prompt, callback_url, external_task_id, watermark_enabled)
    if action == "motion_control":
        return _build_motion_control_body(prompt, image_url, image_list, video_url, keep_original_sound, character_orientation, element_list, model_name, mode, {"enabled": watermark_enabled} if watermark_enabled is not None else None, callback_url, external_task_id)
    if action == "video_extend":
        return _build_video_extend_body(video_id, prompt, negative_prompt, cfg_scale, callback_url, external_task_id, watermark_enabled)
    if action == "lip_sync":
        return _build_lip_sync_body(video_id, audio_url, prompt)
    if action == "avatar":
        return _build_avatar_body(prompt, image_url, model_name, mode, callback_url, external_task_id)
    if action == "text2image":
        return _build_text2image_body(prompt, model_name, aspect_ratio, num_images, negative_prompt, callback_url,
                                       external_task_id, watermark_enabled)
    if action == "image2image":
        return _build_image2image_body(prompt, image_url, image_list, model_name, aspect_ratio, num_images, callback_url,
                                        external_task_id, watermark_enabled)
    if action == "multi_image2image":
        return _build_image2image_multi_body(prompt, image_list, model_name, aspect_ratio, num_images, callback_url,
                                              scene_image, style_image)
    if action == "virtual_try_on":
        return _build_try_on_body(image_url, image_list, model_name, callback_url, external_task_id)
    if action == "text2audio":
        return _build_text2audio_body(prompt, duration)
    if action == "video_to_audio":
        return _build_video_to_audio_body(video_id, video_url, sound_effect_prompt, bgm_prompt, asmr_mode, callback_url, external_task_id)
    if action == "tts":
        return _build_tts_body(prompt, voice_id, voice_language, voice_speed, callback_url, external_task_id)
    if action == "image_expansion":
        return _build_image_expansion_body(image_url, up_ratio, down_ratio, left_ratio, right_ratio, prompt, num_images, callback_url, external_task_id, watermark_enabled)
    if action == "ai_multi_shot":
        return _build_ai_multi_shot_body(image_url, callback_url, external_task_id)
    if action == "image_recognize":
        if not image_url:
            raise ValueError("图像识别需要提供 image_url")
        r = _kling_request("POST", "/v1/videos/image-recognize", {"image": image_url}, timeout=30)
        if r.get("ok"):
            d = r.get("data", {})
            r["data"] = {"action": "image_recognize", "data": d.get("data", d.get("task_result", d))}
        return r
    if action == "custom_voice_create":
        if not image_url and not video_id:
            raise ValueError("创建自定义音色需要提供 voice_url(image_url) 或 video_id")
        body = {}
        if image_url: body["voice_url"] = image_url
        if video_id: body["video_id"] = video_id
        body["voice_name"] = prompt or "自定义音色"
        if callback_url: body["callback_url"] = callback_url
        if external_task_id: body["external_task_id"] = external_task_id
        r = _kling_request("POST", "/v1/general/custom-voices", body, timeout=120)
        if r.get("ok"):
            d = r.get("data", {})
            r["data"] = {"action": "custom_voice_create", "task_id": d.get("data", {}).get("task_id", ""), "task_status": d.get("data", {}).get("task_status", "")}
        return r
    if action == "custom_voice_delete":
        if not task_id:
            raise ValueError("删除自定义音色需要提供 task_id(voice_id)")
        r = _kling_request("POST", "/v1/general/delete-voices", {"voice_id": task_id}, timeout=30)
        return r
    if action == "video_effect":
        if not image_url and not image_list:
            raise ValueError("视频特效需要提供 image_url 或 image_list")
        input_data = {}
        if image_url: input_data["image"] = image_url
        if image_list: input_data["images"] = image_list
        body = {"effect_scene": prompt or "color_mixing", "input": input_data}
        if callback_url: body["callback_url"] = callback_url
        if external_task_id: body["external_task_id"] = external_task_id
        return _action_create_task(action, body)
    if action == "element_create":
        if not image_url:
            raise ValueError("创建主体需要提供 image_url(正面参考图)")
        body = {
            "element_name": prompt or "自定义主体",
            "element_description": negative_prompt or "由AI生成的主体",
            "reference_type": "image_refer",
            "element_image_list": {"frontal_image": image_url}
        }
        if callback_url: body["callback_url"] = callback_url
        if external_task_id: body["external_task_id"] = external_task_id
        return _action_create_task(action, body)
    raise ValueError(f"不支持的操作: {action}")


def _build_image2video_body(prompt, image_url, image_list, image_tail, model_name, duration, mode,
                            aspect_ratio, negative_prompt, cfg_scale, camera_control, sound,
                            watermark_enabled, callback_url, external_task_id,
                            multi_shot=False, shot_type="", multi_prompt=None,
                            element_list=None, voice_list=None):
    if not image_url and not image_list and not image_tail:
        raise ValueError("图生视频需要提供 image_url/image_list/image_tail 之一")
    body = _base_video_body(prompt, model_name, duration, mode, aspect_ratio, negative_prompt, cfg_scale, callback_url)
    if image_url: body["image"] = image_url
    if image_tail: body["image_tail"] = image_tail
    if image_list: body["image_list"] = image_list
    if camera_control: body["camera_control"] = camera_control
    if sound: body["sound"] = sound
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    if external_task_id: body["external_task_id"] = external_task_id
    if multi_shot:
        body["multi_shot"] = True
        if shot_type: body["shot_type"] = shot_type
        if multi_prompt: body["multi_prompt"] = multi_prompt
    if element_list: body["element_list"] = element_list
    if voice_list: body["voice_list"] = voice_list
    return body


def _build_image2image_multi_body(prompt, image_list, model_name, aspect_ratio, num_images, callback_url,
                                  scene_image="", style_image=""):
    if not image_list:
        raise ValueError("多图参考生图需要提供 subject_image_list")
    body: dict = {"subject_image_list": image_list}
    if prompt: body["prompt"] = prompt
    if model_name: body["model_name"] = model_name
    if aspect_ratio: body["aspect_ratio"] = aspect_ratio
    if callback_url: body["callback_url"] = callback_url
    if scene_image: body["scene_image"] = scene_image
    if style_image: body["style_image"] = style_image
    body["n"] = max(1, int(num_images))
    return body


def _build_omni_image_body(prompt, image_list, element_list, model_name, aspect_ratio, num_images,
                            resolution, result_type, series_amount, callback_url, external_task_id,
                            watermark_enabled):
    if not prompt:
        raise ValueError("OmniImage 需要提供 prompt")
    body: dict = {"prompt": prompt}
    if image_list: body["image_list"] = image_list
    if element_list: body["element_list"] = element_list
    if model_name: body["model_name"] = model_name
    if aspect_ratio: body["aspect_ratio"] = aspect_ratio
    if resolution: body["resolution"] = resolution
    if result_type: body["result_type"] = result_type
    if series_amount: body["series_amount"] = series_amount
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    body["n"] = max(1, int(num_images))
    return body


def _build_multimodal_body(prompt, image_list, model_name, duration, mode, sound, aspect_ratio,
                            multi_shot, shot_type, multi_prompt, element_list, video_list,
                            callback_url, external_task_id, watermark_enabled, voice_list=None):
    if not prompt and not image_list and not video_list:
        raise ValueError("多模态需要提供 prompt/image_list/video_list 至少一项")
    body: dict = {}
    if prompt: body["prompt"] = prompt
    if image_list: body["image_list"] = image_list
    if model_name: body["model_name"] = model_name
    if duration: body["duration"] = duration
    if mode: body["mode"] = mode
    if sound: body["sound"] = sound
    if aspect_ratio: body["aspect_ratio"] = aspect_ratio
    if multi_shot: body["multi_shot"] = True
    if shot_type: body["shot_type"] = shot_type
    if multi_prompt: body["multi_prompt"] = multi_prompt
    if element_list: body["element_list"] = element_list
    if video_list: body["video_list"] = video_list
    if voice_list: body["voice_list"] = voice_list
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    return body


def _build_multi_image2video_body(prompt, image_list, model_name, duration, mode, aspect_ratio,
                                  negative_prompt, callback_url, external_task_id, watermark_enabled):
    if not image_list or len(image_list) < 2:
        raise ValueError("多图参考生视频至少需要 2 张图")
    body = _base_video_body(prompt, model_name, duration, mode, aspect_ratio, negative_prompt or "", 0.5, callback_url)
    body["image_list"] = image_list
    if external_task_id: body["external_task_id"] = external_task_id
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    return body


def _build_motion_control_body(prompt, image_url, image_list, video_url, keep_original_sound, character_orientation, element_list, model_name, mode, watermark_info, callback_url, external_task_id):
    if not image_url:
        raise ValueError("动作控制需要提供 image_url")
    if not video_url:
        raise ValueError("动作控制需要提供 video_url")
    body = {"image": image_url, "video_url": video_url, "mode": mode or "pro"}
    if model_name: body["model_name"] = model_name
    if prompt: body["prompt"] = prompt
    if keep_original_sound: body["keep_original_sound"] = keep_original_sound
    if character_orientation: body["character_orientation"] = character_orientation
    if element_list: body["element_list"] = element_list
    if watermark_info is not None: body["watermark_info"] = watermark_info
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    return body


def _build_video_extend_body(video_id, prompt, negative_prompt, cfg_scale, callback_url, external_task_id, watermark_enabled):
    if not video_id:
        raise ValueError("视频延长需要提供 video_id")
    body = {"video_id": video_id}
    if prompt: body["prompt"] = prompt
    if negative_prompt: body["negative_prompt"] = negative_prompt
    if cfg_scale is not None: body["cfg_scale"] = cfg_scale
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    return body


def _build_lip_sync_body(video_id, audio_url, prompt):
    if not video_id or not audio_url:
        raise ValueError("对口型需要提供 video_id 和 audio_url")
    body = {"video_id": video_id, "audio_url": audio_url}
    if prompt: body["prompt"] = prompt
    return body


def _build_avatar_body(prompt, image_url, model_name, mode, callback_url, external_task_id=""):
    if not image_url:
        raise ValueError("数字人需要提供 image_url")
    body = {"image_url": image_url, "model_name": model_name or "kling-v3", "mode": mode or "pro"}
    if prompt: body["prompt"] = prompt
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    return body


def _build_text2image_body(prompt, model_name, aspect_ratio, num_images, negative_prompt, callback_url,
                            external_task_id="", watermark_enabled=None):
    if not prompt:
        raise ValueError("文生图需要提供 prompt")
    body: dict = {"prompt": prompt, "n": max(1, int(num_images))}
    if model_name: body["model_name"] = model_name
    if aspect_ratio: body["aspect_ratio"] = aspect_ratio
    if negative_prompt: body["negative_prompt"] = negative_prompt
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    return body


def _build_image2image_body(prompt, image_url, image_list, model_name, aspect_ratio, num_images, callback_url,
                             external_task_id="", watermark_enabled=None):
    if not prompt:
        raise ValueError("文生图/图生图需要提供 prompt")
    if not image_url and not image_list:
        raise ValueError("图生图需要提供 image_url 或 image_list")
    body: dict = {"prompt": prompt, "n": max(1, int(num_images))}
    if image_url:
        body["image"] = image_url  # 可灵API字段名为 image（对应工具参数 image_url）
        body["image_reference"] = "subject"  # 默认人物长相参考
    if image_list:
        body["image_list"] = image_list
    if model_name: body["model_name"] = model_name
    if aspect_ratio: body["aspect_ratio"] = aspect_ratio
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    return body


def _build_try_on_body(image_url, image_list, model_name, callback_url, external_task_id):
    if not image_url and not image_list:
        raise ValueError("虚拟试穿需要提供人物图 + 服装图")
    body: dict = {}
    if model_name: body["model_name"] = model_name
    if image_url: body["human_image"] = image_url
    if image_list:
        for img in image_list:
            if isinstance(img, dict):
                if img.get("type") in ("person", "human"):
                    body.setdefault("human_image", img.get("url", ""))
                elif img.get("type") in ("garment", "cloth"):
                    body.setdefault("cloth_image", img.get("url", ""))
    if not body.get("human_image") or not body.get("cloth_image"):
        raise ValueError("虚拟试穿需要同时提供人物图(human_image)和服装图(cloth_image)")
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    return body


def _build_video_to_audio_body(video_id, video_url, sound_effect_prompt, bgm_prompt, asmr_mode, callback_url, external_task_id):
    if not video_id and not video_url:
        raise ValueError("视频生音效需要提供 video_id 或 video_url")
    body: dict = {}
    if video_id: body["video_id"] = video_id
    if video_url: body["video_url"] = video_url
    if sound_effect_prompt: body["sound_effect_prompt"] = sound_effect_prompt
    if bgm_prompt: body["bgm_prompt"] = bgm_prompt
    if asmr_mode: body["asmr_mode"] = True
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    return body


def _build_image_expansion_body(image_url, up_ratio, down_ratio, left_ratio, right_ratio, prompt, num_images, callback_url, external_task_id, watermark_enabled):
    if not image_url:
        raise ValueError("扩图需要提供 image_url")
    body = {"image": image_url, "up_expansion_ratio": up_ratio, "down_expansion_ratio": down_ratio,
            "left_expansion_ratio": left_ratio, "right_expansion_ratio": right_ratio}
    if prompt: body["prompt"] = prompt
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    if watermark_enabled is not None: body["watermark_info"] = {"enabled": watermark_enabled}
    body["n"] = max(1, int(num_images))
    return body


def _build_ai_multi_shot_body(image_url, callback_url, external_task_id):
    if not image_url:
        raise ValueError("智能补全主体图需要提供 image_url")
    body = {"element_frontal_image": image_url}
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
    return body


def _build_tts_body(text, voice_id, voice_language, voice_speed, callback_url, external_task_id):
    if not text or not voice_id or not voice_language:
        raise ValueError("TTS需要提供 text, voice_id, voice_language")
    body = {"text": text, "voice_id": voice_id, "voice_language": voice_language}
    if voice_speed is not None: body["voice_speed"] = voice_speed
    if callback_url: body["callback_url"] = callback_url
    if external_task_id: body["external_task_id"] = external_task_id
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


def _action_text2video(prompt: str, model_name: str = "kling-v3", duration: str = "5",
                       mode: str = "std", sound: str = "off", aspect_ratio: str = "16:9",
                       negative_prompt: str = "", cfg_scale: float = 0.5,
                       multi_shot: bool = False, shot_type: str = "",
                       multi_prompt: Optional[list] = None,
                       external_task_id: str = "", callback_url: str = "",
                       camera_control: Optional[dict] = None,
                       watermark_enabled: Optional[bool] = None,
                       element_list: Optional[list] = None,
                       voice_list: Optional[list] = None) -> dict:
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
    if element_list: body["element_list"] = element_list
    if voice_list: body["voice_list"] = voice_list
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
    p.add_argument("--model_name", default="kling-v3", help="模型名称")
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
