# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 获取文章列表（需要浏览器扫码登录微信公众平台）
python scraper.py fetch --account "公众号名称"

# 下载全部文章
python scraper.py download

# 只下载最新 N 篇
python scraper.py download --count 200

# 按文章类型分类（输出到 articles_classified/）
python classifier.py classify

# 按类型 + 部门分类（输出到 articles_by_dept/）
python classifier.py classify-dept
```

## Architecture

### scraper.py — 抓取工具

单文件工具 `scraper.py`，分两个阶段运行：

**阶段 1 — `fetch` 命令**（`fetch_article_list`）：
- 用 Playwright 有头浏览器打开微信公众平台（mp.weixin.qq.com），等待用户扫码登录
- 登录后从 URL 提取 `token`，从浏览器上下文提取 cookie
- 用 `aiohttp` 调用内部接口 `/cgi-bin/searchbiz` 搜索目标公众号，获取 `fakeid`
- 循环调用 `/cgi-bin/appmsg?action=list_ex` 翻页获取全部文章元数据，每页 5 条，间隔 `FETCH_DELAY`（1.5s）
- 结果追加写入 `article_list.json`（断点续传：重新运行时从已有条目数作为 offset 继续）
- 触发微信频率限制（ret=200013）时提示用户等待后重跑

**阶段 2 — `download` 命令**（`download_articles`）：
- 读取 `article_list.json`，对比 `download_progress.json` 过滤已下载 URL
- 用 Playwright 无头浏览器逐篇打开文章页，等待 `#js_content` 渲染完成
- JavaScript 注入将懒加载图片（`data-src` / `data-lazy-src`）替换为真实 `src`
- 提取标题、作者、发布时间、正文 HTML，拼装为干净的独立 HTML 文件
- 文件名格式：`{YYYYMMDD}_{sanitized_title}.html`，保存至 `articles/` 目录
- 每篇下载后将 URL 追加到 `download_progress.json`（断点续传）

### classifier.py — 分类工具

独立脚本 `classifier.py`，对已下载的文章进行分类整理：

**命令 1 — `classify`**（`classify_articles`）：
- 扫描 `articles/` 中的 HTML 文件，提取标题和正文纯文本
- 使用"标题优先"的关键词规则匹配，将文章归入 25 个类别（时政要闻、党建思政、学术科研、竞赛获奖、校园生活 等）
- 对标题匹配不到的文章，使用正文辅助匹配作为兜底
- 将文件拷贝到 `articles_classified/<类别>/` 目录下

**命令 2 — `classify-dept`**（`classify_by_department`）：
- 在类型分类基础上，从正文中提取"特别支持 | 部门名"署名
- 输出结构：`articles_by_dept/<部门>/<类别>/文件.html`
- 无部门署名的文章放入 `未标注部门/<类别>/` 下

**运行时生成的文件**（已被 .gitignore 排除）：
- `article_list.json` — 文章元数据列表
- `download_progress.json` — 已下载 URL 集合
- `articles/` — 下载的 HTML 文章
- `articles_classified/` — 按类型分类后的文章
- `articles_by_dept/` — 按类型 + 部门分类后的文章
