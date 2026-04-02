#!/usr/bin/env python3
"""
微信文章 HTML 清洗工具

将 articles/ 中的微信公众号 HTML 文章清洗后输出到 articles_clean/：
  - 删除文章末尾的"小微推荐▼"推荐区块
  - 删除微信/135编辑器私有的 data-* 属性及其他非标准属性
  - 删除微信页面 JS 注入的残留样式（固定像素宽、visibility、aspect-ratio）
  - 删除微信专属 class（在独立 HTML 中无对应 CSS）
  - 删除 CSS 隐藏的不可见元素；空行占位元素转为下一兄弟的 margin-top
  - 精简 style 属性中不影响视觉的冗余声明

用法：
  python cleaner.py                           # 批量处理全部文章
  python cleaner.py --file articles/xxx.html  # 处理单篇
"""

import argparse
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

ARTICLES_DIR = Path("articles")
OUTPUT_DIR = Path("articles_clean")

# 要删除的非标准/私有属性（精确匹配）
REMOVE_ATTRS = {
    # 微信图片元数据
    "data-backh", "data-backw", "data-ratio", "data-w",
    "data-src", "data-original-style", "data-index", "data-order",
    "data-imgfileid", "data-galleryid", "data-s", "data-type",
    # 135 编辑器
    "data-tools", "data-id", "data-color", "data-autoskip",
    "data-role", "data-itemshowtype", "data-linktype",
    # 链接相关非标准属性
    "textvalue", "linktype", "imgurl", "imgdata", "tab", "hasload",
    # 其他非标准属性
    "_width", "label", "draggable", "xml:space",
}

# 微信专属 class（在独立 HTML 中无对应 CSS，删除不影响渲染）
WECHAT_CLASSES = {
    "rich_pages", "wxw-img", "js_img_placeholder", "wx_img_placeholder",
    "__bg_gif", "js_jump_icon", "h5_image_link", "js_insertlocalimg",
}

# style 中要整条删除的声明
_STYLE_REMOVE = re.compile(
    r"(?:"
    r"-webkit-tap-highlight-color\s*:[^;]+"           # 移动端点击高亮
    r"|outline\s*:\s*0(?:px)?[^;]*"                   # outline: 0
    r"|caret-color\s*:[^;]+"                           # 光标颜色
    r"|visibility\s*:\s*visible(?:\s*!important)?"       # JS 懒加载残留（含无 !important 版本）
    r"|aspect-ratio\s*:\s*calc\([^)]+\)\s*/\s*1"      # JS 预占位防抖动
    r")\s*;?\s*",
    re.IGNORECASE,
)

# style 中 width: Npx !important → width: 100%
_WIDTH_PX = re.compile(r"width\s*:\s*\d+px\s*!important", re.IGNORECASE)

# 有标准 transform 时才删除的厂商前缀版本
_VENDOR_TRANSFORM = re.compile(
    r"(?:-webkit-|-moz-|-o-)transform\s*:[^;]+;?\s*", re.IGNORECASE
)


def clean_style(style: str) -> str:
    """精简 style 属性值，删除/替换无关声明。"""
    style = _STYLE_REMOVE.sub("", style)
    style = _WIDTH_PX.sub("width: 100%", style)

    # 只有当标准 transform 已存在时，才删除厂商前缀版本
    if re.search(r"(?<![a-z-])transform\s*:", style, re.IGNORECASE):
        style = _VENDOR_TRANSFORM.sub("", style)

    # 清理多余分号和空白
    style = re.sub(r";\s*;+", ";", style)
    style = style.strip().strip(";").strip()
    return style


def clean_tag(tag: Tag) -> None:
    """删除标签上的无用属性、精简 style、过滤微信专属 class。"""
    for attr in list(tag.attrs.keys()):
        if attr in REMOVE_ATTRS or attr.startswith("data-"):
            del tag[attr]

    # 过滤微信专属 class
    if tag.get("class"):
        kept = [c for c in tag["class"] if c not in WECHAT_CLASSES]
        if kept:
            tag["class"] = kept
        else:
            del tag["class"]

    if tag.get("style"):
        cleaned = clean_style(tag["style"])
        if cleaned:
            tag["style"] = cleaned
        else:
            del tag["style"]


def _is_spacer(tag: Tag) -> bool:
    """判断是否为纯空行占位元素（只含 <br> 或空文本的 p/section）。"""
    if tag.name not in ("p", "section"):
        return False
    style = tag.get("style", "")
    # height:1px + overflow:hidden 是 135 编辑器常见占位写法
    if re.search(r"height\s*:\s*1px", style, re.IGNORECASE) and \
       re.search(r"overflow\s*:\s*hidden", style, re.IGNORECASE):
        return True
    # 仅含 <br> 或纯空白文本
    children = [c for c in tag.children
                if not (isinstance(c, NavigableString) and not c.strip())]
    if not children:
        return True
    if len(children) == 1 and getattr(children[0], "name", None) == "br":
        return True
    return False


def _add_margin_top(tag: Tag) -> None:
    """在标签 style 上追加 margin-top: 1em（若已有则跳过）。"""
    style = tag.get("style", "")
    if re.search(r"margin-top\s*:", style, re.IGNORECASE):
        return
    tag["style"] = (style.rstrip(";") + "; margin-top: 1em").lstrip("; ")


