# -*- coding: utf-8 -*-
"""工具库共享：错误时附带 --help 的辅助函数。"""

import io as _io
import argparse as _argparse


def _capture_help(parser: object) -> str:
    """捕获 argparse --help 输出为字符串。须传入 ArgumentParser；误传 Namespace 时返回说明文本，避免二次异常。"""
    if isinstance(parser, _argparse.ArgumentParser):
        buf = _io.StringIO()
        parser.print_help(file=buf)
        return buf.getvalue()
    return (
        "[工具内部] 无法展开 --help：_capture_help 需要 ArgumentParser，"
        f"当前为 {type(parser).__name__}。请在 main() 中先 parser = build_parser() 再 parse_args()，"
        "并在 except 中调用 _capture_help(parser)。\n"
    )


class _HelpFulParser(_argparse.ArgumentParser):
    """自定义 ArgumentParser，出错时不 exit 而是抛 ValueError，消息中附带 --help。"""

    def error(self, message: str):
        full_help = _capture_help(self)
        raise ValueError(f"参数错误: {message}\n\n用法:\n{full_help}")
