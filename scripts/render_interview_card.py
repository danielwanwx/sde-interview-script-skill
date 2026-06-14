#!/usr/bin/env python3
"""Render a senior SDE interview script card as Excalidraw + preview image.

The agent supplies already-summarized content as JSON. This script is purposely
dependency-light so Codex, Cursor, and Claude Code can all run it after clone.
It always writes a preview SVG and a .excalidraw file. If Node.js and network
access are available, it also creates an excalidraw.com share link.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


HANDWRITING_FONT = (
    "HanziPen SC,HanziPen TC,Kaiti SC,KaiTi,Bradley Hand,"
    "Comic Sans MS,cursive,sans-serif"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render an interview answer card from structured JSON content.",
    )
    parser.add_argument(
        "--content",
        required=True,
        help="Path to JSON content file, or '-' to read JSON from stdin.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory. Defaults to /tmp/sde-interview-card-<timestamp>.",
    )
    parser.add_argument("--slug", default="interview-card", help="Output filename slug.")
    parser.add_argument(
        "--no-share",
        action="store_true",
        help="Skip uploading to excalidraw.com.",
    )
    return parser.parse_args()


def load_content(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    required = ["title", "summary", "script", "short", "flows"]
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise SystemExit(f"Missing required content fields: {', '.join(missing)}")
    if not isinstance(data["flows"], list):
        raise SystemExit("content.flows must be a list")
    flows = [str(item) for item in data["flows"][:4]]
    while len(flows) < 4:
        flows.append("")
    data["flows"] = flows
    return data


def token_width(token: str, size: int) -> float:
    width = 0.0
    for char in token:
        code = ord(char)
        if char.isspace():
            width += size * 0.32
        elif 0x3400 <= code <= 0x9FFF:
            width += size * 0.98
        elif char in "`.,;:/|!()[]{}":
            width += size * 0.35
        else:
            width += size * 0.56
    return width


def tokenize(text: str) -> list[str]:
    return re.findall(
        r"`[^`]+`|[A-Za-z0-9_./?=&{}:#()+-]+|[\u3400-\u9fff]|\s+|[^\sA-Za-z0-9_./?=&{}:#()+\-\u3400-\u9fff`]",
        text,
    )


def wrap_paragraph(paragraph: str, size: int, max_width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    line_width = 0.0
    for token in tokenize(paragraph):
        token = " " if token.isspace() else token
        width = token_width(token, size)
        if line and line_width + width > max_width:
            lines.append(line.rstrip())
            line = token.lstrip()
            line_width = token_width(line, size)
        else:
            line += token
            line_width += width

        while line_width > max_width and len(line) > 1:
            acc = ""
            acc_width = 0.0
            rest_start = 0
            for idx, char in enumerate(line):
                char_width = token_width(char, size)
                if acc and acc_width + char_width > max_width:
                    rest_start = idx
                    break
                acc += char
                acc_width += char_width
            else:
                break
            lines.append(acc.rstrip())
            line = line[rest_start:].lstrip()
            line_width = token_width(line, size)
    if line.strip():
        lines.append(line.rstrip())
    return lines


def wrap_text(text: str, size: int, max_width: int) -> list[str | None]:
    wrapped: list[str | None] = []
    paragraphs = text.split("\n")
    for idx, paragraph in enumerate(paragraphs):
        if paragraph:
            wrapped.extend(wrap_paragraph(paragraph, size, max_width))
        else:
            wrapped.append(None)
        if idx != len(paragraphs) - 1:
            wrapped.append(None)
    return wrapped


def text_block_svg(
    text: str,
    size: int,
    max_width: int,
    color: str,
    line_gap: int,
    paragraph_gap: int,
) -> dict[str, Any]:
    lines = wrap_text(text, size, max_width)
    line_height = int(size * 1.26) + line_gap
    y = int(size * 1.1)
    text_nodes: list[str] = []
    max_line_width = 1.0
    for line in lines:
        if line is None:
            y += paragraph_gap
            continue
        max_line_width = max(max_line_width, token_width(line, size))
        text_nodes.append(
            f'<text x="0" y="{y}" font-size="{size}" fill="{color}" '
            f'font-family="{HANDWRITING_FONT}">{html.escape(line)}</text>'
        )
        y += line_height
    width = min(max_width + 8, int(math.ceil(max_line_width)) + 10)
    height = max(1, y - int(size * 0.3))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">' + "".join(text_nodes) + "</svg>"
    )
    data_url = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode(
        "ascii",
    )
    return {"dataURL": data_url, "width": width, "height": height, "svg": svg}


def element_id(rng: random.Random, prefix: str) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return f"{prefix}_" + "".join(rng.choice(chars) for _ in range(16))


def base_element(
    rng: random.Random,
    el_type: str,
    x: float,
    y: float,
    width: float,
    height: float,
    now: int,
) -> dict[str, Any]:
    return {
        "id": element_id(rng, el_type),
        "type": el_type,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "angle": 0,
        "strokeColor": "#1f2937",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 2,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": {"type": 3},
        "seed": rng.randint(1, 2**31 - 1),
        "version": 1,
        "versionNonce": rng.randint(1, 2**31 - 1),
        "isDeleted": False,
        "boundElements": None,
        "updated": now,
        "link": None,
        "locked": False,
    }


def rectangle(
    rng: random.Random,
    x: float,
    y: float,
    width: float,
    height: float,
    stroke: str,
    stroke_width: int,
    now: int,
) -> dict[str, Any]:
    element = base_element(rng, "rectangle", x, y, width, height, now)
    element.update(
        {
            "strokeColor": stroke,
            "backgroundColor": "transparent",
            "strokeWidth": stroke_width,
        },
    )
    return element


def image_element(
    rng: random.Random,
    key: str,
    block: dict[str, Any],
    x: float,
    y: float,
    now: int,
) -> tuple[dict[str, Any], str]:
    file_id = f"{key}_{element_id(rng, 'file')}"
    element = base_element(rng, "image", x, y, block["width"], block["height"], now)
    element.update(
        {
            "strokeColor": "transparent",
            "backgroundColor": "transparent",
            "roundness": None,
            "fileId": file_id,
            "scale": [1, 1],
            "status": "saved",
            "crop": None,
        },
    )
    return element, file_id


def arrow(
    rng: random.Random,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    now: int,
) -> dict[str, Any]:
    element = base_element(rng, "arrow", x1, y1, x2 - x1, y2 - y1, now)
    element.update(
        {
            "strokeColor": "#2563eb",
            "backgroundColor": "transparent",
            "roundness": None,
            "points": [[0, 0], [x2 - x1, y2 - y1]],
            "lastCommittedPoint": None,
            "startBinding": None,
            "endBinding": None,
            "startArrowhead": None,
            "endArrowhead": "arrow",
            "strokeWidth": 3,
        },
    )
    return element


def build_scene(content: dict[str, Any], slug: str) -> tuple[dict[str, Any], dict[str, Any], int]:
    rng = random.Random(20260614)
    now = int(time.time() * 1000)
    blocks: dict[str, Any] = {
        "title": text_block_svg(content["title"], 42, 1100, "#111827", 14, 22),
        "summary": text_block_svg(content["summary"], 28, 1450, "#374151", 14, 22),
        "script": text_block_svg(content["script"], 27, 1450, "#111827", 14, 24),
        "short": text_block_svg(content["short"], 26, 1450, "#111827", 13, 20),
    }
    for index, flow in enumerate(content["flows"]):
        blocks[f"flow{index}"] = text_block_svg(flow, 24, 310, "#1e3a8a", 12, 16)

    margin = 80
    card_width = 1600
    padding = 46
    script_y = 70
    content_x = margin + padding
    y_cursor = script_y + 34
    elements: list[dict[str, Any]] = []
    files: dict[str, Any] = {}
    content_items: list[tuple[str, dict[str, Any], str]] = []
    for key in ["title", "summary", "script"]:
        element, file_id = image_element(rng, key, blocks[key], content_x, y_cursor, now)
        content_items.append((key, element, file_id))
        y_cursor += element["height"] + (24 if key == "title" else 22)
    script_card_height = y_cursor - script_y + 24
    elements.append(rectangle(rng, margin, script_y, card_width, script_card_height, "#111827", 2, now))
    for key, element, file_id in content_items:
        elements.append(element)
        files[file_id] = {
            "id": file_id,
            "mimeType": "image/svg+xml",
            "dataURL": blocks[key]["dataURL"],
            "created": now,
        }

    flow_y = script_y + script_card_height + 74
    flow_width = 360
    flow_height = 250
    gap = (card_width - 4 * flow_width) / 3
    flow_xs = [margin + index * (flow_width + gap) for index in range(4)]
    for index, flow_x in enumerate(flow_xs):
        elements.append(rectangle(rng, flow_x, flow_y, flow_width, flow_height, "#2563eb", 3, now))
        key = f"flow{index}"
        element, file_id = image_element(rng, key, blocks[key], flow_x + 24, flow_y + 30, now)
        elements.append(element)
        files[file_id] = {
            "id": file_id,
            "mimeType": "image/svg+xml",
            "dataURL": blocks[key]["dataURL"],
            "created": now,
        }
        if index < 3:
            elements.append(
                arrow(
                    rng,
                    flow_x + flow_width + 14,
                    flow_y + flow_height / 2,
                    flow_xs[index + 1] - 18,
                    flow_y + flow_height / 2,
                    now,
                ),
            )

    short_y = flow_y + flow_height + 74
    short_element, short_file_id = image_element(
        rng,
        "short",
        blocks["short"],
        margin + padding,
        short_y + 38,
        now,
    )
    short_height = short_element["height"] + 76
    elements.append(rectangle(rng, margin, short_y, card_width, short_height, "#111827", 2, now))
    elements.append(short_element)
    files[short_file_id] = {
        "id": short_file_id,
        "mimeType": "image/svg+xml",
        "dataURL": blocks["short"]["dataURL"],
        "created": now,
    }

    canvas_height = int(short_y + short_height + 80)
    scene = {
        "type": "excalidraw",
        "version": 2,
        "source": "sde-interview-script-skill",
        "elements": elements,
        "appState": {
            "viewBackgroundColor": "#ffffff",
            "gridSize": None,
            "theme": "light",
            "name": slug,
            "scrollX": 0,
            "scrollY": 0,
            "zoom": {"value": 1},
        },
        "files": files,
    }
    return scene, blocks, canvas_height


def svg_image_tag(block: dict[str, Any], x: float, y: float) -> str:
    inner_svg = block["svg"].split(">", 1)[1].rsplit("</svg>", 1)[0]
    return (
        f'<g transform="translate({x:.0f} {y:.0f})">'
        f"{inner_svg}</g>"
    )


def render_preview_svg(scene: dict[str, Any], blocks: dict[str, Any], width: int, height: int) -> str:
    nodes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff" />',
    ]
    for element in scene["elements"]:
        if element["type"] == "rectangle":
            nodes.append(
                f'<rect x="{element["x"]:.0f}" y="{element["y"]:.0f}" '
                f'width="{element["width"]:.0f}" height="{element["height"]:.0f}" '
                f'rx="28" ry="28" fill="none" stroke="{element["strokeColor"]}" '
                f'stroke-width="{element["strokeWidth"]}" />'
            )
        elif element["type"] == "arrow":
            x1 = element["x"]
            y1 = element["y"]
            x2 = x1 + element["width"]
            y2 = y1 + element["height"]
            marker_id = f"arrow-{element['id']}"
            nodes.append(
                f'<defs><marker id="{marker_id}" markerWidth="10" markerHeight="10" '
                'refX="8" refY="3" orient="auto" markerUnits="strokeWidth">'
                f'<path d="M0,0 L0,6 L9,3 z" fill="{element["strokeColor"]}" />'
                "</marker></defs>"
            )
            nodes.append(
                f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                f'stroke="{element["strokeColor"]}" stroke-width="4" '
                f'marker-end="url(#{marker_id})" />'
            )
        elif element["type"] == "image":
            key = element["fileId"].split("_file_")[0]
            if key in blocks:
                nodes.append(svg_image_tag(blocks[key], element["x"], element["y"]))
    nodes.append("</svg>")
    return "".join(nodes)


def maybe_share(excalidraw_path: Path, skip: bool) -> dict[str, Any] | None:
    if skip or not shutil.which("node"):
        return None
    script_path = Path(__file__).with_name("share_excalidraw.mjs")
    try:
        completed = subprocess.run(
            ["node", str(script_path), "--input", str(excalidraw_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return json.loads(completed.stdout)
    except Exception as error:  # pragma: no cover - runtime/network fallback
        return {"error": str(error)}


def main() -> None:
    args = parse_args()
    content = load_content(args.content)
    timestamp = int(time.time())
    out_dir = Path(args.out or f"/tmp/sde-interview-card-{timestamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", args.slug).strip("-") or "interview-card"

    scene, blocks, canvas_height = build_scene(content, slug)
    excalidraw_path = out_dir / f"{slug}.excalidraw"
    preview_path = out_dir / f"{slug}-preview.svg"
    result_path = out_dir / f"{slug}-result.json"
    excalidraw_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    preview_svg = render_preview_svg(scene, blocks, 1760, canvas_height)
    preview_path.write_text(preview_svg, encoding="utf-8")
    share = maybe_share(excalidraw_path, args.no_share)
    result = {
        "preview": str(preview_path),
        "excalidraw": str(excalidraw_path),
        "link": share.get("url") if isinstance(share, dict) else None,
        "share": share,
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