def remove_hidden_elements(soup: BeautifulSoup) -> None:
    """删除 CSS 隐藏的不可见元素，空行占位转为下一兄弟的 margin-top。"""
    # A. 完全不可见：display:none 或 visibility:hidden
    for tag in soup.find_all(True):
        if tag.parent is None:  # 已被 decompose 的节点跳过
            continue
        style = tag.get("style", "")
        if re.search(r"display\s*:\s*none", style, re.IGNORECASE) or \
           re.search(r"visibility\s*:\s*hidden", style, re.IGNORECASE):
            tag.decompose()

    # B. 纯空行占位：删除并给下一兄弟补 margin-top
    for tag in soup.find_all(True):
        if tag.parent is None:  # 已被 decompose 的节点跳过
            continue
        if _is_spacer(tag):
            next_sib = tag.find_next_sibling()
            if next_sib and isinstance(next_sib, Tag):
                _add_margin_top(next_sib)
            tag.decompose()


def _find_cut_node(start: Tag | None) -> Tag | None:
    """从起始节点向上查找正文与尾部推荐区的分界节点。"""
    node = start
    while node and node.name and node.name != "body":
        parent = node.parent
        if not parent:
            break
        siblings = [c for c in parent.children if isinstance(c, Tag)]
        if node not in siblings:
            node = node.parent
            continue
        idx = siblings.index(node)
        prev = siblings[:idx]
        # 前面有兄弟节点包含实质正文，说明当前节点已处于正文尾部。
        if any(len(s.get_text(strip=True)) > 50 for s in prev):
            return node
        node = node.parent
    return None


def _normalized_text(tag: Tag) -> str:
    """获取标签内折叠空白后的纯文本。"""
    return " ".join(tag.get_text(" ", strip=True).split())


def _find_marker_cut_node(start: Tag | None) -> Tag | None:
    """沿祖先链查找最高的、以"小微推荐"开头的块级容器。"""
    candidate = None
    node = start
    while node and node.name and node.name != "body":
        if node.name in {"p", "div", "section"} and _normalized_text(node).startswith("小微推荐"):
            candidate = node
        node = node.parent
    return candidate


def _remove_tail_from(node: Tag) -> None:
    """删除给定节点及其后续所有兄弟节点。"""
    for sibling in list(node.find_next_siblings()):
        sibling.decompose()
    node.decompose()


def remove_recommendations(soup: BeautifulSoup) -> None:
    """删除"小微推荐▼"推荐区块。

    优先策略：直接定位包含"小微推荐"的文本节点，再向上找到
    「前面有实质正文内容的兄弟节点」的那一层，删除该节点及其后所有兄弟。
    这样不会被正文中更早出现的装饰性 <hr> 干扰。

    兜底策略：若未找到文本标记，则遍历所有 <hr>，选择能定位到同类尾部区块的节点。
    """
    for text in soup.find_all(string=lambda s: isinstance(s, str) and "小微推荐" in s):
        parent = text.parent if isinstance(text.parent, Tag) else None
        cut_node = _find_marker_cut_node(parent) or _find_cut_node(parent)
        if cut_node:
            _remove_tail_from(cut_node)
            return

    for hr in soup.find_all("hr"):
        parent = hr.parent if isinstance(hr.parent, Tag) else None
        cut_node = _find_cut_node(parent)
        if cut_node and "小微推荐" in cut_node.get_text(" ", strip=True):
            _remove_tail_from(cut_node)
            return


def clean_html(html_content: str) -> str:
    soup = BeautifulSoup(html_content, "html.parser")

    # 1. 删除推荐区块
    remove_recommendations(soup)

    # 2. 删除隐藏/占位元素（需在属性清理之前，依赖原始 data-role）
    remove_hidden_elements(soup)

    # 3. 清理所有标签的属性、class、style
    for tag in soup.find_all(True):
        clean_tag(tag)

    return str(soup)


def process_file(src: Path, dst: Path) -> tuple[int, int]:
    """清洗单个文件，返回 (原始字节数, 清洗后字节数)。"""
    original = src.read_text(encoding="utf-8")
    cleaned = clean_html(original)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(cleaned, encoding="utf-8")
    return len(original.encode()), len(cleaned.encode())


def main():
    parser = argparse.ArgumentParser(description="微信文章 HTML 清洗工具")
    parser.add_argument(
        "--file", metavar="PATH",
        help="只处理指定的单篇文章（默认批量处理全部）",
    )
    args = parser.parse_args()

    if args.file:
        src = Path(args.file)
        if not src.exists():
            print(f"错误：文件不存在：{src}", file=sys.stderr)
            sys.exit(1)
        dst = OUTPUT_DIR / src.name
        orig_size, clean_size = process_file(src, dst)
        ratio = (1 - clean_size / orig_size) * 100
        print(f"完成：{dst}")
        print(f"  原始：{orig_size:,} 字节  →  清洗后：{clean_size:,} 字节  （减少 {ratio:.1f}%）")
    else:
        files = sorted(ARTICLES_DIR.glob("*.html"))
        if not files:
            print(f"未找到文章，请确认 {ARTICLES_DIR}/ 目录存在。", file=sys.stderr)
            sys.exit(1)

        OUTPUT_DIR.mkdir(exist_ok=True)
        total_orig = total_clean = 0
        for src in files:
            dst = OUTPUT_DIR / src.name
            orig, clean = process_file(src, dst)
            total_orig += orig
            total_clean += clean

        ratio = (1 - total_clean / total_orig) * 100
        print(f"批量处理完成：共 {len(files)} 篇 → {OUTPUT_DIR}/")
        print(f"  总计：{total_orig:,} 字节  →  {total_clean:,} 字节  （减少 {ratio:.1f}%）")


if __name__ == "__main__":
    main()
