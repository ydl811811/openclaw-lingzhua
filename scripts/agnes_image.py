#!/usr/bin/env python3
"""
agnes_image.py — Agnes AI 图像生成 wrapper (2026-08-07)
支持 agnes-image-2.0-flash (图生图/多图合成) + agnes-image-2.1-flash (文生图)

用法:
    python3 agnes_image.py "prompt"                          # 文生图 (1024x1024)
    python3 agnes_image.py "prompt" --size 1024x1792         # 自定义尺寸
    python3 agnes_image.py "prompt" --model 2.1              # 显式选模型
    python3 agnes_image.py "prompt" --image url1 --image url2  # 图生图/多图
    python3 agnes_image.py "prompt" --out /tmp/foo.png       # 保存到本地

官方文档: https://agnes-ai.com/zh-Hans/docs/agnes-image-20-flash
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# 从 openclaw.json 读 agnes API key
OPENCLAW_JSON = Path("/home/YDL/.openclaw/openclaw.json")
BASE_URL = "https://apihub.agnes-ai.com/v1/images/generations"

MODEL_ALIAS = {
    "2.0": "agnes-image-2.0-flash",
    "2.1": "agnes-image-2.1-flash",
    "20":  "agnes-image-2.0-flash",
    "21":  "agnes-image-2.1-flash",
}


def load_api_key() -> str:
    """从 openclaw.json 读 agnes apiKey"""
    if env_key := os.environ.get("AGNES_API_KEY"):
        return env_key
    with OPENCLAW_JSON.open() as f:
        cfg = json.load(f)
    key = cfg["models"]["providers"]["agnes"]["apiKey"]
    if not key or len(key) < 20:
        sys.exit("❌ openclaw.json 里 agnes apiKey 异常")
    return key


def build_payload(args, model: str) -> dict:
    """根据官方文档构造请求体
    - 顶层: model, prompt, size, n
    - extra_body: image (图生图输入), tags: ["img2img"], response_format
    """
    payload = {
        "model": model,
        "prompt": args.prompt,
        "size": args.size,
        "n": args.n,
    }
    extra = {}
    if args.image:
        extra["image"] = args.image if len(args.image) > 1 else args.image[0]
    if args.image and len(args.image) >= 1:
        # 图生图必须带 tags
        extra["tags"] = ["img2img"]
    if args.format:
        extra["response_format"] = args.format
    elif not args.out:
        # 默认输出 url（不写文件时 url 方便）
        extra["response_format"] = "url"
    if extra:
        payload["extra_body"] = extra
    return payload


def call_agnes(api_key: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        BASE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.exit(f"❌ HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        sys.exit(f"❌ 网络错误: {e.reason}")


def save_outputs(data: dict, out_path: Path | None, n: int) -> list[str]:
    """保存图片到本地，返回路径列表"""
    paths = []
    for i, item in enumerate(data.get("data", [])):
        if b64 := item.get("b64_json"):
            img_bytes = base64.b64decode(b64)
            target = out_path if (out_path and n == 1) else Path(f"/tmp/agnes_{i}.png")
            target.write_bytes(img_bytes)
            paths.append(str(target))
        elif url := item.get("url"):
            if out_path and n == 1:
                target = out_path
            else:
                ext = "png"
                target = Path(f"/tmp/agnes_{i}.{ext}")
            try:
                urllib.request.urlretrieve(url, str(target))
                paths.append(str(target))
            except Exception as e:
                paths.append(f"URL(下载失败 {e}): {url}")
                print(f"⚠️ 下载失败，提供 URL: {url}")
    return paths


def main():
    ap = argparse.ArgumentParser(description="Agnes AI 图像生成 wrapper")
    ap.add_argument("prompt", help="提示词")
    ap.add_argument("--model", default="2.1", choices=list(MODEL_ALIAS.keys()),
                    help="模型 (2.0=图生图, 2.1=文生图, 默认 2.1)")
    ap.add_argument("--size", default="1024x1024",
                    help="尺寸 (默认 1024x1024，可选 1024x1792/1792x1024 等)")
    ap.add_argument("--n", type=int, default=1, help="生成数量 (默认 1)")
    ap.add_argument("--image", action="append", default=[],
                    help="图生图输入图 URL，可多次传 (--image url1 --image url2)")
    ap.add_argument("--format", choices=["url", "b64_json"],
                    help="响应格式 (url=URL, b64_json=base64)")
    ap.add_argument("--out", help="保存路径 (单图时生效，多图保存到 /tmp/agnes_N.png)")
    ap.add_argument("--raw", action="store_true", help="打印原始 JSON")
    args = ap.parse_args()

    model = MODEL_ALIAS[args.model]
    api_key = load_api_key()
    payload = build_payload(args, model)

    print(f"🚀 调用 agnes {model} (size={args.size}, n={args.n})")
    if args.image:
        print(f"   图生图输入: {args.image}")

    data = call_agnes(api_key, payload)

    if args.raw:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    paths = save_outputs(data, Path(args.out) if args.out else None, args.n)
    print(f"✅ 生成 {len(paths)} 张图:")
    for p in paths:
        print(f"   📁 {p}")


if __name__ == "__main__":
    main()