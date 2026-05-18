# -*- coding: utf-8 -*-
"""Skills 管理器：扫描 skills 目录、解析元数据、自动规范化、提供注册清单与全文读取。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class SkillMeta:
    """单个 Skill 的元数据。"""
    __slots__ = ("name", "description", "file_path", "is_auto_load")

    def __init__(self, name: str, description: str, file_path: Path, is_auto_load: bool = False) -> None:
        self.name = str(name or "").strip()
        self.description = str(description or "").strip()
        self.file_path = file_path
        self.is_auto_load = bool(is_auto_load)


class SkillManager:
    """会话级单例：启动时扫描 skills 目录，缓存元数据与全文。"""

    __slots__ = ("skills_dir", "max_file_size", "skills", "_content_cache")

    def __init__(self, skills_dir: Optional[Path], max_file_size: int = 200000) -> None:
        self.skills_dir = skills_dir
        self.max_file_size = int(max_file_size)
        self.skills: List[SkillMeta] = []
        self._content_cache: Dict[str, str] = {}
        if self.skills_dir and self.skills_dir.is_dir():
            self._scan()

    # ── 扫描 ──

    def _scan(self) -> None:
        """递归扫描 skills_dir 下所有 .md 文件。"""
        if not self.skills_dir or not self.skills_dir.is_dir():
            return
        seen: set[str] = set()
        auto_dir = self.skills_dir / "auto_load"

        # 先用相对路径做 key，避免不同子目录同名文件冲突
        def _rel_key(md: Path) -> str:
            try:
                return str(md.relative_to(self.skills_dir)).replace("\\", "/").lower()
            except ValueError:
                return md.name.lower()

        # auto_load 子目录（递归）
        if auto_dir.is_dir():
            for md_file in sorted(auto_dir.rglob("*.md")):
                if not md_file.is_file():
                    continue
                key = _rel_key(md_file)
                if key in seen:
                    continue
                seen.add(key)
                self._normalize_if_needed(md_file)
                name, desc = self._parse_meta(md_file)
                self.skills.append(SkillMeta(name, desc, md_file, is_auto_load=True))

        # 其余所有子目录（排除 auto_load 自身）
        skip_prefix = auto_dir.resolve() if auto_dir.is_dir() else None
        for md_file in sorted(self.skills_dir.rglob("*.md")):
            if not md_file.is_file():
                continue
            # 跳过 auto_load/ 下的文件（已在上一步处理）
            if skip_prefix and skip_prefix in md_file.resolve().parents:
                continue
            key = _rel_key(md_file)
            if key in seen:
                continue
            seen.add(key)
            self._normalize_if_needed(md_file)
            name, desc = self._parse_meta(md_file)
            self.skills.append(SkillMeta(name, desc, md_file, is_auto_load=False))

    # ── 元数据解析 ──

    def _parse_meta(self, file_path: Path) -> tuple[str, str]:
        """从文件前两行提取 __name__ 和 __description__。"""
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            return file_path.stem, ""
        lines = text.strip().splitlines()
        name = ""
        desc = ""
        for line in lines:
            line = line.strip()
            if not name and line.startswith("__name__:"):
                name = line[len("__name__:"):].strip()
            elif not desc and line.startswith("__description__:"):
                desc = line[len("__description__:"):].strip()
            if name and desc:
                break
        return name or file_path.stem, desc

    # ── 自动规范化 ──

    def _normalize_if_needed(self, file_path: Path) -> None:
        """若文件前两行不是 __name__ / __description__ 格式，自动分析内容并补充。"""
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            return
        lines = text.strip().splitlines()
        has_name = False
        has_desc = False
        for line in lines[:4]:
            stripped = line.strip()
            if stripped.startswith("__name__:"):
                has_name = True
            elif stripped.startswith("__description__:"):
                has_desc = True
        if has_name and has_desc:
            return
        if not text.strip():
            return
        # 自动生成
        name = self._auto_name(file_path, lines)
        desc = self._auto_description(lines)
        # 跳过已有 __name__ 或 __description__ 的行
        body_lines: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("__name__:") or stripped.startswith("__description__:"):
                continue
            body_lines.append(line)
        body = "\n".join(body_lines).strip()
        new_text = f"__name__: {name}\n__description__: {desc}\n\n{body}\n"
        try:
            file_path.write_text(new_text, encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _auto_name(file_path: Path, lines: List[str]) -> str:
        """从第一个 # 标题或文件名推断 name。"""
        for line in lines:
            stripped = line.strip()
            m = re.match(r"^#+\s*(.+)", stripped)
            if m:
                title = m.group(1).strip()
                # 排除可能是 __name__ 或 __description__ 的行
                if not title.startswith("__name__") and not title.startswith("__description__"):
                    return title[:120]
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
            if stripped.startswith("#") or stripped.startswith("__"):
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
        """列出所有 skill（供 LLM tool 调用）。"""
        return [
            {"name": s.name, "description": s.description, "auto_load": s.is_auto_load}
            for s in self.skills
        ]

    def get_skill(self, name: str) -> Optional[str]:
        """按 name 获取完整正文（供 tool read）。"""
        key = (name or "").strip()
        # 优先精确匹配
        for s in self.skills:
            if s.name == key:
                return self._read_content(s)
        # 模糊匹配（忽略大小写）
        key_lower = key.lower()
        for s in self.skills:
            if s.name.lower() == key_lower:
                return self._read_content(s)
        return None

    def _read_content(self, meta: SkillMeta) -> str:
        if meta.name in self._content_cache:
            return self._content_cache[meta.name]
        try:
            text = meta.file_path.read_text(encoding="utf-8")
        except Exception:
            return ""
        if len(text.encode("utf-8")) > self.max_file_size:
            text = text[:self.max_file_size // 2] + "\n\n...(文件过大，已截断)"
        # 去掉元数据头两行
        lines = text.strip().splitlines()
        body_lines: List[str] = []
        skipped = 0
        for line in lines:
            stripped = line.strip()
            if skipped < 2 and (stripped.startswith("__name__:") or stripped.startswith("__description__:")):
                skipped += 1
                continue
            body_lines.append(line)
        content = "\n".join(body_lines).strip()
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
