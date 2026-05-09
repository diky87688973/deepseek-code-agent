# -*- coding: utf-8 -*-
"""工具库共享：捕获 argparse --help；解析错误时附带完整用法（供模型纠错）。"""

from __future__ import annotations

import argparse as _argparse
import io as _io


def capture_help(parser: object) -> str:
    """捕获 argparse --help 文本。须传入 ArgumentParser。"""
    if isinstance(parser, _argparse.ArgumentParser):
        buf = _io.StringIO()
        parser.print_help(file=buf)
        return buf.getvalue()
    return (
        "[内部] 无法展开 --help：需要 ArgumentParser，"
        f"当前为 {type(parser).__name__}。请在模块内提供 build_parser() -> ArgumentParser。\n"
    )


class HelpfulParser(_argparse.ArgumentParser):
    """解析参数出错时抛 ValueError，消息中附带完整 --help。"""

    def error(self, message: str) -> None:
        full_help = capture_help(self)
        raise ValueError(f"参数错误: {message}\n\n用法:\n{full_help}")
