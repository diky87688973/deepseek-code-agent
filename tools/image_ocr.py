# -*- coding: utf-8 -*-
"""图片 OCR：pytesseract / easyocr。扁平参数；路径经 agent_common。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Tuple

import agent_common as ac

try:
    import pytesseract

    _HAS_PYTESSERACT = True
except ImportError:
    pytesseract = None
    _HAS_PYTESSERACT = False

try:
    import easyocr

    _HAS_EASYOCR = True
except ImportError:
    easyocr = None
    _HAS_EASYOCR = False

try:
    from PIL import Image

    _HAS_PIL = True
except ImportError:
    Image = None
    _HAS_PIL = False


def _detect_engine(prefer: Optional[str]) -> str:
    if prefer == "pytesseract" and _HAS_PYTESSERACT:
        return "pytesseract"
    if prefer == "easyocr":
        try:
            import easyocr
            return "easyocr"
        except ImportError:
            pass
    if _HAS_PYTESSERACT:
        return "pytesseract"
    try:
        import easyocr
        return "easyocr"
    except ImportError:
        pass
    return "none"


def _ocr_pytesseract(image_path: Path, lang: str, region: Optional[Tuple[int, int, int, int]]) -> dict:
    if not _HAS_PIL:
        return {"ok": False, "error": "Pillow 未安装"}
    assert Image is not None
    img = Image.open(image_path)
    if region:
        x, y, w, h = region
        img = img.crop((x, y, x + w, y + h))

    import pytesseract as pt

    try:
        data = pt.image_to_data(img, lang=lang, output_type=pt.Output.DICT)
    except Exception as e:
        return {"ok": False, "error": f"Tesseract 识别失败: {e}"}

    text_lines = []
    words_with_pos = []
    for i in range(len(data["text"])):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        words_with_pos.append(
            {
                "text": txt,
                "x": data["left"][i],
                "y": data["top"][i],
                "w": data["width"][i],
                "h": data["height"][i],
                "confidence": data["conf"][i],
            }
        )
    line_map: dict = {}
    for w in words_with_pos:
        row = w["y"] // 5
        line_map.setdefault(row, []).append(w)
    for row in sorted(line_map):
        line_map[row].sort(key=lambda x: x["x"])
        txt = " ".join(w["text"] for w in line_map[row])
        if txt.strip():
            text_lines.append({"text": txt.strip(), "words": line_map[row]})

    full_text = "\n".join(t["text"] for t in text_lines)
    return {
        "ok": True,
        "data": {
            "engine": "pytesseract",
            "full_text": full_text,
            "lines": text_lines,
            "words": words_with_pos,
        },
    }


def _ocr_easyocr(image_path: Path, lang: str, region: Optional[Tuple[int, int, int, int]]) -> dict:
    import easyocr as _eo

    _lang_map = {"chi_sim": "ch_sim", "chi_tra": "ch_tra", "eng": "en", "en": "en"}
    lang_list = []
    for p in lang.replace("+", ",").split(","):
        p = p.strip()
        if p:
            lang_list.append(_lang_map.get(p, p))
    if not lang_list:
        lang_list = ["ch_sim", "en"]
    try:
        reader = _eo.Reader(lang_list, gpu=False)
    except Exception as e:
        return {"ok": False, "error": f"easyocr reader 初始化失败: {e}"}

    try:
        results = reader.readtext(str(image_path))
    except Exception as e:
        return {"ok": False, "error": f"easyocr 识别失败: {e}"}

    if region:
        rx, ry, rw, rh = region
        filtered = []
        for bbox, txt, conf in results:
            cx = (bbox[0][0] + bbox[2][0]) / 2
            cy = (bbox[0][1] + bbox[2][1]) / 2
            if rx <= cx <= rx + rw and ry <= cy <= ry + rh:
                filtered.append((bbox, txt, conf))
        results = filtered

    words = []
    for bbox, txt, conf in results:
        x0 = int(min(p[0] for p in bbox))
        y0 = int(min(p[1] for p in bbox))
        x1 = int(max(p[0] for p in bbox))
        y1 = int(max(p[1] for p in bbox))
        words.append(
            {
                "text": txt,
                "x": x0,
                "y": y0,
                "w": x1 - x0,
                "h": y1 - y0,
                "confidence": round(conf, 3),
            }
        )

    full_text = "\n".join(w["text"] for w in words)
    return {"ok": True, "data": {"engine": "easyocr", "full_text": full_text, "words": words}}


def agent_main(
    *,
    path: str = "",
    lang: str = "chi_sim+eng",
    region: Optional[str] = None,
    engine: str = "auto",
    **_kwargs: object,
) -> dict:
    if _kwargs.get("source"):
        return ac.err(ValueError("image_ocr 使用 path 指定图片，勿传已废弃的 source 参数"))
    if not str(path or "").strip():
        return ac.err(ValueError("缺少 path（图片文件路径）"))
    try:
        src = ac.resolve_path(str(path).strip(), allow_outside_workspace=True)
        if not src.is_file():
            return ac.err(FileNotFoundError(f"图片不存在: {src}"))

        region_tuple = None
        if region:
            parts = [int(x.strip()) for x in str(region).split(",")]
            if len(parts) != 4:
                return ac.err(ValueError("region 须为 x,y,w,h 四个整数"))
            region_tuple = tuple(parts)  # type: ignore[assignment]

        preferred = None if engine == "auto" else engine
        active = _detect_engine(preferred)
        if active == "none":
            return {
                "ok": False,
                "data": None,
                "error": {
                    "type": "MissingDependency",
                    "message": "未检测到 OCR 引擎，请安装 pytesseract（含 Tesseract 可执行文件）或 easyocr",
                    "dependencies": {
                        "install_command": "pip install pytesseract Pillow  # 或 pip install easyocr",
                        "estimated_time": "easyocr 首次安装约 2–5 分钟（含模型）",
                        "security_notes": [
                            "easyocr 依赖链可能在编译时触发杀软误报，需按需加白名单"
                        ],
                    },
                },
            }

        if active == "pytesseract":
            if not _HAS_PIL:
                return {
                    "ok": False,
                    "data": None,
                    "error": {
                        "type": "MissingDependency",
                        "message": "pytesseract 需要 Pillow：pip install Pillow",
                        "dependencies": {"install_command": "pip install Pillow"},
                    },
                }
            result = _ocr_pytesseract(src, lang, region_tuple)
        elif active == "easyocr":
            result = _ocr_easyocr(src, lang, region_tuple)
        else:
            return ac.err(RuntimeError(f"未知引擎: {active}"))

        if not result.get("ok"):
            return {
                "ok": False,
                "data": None,
                "error": {"type": "OCRError", "message": str(result.get("error", "识别失败"))},
            }
        return ac.ok(result["data"])
    except Exception as e:
        return ac.err(e)


def main() -> None:
    p = argparse.ArgumentParser(description="image_ocr")
    p.add_argument("--path", required=True, help="图片文件路径")
    p.add_argument("--lang", default="chi_sim+eng")
    p.add_argument("--region", default=None)
    p.add_argument("--engine", default="auto")
    p.add_argument("--json_out", action="store_true")
    args = p.parse_args()
    r = agent_main(
        path=args.path,
        lang=args.lang,
        region=args.region,
        engine=args.engine,
    )
    if args.json_out:
        print(json.dumps(r, ensure_ascii=False))
    else:
        if r.get("ok"):
            print(json.dumps(r.get("data"), ensure_ascii=False, indent=2))
        else:
            print((r.get("error") or {}).get("message", ""), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
