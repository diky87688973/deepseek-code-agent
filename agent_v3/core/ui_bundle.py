# -*- coding: utf-8
"""内联经典 UI 资源打包。"""
from __future__ import annotations

from agent_v3.core.deps import *  # noqa: F403

UI_HTML_FILE = AGENT_ROOT / "res" / "html" / "agent-ui.html"

RESET_CSS_FILE = AGENT_ROOT / "res" / "css" / "reset.css"

UI_CSS_FILE = AGENT_ROOT / "res" / "css" / "agent-ui.css"

UI_JS_FILE = AGENT_ROOT / "res" / "js" / "agent-ui.js"

THEME_UI_JS_FILE = AGENT_ROOT / "res" / "js" / "theme-ui.js"

HLJS_JS_FILE = AGENT_ROOT / "res" / "js" / "vendor" / "highlight.min.js"

CODE_HIGHLIGHT_JS_FILE = AGENT_ROOT / "res" / "js" / "code-highlight.js"

HLJS_CSS_DARK_FILE = AGENT_ROOT / "res" / "css" / "vendor" / "hljs-github-dark.min.css"

HLJS_CSS_LIGHT_FILE = AGENT_ROOT / "res" / "css" / "vendor" / "hljs-github.min.css"


def _scope_hljs_css(css: str, prefix: str) -> str:
    import re

    return re.sub(r"(?<![\w-])\.hljs", prefix + " .hljs", css)


_INLINE_CSS = (
    RESET_CSS_FILE.read_text(encoding="utf-8").rstrip()
    + "\n\n"
    + UI_CSS_FILE.read_text(encoding="utf-8")
    + "\n\n"
    + HLJS_CSS_DARK_FILE.read_text(encoding="utf-8")
    + "\n\n"
    + _scope_hljs_css(HLJS_CSS_LIGHT_FILE.read_text(encoding="utf-8"), 'html[data-ui-theme="light"]')
)

_INLINE_JS = (
    THEME_UI_JS_FILE.read_text(encoding="utf-8")
    + "\n;"
    + HLJS_JS_FILE.read_text(encoding="utf-8")
    + "\n;"
    + CODE_HIGHLIGHT_JS_FILE.read_text(encoding="utf-8")
    + "\n;"
    + UI_JS_FILE.read_text(encoding="utf-8")
)

TTS_JS_FILE = AGENT_ROOT / "res" / "js" / "agent-tts.js"

_INLINE_JS2 = TTS_JS_FILE.read_text(encoding="utf-8") if TTS_JS_FILE.is_file() else ""

_INLINE_HTML_TMPL = UI_HTML_FILE.read_text(encoding="utf-8")

INLINE_UI_HTML = (
    _INLINE_HTML_TMPL.replace("{{CSS}}", _INLINE_CSS)
    .replace("{{agent-ui.js}}", _INLINE_JS)
    .replace("{{agent-tts.js}}", _INLINE_JS2)
    .replace("{{APP_VERSION}}", AGENT_APP_VERSION)
)
