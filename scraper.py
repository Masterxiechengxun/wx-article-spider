"""
微信公众号文章抓取工具

原理：
  通过微信公众平台（mp.weixin.qq.com）的内部接口，
  可以查询任意公众号的全部文章列表，无需拥有目标账号。
  只需用自己的订阅号登录公众平台即可。

用法：
  python scraper.py fetch --account "公众号名称"   # 获取文章列表
  python scraper.py download                       # 下载全部文章
  python scraper.py download --count 200           # 只下载最新 N 篇
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime

import aiohttp
from playwright.async_api import async_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LIST_PATH = os.path.join(SCRIPT_DIR, "article_list.json")
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")
PROGRESS_PATH = os.path.join(SCRIPT_DIR, "download_progress.json")

MP_BASE = "https://mp.weixin.qq.com"
FETCH_DELAY = 1.5    # 获取列表请求间隔（秒）
DOWNLOAD_DELAY = 2   # 下载文章请求间隔（秒）


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else []


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sanitize_filename(name, max_len=80):
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', name)
    return name.strip('. ')[:max_len]


def print_separator(char="─", width=55):
    print(char * width)


# ──────────────────────────────────────────────
# 阶段1：通过微信公众平台获取文章列表
# ──────────────────────────────────────────────

async def fetch_article_list(account_name):
    """
    打开浏览器，用户扫码登录微信公众平台，
    自动搜索目标公众号并翻页获取全部文章列表。
    支持断点续传。
    """
    print_separator("=")
    print("微信公众号文章列表获取工具")
    print_separator("=")
    print(f"\n目标公众号：{account_name}\n")

    # 断点续传提示
    existing = load_json(LIST_PATH, [])
    if existing:
        print(f"检测到已有进度：{len(existing)} 篇文章")
        print("本次将从断点继续，不会重复获取。\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        # ── 步骤 1：扫码登录 ──
        print_separator()
        print("步骤 1/3  登录微信公众平台")
        print_separator()
        print()
        print("正在打开浏览器，请稍候...")
        await page.goto(f"{MP_BASE}/", wait_until="domcontentloaded")
        print()
        print("  ✦ 浏览器已打开，页面上显示了一个二维码")
        print("  ✦ 请用微信扫描该二维码")
        print("  ✦ 手机上弹出确认后，点击「登录」按钮")
        print("  ✦ 登录成功后，脚本将自动继续（无需手动操作）")
        print()
        print("等待扫码登录中...（最长等待 5 分钟）")

        try:
            await page.wait_for_url("**/cgi-bin/home**", timeout=300000)
        except Exception:
            print()
            print("✘ 登录等待超时（超过 5 分钟未扫码），请重新运行脚本")
            await browser.close()
            return

        await asyncio.sleep(1)

        # 提取 token
        token_match = re.search(r"token=(\d+)", page.url)
        if not token_match:
            print(f"✘ 登录成功但未获取到 token，请重新运行脚本")
            print(f"  当前 URL：{page.url}")
            await browser.close()
            return

        token = token_match.group(1)
        print()
        print(f"✔ 登录成功！")

        # 提取 cookie
        cookies = await context.cookies()
        cookie_str = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies
            if "weixin" in c.get("domain", "") or "qq.com" in c.get("domain", "")
        )

        headers = {
            "Cookie": cookie_str,
            "Referer": f"{MP_BASE}/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }

        # ── 步骤 2：搜索公众号 ──
        print()
        print_separator()
        print(f"步骤 2/3  搜索公众号「{account_name}」")
        print_separator()
        print()
        print("正在搜索，请稍候...")

        async with aiohttp.ClientSession(headers=headers) as session:
            resp = await session.get(
                f"{MP_BASE}/cgi-bin/searchbiz",
                params={
                    "action": "search_biz",
                    "token": token,
                    "lang": "zh_CN",
                    "f": "json",
                    "ajax": "1",
                    "query": account_name,
                    "count": "10",
                    "begin": "0",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            )
            search_data = await resp.json(content_type=None)

        accounts = search_data.get("list", [])
        if not accounts:
            print(f"✘ 未找到公众号「{account_name}」，请检查名称是否正确")
            await browser.close()
            return

        # 列出搜索结果，让用户确认
        print(f"搜索到 {len(accounts)} 个相关账号：")
        print()
        for i, acc in enumerate(accounts):
            marker = "▶" if acc.get("nickname") == account_name else " "
            print(f"  [{i}] {marker} {acc.get('nickname', '')}  |  {acc.get('alias', '（无英文ID）')}")
        print()

        # 优先精确匹配，否则提示用户选择
        exact = next((a for a in accounts if a.get("nickname") == account_name), None)
        if exact:
            target = exact
            print(f"✔ 自动匹配到：{target['nickname']}")
        else:
            print(f"未找到与「{account_name}」完全匹配的账号。")
            while True:
                choice = input("请输入序号选择目标账号（直接回车取消）：").strip()
                if choice == "":
                    print("已取消")
                    await browser.close()
                    return
                if choice.isdigit() and 0 <= int(choice) < len(accounts):
                    target = accounts[int(choice)]
                    break
                print(f"  请输入 0 ~ {len(accounts)-1} 之间的数字")

        fakeid = target["fakeid"]
        print(f"\n目标确认：{target['nickname']}（fakeid = {fakeid}）")

        # ── 步骤 3：翻页获取全部文章 ──
        print()
        print_separator()
        print(f"步骤 3/3  获取「{target['nickname']}」的全部文章列表")
        print_separator()
        print()

        all_articles = load_json(LIST_PATH, [])
        offset = len(all_articles)
        if offset > 0:
            print(f"从第 {offset + 1} 篇继续获取...\n")

        async with aiohttp.ClientSession(headers=headers) as session:
            while True:
                resp = await session.get(
                    f"{MP_BASE}/cgi-bin/appmsg",
                    params={
                        "action": "list_ex",
                        "token": token,
                        "lang": "zh_CN",
                        "f": "json",
                        "ajax": "1",
                        "begin": str(offset),
                        "count": "5",
                        "query": "",
                        "fakeid": fakeid,
                        "type": "9",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                )
                result = await resp.json(content_type=None)

                ret = result.get("base_resp", {}).get("ret", -1)
                if ret != 0:
                    err_msg = result.get("base_resp", {}).get("err_msg", "未知错误")
                    print()
                    if ret == 200013:
                        print("⚠ 触发频率限制（微信接口限流）")
                        print()
                        print("  已自动保存当前进度，请按以下步骤继续：")
                        print("  1. 等待 10 分钟")
                        print(f"  2. 重新运行：python scraper.py fetch --account \"{account_name}\"")
                        print("  3. 重新扫码登录，脚本会自动从断点继续")
                    elif ret in (-1, 200003):
                        print("⚠ 登录 session 已过期")
                        print()
                        print("  已自动保存当前进度，请按以下步骤继续：")
                        print(f"  1. 重新运行：python scraper.py fetch --account \"{account_name}\"")
                        print("  2. 重新扫码登录，脚本会自动从断点继续")
                    else:
                        print(f"✘ 接口返回错误：ret={ret}, msg={err_msg}")
                    break

                app_list = result.get("app_msg_list", [])
                if not app_list:
                    print("✔ 已获取全部文章")
                    break

                for item in app_list:
                    all_articles.append({
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "digest": item.get("digest", ""),
                        "cover": item.get("cover", ""),
                        "send_time": item.get("create_time", 0),
                    })

                offset += len(app_list)
                save_json(LIST_PATH, all_articles)
                print(f"  第 {offset - len(app_list) + 1} ~ {offset} 篇，累计 {len(all_articles)} 篇")
                await asyncio.sleep(FETCH_DELAY)

        await browser.close()

    total = len(load_json(LIST_PATH, []))
    print()
    print_separator("=")
    print(f"✔ 文章列表获取完成，共 {total} 篇")
    print(f"  保存位置：{LIST_PATH}")
    print()
    print("下一步，运行以下命令下载文章：")
    print(f"  python scraper.py download             # 下载全部")
    print(f"  python scraper.py download --count 200 # 只下载最新 200 篇")
    print_separator("=")


# ──────────────────────────────────────────────
# 阶段2：下载文章内容（Playwright 无头浏览器）
# ──────────────────────────────────────────────

async def download_articles(count=None):
    """
    用 Playwright 逐篇打开文章页面，提取渲染后的正文和图片，
    保存为干净的 HTML 文件。支持断点续传。
    """
    all_articles = load_json(LIST_PATH, [])
    if not all_articles:
        print("✘ 未找到文章列表，请先运行：")
        print('  python scraper.py fetch --account "公众号名称"')
        sys.exit(1)

    articles = all_articles[:count] if count else all_articles
    downloaded = set(load_json(PROGRESS_PATH, []))
    pending = [(i, a) for i, a in enumerate(articles)
               if a.get("url") and a["url"] not in downloaded]

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    total = len(articles)

    print_separator("=")
    print("文章下载")
    print_separator("=")
    print()
    print(f"  文章总数：{total} 篇")
    print(f"  已下载：  {len(downloaded)} 篇")
    print(f"  待下载：  {len(pending)} 篇")
    print(f"  保存目录：{ARTICLES_DIR}")
    print()

    if not pending:
        print("✔ 所有文章已下载完毕，无需重复下载")
        return

    print("开始下载...\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        # 屏蔽字体和视频以加快速度，保留图片
        await context.route("**/*.{mp4,mp3,woff,woff2,ttf}", lambda r: r.abort())
        page = await context.new_page()
        ok = fail = 0

        for idx, art in pending:
            url = art["url"]
            title = art.get("title", "无标题")
            send_time = art.get("send_time", 0)
            date_str = datetime.fromtimestamp(send_time).strftime("%Y%m%d") if send_time else "00000000"
            filepath = os.path.join(ARTICLES_DIR, f"{date_str}_{sanitize_filename(title)}.html")

            print(f"  [{ok + fail + 1}/{len(pending)}] {title[:48]}...")

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if resp and resp.status != 200:
                    print(f"    ✘ HTTP {resp.status}，跳过")
                    fail += 1
                    await asyncio.sleep(DOWNLOAD_DELAY)
                    continue

                try:
                    await page.wait_for_selector("#js_content", timeout=8000)
                except Exception:
                    pass

                # 将懒加载图片的 data-src 转为 src，确保图片正常显示
                await page.evaluate("""() => {
                    document.querySelectorAll('img[data-src]').forEach(
                        img => img.src = img.getAttribute('data-src')
                    );
                    document.querySelectorAll('img[data-lazy-src]').forEach(
                        img => img.src = img.getAttribute('data-lazy-src')
                    );
                }""")

                d = await page.evaluate("""() => {
                    const t  = document.getElementById('activity-name');
                    const a  = document.getElementById('js_name');
                    const pt = document.getElementById('publish_time');
                    const c  = document.getElementById('js_content');
                    return {
                        title:   t  ? t.innerText.trim()  : '',
                        author:  a  ? a.innerText.trim()  : '',
                        pubTime: pt ? pt.innerText.trim() : '',
                        html:    c  ? c.innerHTML         : '',
                    };
                }""")

                if not d["html"]:
                    print("    ✘ 正文为空（可能是视频或特殊格式），跳过")
                    fail += 1
                    await asyncio.sleep(DOWNLOAD_DELAY)
                    continue

                art_title = d["title"] or title
                clean_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{art_title}</title>
<style>
  body {{ max-width: 720px; margin: 40px auto; padding: 0 20px;
          font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
          line-height: 1.8; color: #333; }}
  h1 {{ font-size: 22px; line-height: 1.4; }}
  .meta {{ color: #999; font-size: 14px; margin-bottom: 24px; }}
  img {{ max-width: 100%; height: auto; display: block; margin: 8px 0; }}
</style>
</head>
<body>
<h1>{art_title}</h1>
<div class="meta">{d["author"]} | {d["pubTime"]}</div>
{d["html"]}
</body>
</html>"""

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(clean_html)

                downloaded.add(url)
                save_json(PROGRESS_PATH, list(downloaded))
                ok += 1

            except Exception as e:
                print(f"    ✘ 失败：{e}")
                fail += 1

            await asyncio.sleep(DOWNLOAD_DELAY)

        await browser.close()

    print()
    print_separator("=")
    print(f"✔ 下载完成！成功：{ok} 篇，跳过/失败：{fail} 篇")
    print(f"  文章保存在：{ARTICLES_DIR}")
    print_separator("=")


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="微信公众号文章抓取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python scraper.py fetch --account "测试大学"
  python scraper.py download
  python scraper.py download --count 200
        """,
    )
    subparsers = parser.add_subparsers(dest="command")

    p_fetch = subparsers.add_parser("fetch", help="获取文章列表（浏览器扫码登录）")
    p_fetch.add_argument("--account", required=True, metavar="公众号名称",
                         help="要抓取的微信公众号名称，例如：--account \"测试大学\"")

    p_dl = subparsers.add_parser("download", help="下载文章内容到本地")
    p_dl.add_argument("--count", type=int, default=None,
                      metavar="N", help="只下载最新 N 篇（默认全部）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "fetch":
        await fetch_article_list(args.account)
    elif args.command == "download":
        await download_articles(count=args.count)


if __name__ == "__main__":
    asyncio.run(main())
