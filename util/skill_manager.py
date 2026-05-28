# -*- coding: utf-8 -*-
"""Skills 管理器：扫描 skills 目录、解析元数据、自动规范化、提供注册清单与全文读取。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class SkillMeta:
    """单个 Skill 的元数据。"""
    __slots__ = ("name", "description", "file_path", "is_auto_load")

    def __init__(self, name: str, description: str, file_path: Path, is_auto_load: bool = False) -> None:
        self.name = str(name or "").strip()
        self.description = str(description or "").strip()
        self.file_path = file_path
        self.is_auto_load = bool(is_auto_load)


class SkillManager:
    """会话级单例：扫描 skills 目录，缓存元数据与全文，动态检测文件变更。"""

    __slots__ = ("skills_dir", "max_file_size", "skills", "_content_cache", "_last_mtimes", "_side_usage", "pending_notifications")

    def __init__(self, skills_dir: Optional[Path], max_file_size: int = 200000) -> None:
        self.skills_dir = skills_dir
        self.max_file_size = int(max_file_size)
        self.skills: List[SkillMeta] = []
        self._content_cache: Dict[str, str] = {}
        self._last_mtimes: Dict[str, float] = {}
        self._side_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.pending_notifications: List[str] = []
        if self.skills_dir and self.skills_dir.is_dir():
            self._scan(full=True)

    # ── 隐藏目录过滤 ──

    @staticmethod
    def _should_skip(file_path: Path) -> bool:
        """检查文件路径是否在隐藏目录下（目录名以 . 开头），是则跳过。"""
        for part in file_path.parts:
            if part.startswith(".") and part != ".":
                return True
        return False

    # ── 动态检测 ──

    def _check_rescan(self) -> None:
        """检测 skills 目录是否有文件新增/变更，有则增量重新扫描。"""
        if not self.skills_dir or not self.skills_dir.is_dir():
            return
        changed = False
        current = {}
        for md_file in self.skills_dir.rglob("*.md"):
            if not md_file.is_file() or self._should_skip(md_file):
                continue
            try:
                key = str(md_file.resolve())
                mtime = md_file.stat().st_mtime
                current[key] = mtime
                if key not in self._last_mtimes or self._last_mtimes[key] != mtime:
                    changed = True
            except OSError:
                pass
        # 检查是否有文件被删除
        if set(self._last_mtimes.keys()) - set(current.keys()):
            changed = True
        if changed:
            self._last_mtimes = current
            self.skills.clear()
            self._content_cache.clear()
            self._scan(full=False)

    # ── 扫描 ──

    def _scan(self, full: bool = False) -> None:
        """递归扫描 skills_dir 下所有 .md 文件。
        full=True：启动扫描，不调 LLM，缺少 frontmatter 的文件仅控制台提示。
        full=False：运行时扫描，缺少 frontmatter 的文件走旁支 LLM 自动生成。
        """
        if not self.skills_dir or not self.skills_dir.is_dir():
            return
        seen: Set[str] = set()
        auto_dir = self.skills_dir / "auto_load"

        def _rel_key(md: Path) -> str:
            try:
                return str(md.relative_to(self.skills_dir)).replace("\\", "/").lower()
            except ValueError:
                return md.name.lower()

        def _collect_one(md_file: Path, is_auto_load: bool) -> None:
            nonlocal seen
            key = _rel_key(md_file)
            if key in seen:
                return
            seen.add(key)
            # name = 相对路径（不含 .md），保证唯一
            rel_name = str(md_file.relative_to(self.skills_dir)).replace("\\", "/")
            if rel_name.lower().endswith(".md"):
                rel_name = rel_name[:-3]
            if self._has_frontmatter(md_file):
                _, desc = self._parse_meta(md_file)
                name = rel_name
            elif full:
                name = rel_name
                desc = ""
                print(f"[skill_manager] ⚠️ {rel_name} 缺少 frontmatter，启动后可通过 skill_manage 自动生成", file=__import__("sys").stderr, flush=True)
            else:
                self._auto_generate_meta(md_file)
                _, desc = self._parse_meta(md_file)
                name = rel_name
            self.skills.append(SkillMeta(name, desc, md_file, is_auto_load=is_auto_load))

        # auto_load 子目录
        if auto_dir.is_dir():
            for md_file in sorted(auto_dir.rglob("*.md")):
                if md_file.is_file() and not self._should_skip(md_file):
                    _collect_one(md_file, is_auto_load=True)

        # 其余子目录
        skip_prefix = auto_dir.resolve() if auto_dir.is_dir() else None
        for md_file in sorted(self.skills_dir.rglob("*.md")):
            if not md_file.is_file() or self._should_skip(md_file):
                continue
            if skip_prefix and skip_prefix in md_file.resolve().parents:
                continue
            _collect_one(md_file, is_auto_load=False)

    # ── 旁支 LLM 生成元数据 ──

    def _has_frontmatter(self, file_path: Path) -> bool:
        """检查文件是否有 frontmatter（--- 开头）。"""
        try:
            text = file_path.read_text(encoding="utf-8")
            return text.strip().startswith("---")
        except Exception:
            return False

    def _auto_generate_meta(self, file_path: Path) -> None:
        """对没有 frontmatter 的文件，通过旁支 LLM 生成 description 并写入，记录 token 消耗与通知。"""
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            return
        if not text.strip():
            return
        rel_path = file_path.relative_to(self.skills_dir) if self.skills_dir else file_path.name
        self.pending_notifications.append(f"📝 正在为 {rel_path} 自动生成元数据...")
        preview = text.strip()[:2000]
        name = file_path.stem
        desc = ""
        try:
            from util.agent_openai_compatible_client import chat_completion_request
            from util.agent_model_dispatch import default_model_from_env
            model = default_model_from_env()
            prompt = (
                "你是一个技能描述生成器。分析以下 Markdown 文件内容，用一句话概括该技能的功能（≤60字）。\n"
                "只输出 JSON：{\"description\": \"...\"}\n\n"
                f"---\n{preview}\n---"
            )
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 200,
            }
            data = chat_completion_request(payload)
            usage = data.get("usage") or {}
            if usage:
                self._side_usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
                self._side_usage["completion_tokens"] += int(usage.get("completion_tokens", 0))
                self._side_usage["total_tokens"] += int(usage.get("total_tokens", 0))
            choices = data.get("choices") or []
            msg = (choices[0] or {}).get("message") or {}
            content = (msg.get("content") or "").strip()
            import json
            import re
            m = re.search(r"\{[^{}]+\}", content)
            if m:
                parsed = json.loads(m.group(0))
                desc = str(parsed.get("description") or "").strip()
        except Exception:
            desc = ""
        today = "2026-05-19"
        new_text = (
            "---\n"
            f"name: {name}\n"
            f"description: {desc}\n"
            f"updated: {today}\n"
            "version: 1.0\n"
            "---\n"
            f"\n{text.strip()}\n"
        )
        try:
            file_path.write_text(new_text, encoding="utf-8")
            self.pending_notifications.append(f"✅ {rel_path} 元数据已自动生成（name: {name}）")
        except Exception as exc:
            self.pending_notifications.append(f"⚠️ {rel_path} 元数据写入失败: {exc}")

    # ── 元数据解析 ──

    @staticmethod
    def _strip_frontmatter(text: str) -> Tuple[dict, str]:
        """解析文件正文，提取 --- 分隔的 YAML frontmatter。"""
        meta: dict = {}
        lines = text.strip().splitlines()
        if not lines:
            return meta, text
        if not lines[0].strip().startswith("---"):
            return meta, text
        end = -1
        for i in range(1, len(lines)):
            if lines[i].strip().startswith("---"):
                end = i
                break
        if end < 1:
            return meta, text
        for line in lines[1:end]:
            s = line.strip()
            if ":" in s:
                key, _, val = s.partition(":")
                meta[key.strip().lower()] = val.strip()
        body = "\n".join(lines[end+1:]).strip()
        return meta, body

    def _parse_meta(self, file_path: Path) -> Tuple[str, str]:
        """从 YAML frontmatter 提取 name 和 description。"""
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            return file_path.stem, ""
        meta, _ = self._strip_frontmatter(text)
        name = str(meta.get("name") or "").strip() or file_path.stem
        desc = str(meta.get("description") or "").strip()
        return name, desc

    # ── 自动规范化：无 frontmatter 的文件自动补充 ──

    def _normalize_if_needed(self, file_path: Path) -> None:
        """若文件没有 frontmatter，自动分析内容并补充。纯本地规则，不走 LLM。"""
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            return
        meta, body = self._strip_frontmatter(text)
        if meta.get("name") or meta.get("description"):
            return
        if not text.strip():
            return

        lines = text.strip().splitlines()
        name = self._auto_name(file_path, lines)
        desc = self._auto_description(lines)

        today = "2026-05-19"
        new_text = (
            "---\n"
            f"name: {name}\n"
            f"description: {desc}\n"
            f"updated: {today}\n"
            "version: 1.0\n"
            "---\n"
            f"\n{body}\n"
        )
        try:
            file_path.write_text(new_text, encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _auto_name(file_path: Path, lines: List[str]) -> str:
        """用文件名（不含后缀）作为 name。"""
        return file_path.stem

    @staticmethod
    def _auto_description(lines: List[str]) -> str:
        """从第一个非空非标题非元数据段落提取描述。"""
        collected: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if collected:
                    break
                continue
            if stripped.startswith("#") or stripped.startswith("name:") or stripped.startswith("description:") or stripped.startswith("---"):
                if collected:
                    break
                continue
            collected.append(stripped)
            if len(" ".join(collected)) > 120:
                break
        desc = " ".join(collected).strip()
        return desc[:200] if desc else ""

    # ── 公开 API ──

    def list_skills(self) -> List[Dict[str, Any]]:
        """列出所有普通 skill（auto_load 常驻技能不在此列出，它们已自动注入上下文）。"""
        self._check_rescan()
        return [
            {"name": s.name, "description": s.description, "auto_load": s.is_auto_load}
            for s in self.skills if not s.is_auto_load
        ]

    def get_skill(self, name: str) -> Optional[str]:
        """按 name 获取完整正文。name 必须与相对路径完全一致。
        传入纯文件名(stem)时只匹配父目录根下的文件，不匹配子目录。
        """
        self._check_rescan()
        key = (name or "").strip()
        if not key:
            return None
        # 精确匹配（相对路径）
        for s in self.skills:
            if s.name == key:
                return self._read_content(s)
        # 忽略大小写匹配
        key_lower = key.lower()
        for s in self.skills:
            if s.name.lower() == key_lower:
                return self._read_content(s)
        # 纯文件名(stem)匹配：只匹配父目录根下的文件
        if "/" not in key and "\\" not in key:
            stem_matches = [
                s for s in self.skills
                if s.file_path.stem.lower() == key_lower
                and s.file_path.parent == self.skills_dir
            ]
            if len(stem_matches) == 1:
                return self._read_content(stem_matches[0])
        return None

    def list_skill_names(self) -> List[str]:
        """返回所有 skill name 列表（供错误提示使用）。"""
        return [s.name for s in self.skills]

    def _read_content(self, meta: SkillMeta) -> str:
        if meta.name in self._content_cache:
            return self._content_cache[meta.name]
        try:
            text = meta.file_path.read_text(encoding="utf-8")
        except Exception:
            return ""
        if len(text.encode("utf-8")) > self.max_file_size:
            text = text[:self.max_file_size // 2] + "\n\n...(文件过大，已截断)"
        # 剥离 frontmatter（新旧格式皆可）
        _, body = self._strip_frontmatter(text)
        content = body.strip()
        self._content_cache[meta.name] = content
        return content

    def get_auto_load_skills(self) -> List[Dict[str, str]]:
        """返回 auto_load skill 的 {name, content} 列表（供上下文注入）。"""
        result: List[Dict[str, str]] = []
        for s in self.skills:
            if s.is_auto_load:
                content = self._read_content(s)
                if content.strip():
                    result.append({"name": s.name, "content": content})
        return result

    def build_registry_message(self) -> str:
        """构建 Skills 注册清单 system 消息（插入前缀）。"""
        if not self.skills:
            return ""
        lines = ["【可用技能清单】"]
        for s in self.skills:
            tag = "[Auto Load]" if s.is_auto_load else ""
            lines.append(f"- {s.name} {tag}: {s.description}")
        return "\n".join(lines)

    def build_auto_load_messages(self) -> List[str]:
        """构建 auto_load skill 的 system 消息列表。"""
        result: List[str] = []
        for s in self.skills:
            if s.is_auto_load:
                content = self._read_content(s)
                if content.strip():
                    result.append(f"【技能：{s.name}】(Auto Load)\n{content}")
        return result

    @property
    def registry_count(self) -> int:
        """可用 skill 总数（含 auto_load）。"""
        return len(self.skills)

    @property
    def auto_load_count(self) -> int:
        """auto_load skill 数量。"""
        return sum(1 for s in self.skills if s.is_auto_load)


# ── 进程内单例（由 bootstrap 初始化）──
_skill_manager: Optional[SkillManager] = None


def init_skill_manager(skills_dir: Optional[Path], max_file_size: int = 200000) -> SkillManager:
    """启动时调用：初始化并返回全局 SkillManager 实例。"""
    global _skill_manager
    _skill_manager = SkillManager(skills_dir, max_file_size)
    return _skill_manager


def get_skill_manager() -> SkillManager:
    """获取全局 SkillManager 实例（未初始化则返回空实例）。"""
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager(None)
    return _skill_manager
