"""
微信公众号文章分类工具

功能：
  1. classify —— 按文章类型自动分类，将 articles/ 中的推文拷贝到 articles_classified/ 目录
  2. classify-dept —— 在类型分类基础上，再按"特别支持 | 部门"署名进行部门二级分类，
     输出到 articles_by_dept/ 目录

用法：
  python classifier.py classify                   # 按类型分类
  python classifier.py classify-dept              # 按类型 + 部门分类
"""

import argparse
import os
import re
import shutil
import sys
from collections import defaultdict


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")
CLASSIFIED_DIR = os.path.join(SCRIPT_DIR, "articles_classified")
BY_DEPT_DIR = os.path.join(SCRIPT_DIR, "articles_by_dept")


# ──────────────────────────────────────────────
# 分类规则定义
# ──────────────────────────────────────────────

# 分类采用"标题优先"策略：
#   scope="title" → 仅匹配标题（避免正文中的模板化措辞造成误判）
#   scope="both"  → 同时匹配标题和正文
# 按优先级排列，首个命中的类别即为结果
# 每项: (类别名, 关键词列表, 排除词列表, 匹配范围)

CATEGORY_RULES = [
	# 悼念纪念 —— 高优先级
	("悼念纪念", [
		"悼念", "送别", "沉痛", "缅怀",
	], [], "title"),

	# 时政要闻 —— 仅标题匹配，防止正文模板化措辞误判
	("时政要闻", [
		"习近平", "总书记", "国务院副总理",
		"全国政协副主席", "《求是》", "丁薛祥",
		"讲话精神", "视察",
	], [], "title"),

	# 党建思政 —— 仅标题
	("党建思政", [
		"党委.*部署", "思政", "同上一堂课",
		"传习读书社", "传习杯", "正确政绩观",
		"主题教育", "党建", "党课",
		"聚焦思政", "联学", "党员.*同上",
		"共同学习.*聚焦",
	], [], "title"),

	# 安全提醒
	("安全提醒", [
		"警惕", "警方", "反诈", "紧急提醒",
		"安全提醒", "防骗",
	], [], "title"),

	# 雄安校区
	("雄安校区", [
		"雄安",
	], [], "title"),

	# 研究生招生
	("研究生招生", [
		"研考", "复试.*线", "复试.*录取", "考研",
		"国家线", "硕士.*招生", "博士.*招生",
		"研究生.*招生", "上岸", "研招",
		"考研.*成绩", "研考.*报名", "四六级",
		"硕士.*新生.*须知",
	], [], "title"),

	# 本科招生
	("本科招生", [
		"高考", "招生章程", "招生亮点", "招生计划",
		"招生指南", "招生咨询", "投档线", "录取分数",
		"报考.*交", "多少分", "拔尖班",
		"录取.*通知书", "新增.*专业",
		"录取进程", "录取.*查询", "本科.*招生",
		"生源质量", "招生.*发布",
	], [], "title"),

	# 就业创业
	("就业创业", [
		"就业", "岗位", "招聘需求", "就创",
		"西部计划", "面试亭", "面试.*空间",
		"专项保研", "3000.*岗位", "校企联合培养",
	], [], "title"),

	# 人才引进
	("人才引进", [
		"诚聘", "英才", "海外优青",
		"招聘院长", "直聘教授", "非教师.*招聘",
	], [], "title"),

	# 毕业季
	("毕业季", [
		"毕业典礼", "毕业照", "毕业歌会", "毕业快乐",
		"毕业.*创意", "致青春", "毕业季",
		"毕业.*合影", "毕业下一站",
		"毕业.*讲话", "珍藏.*合影",
	], [], "title"),

	# 迎新季
	("迎新季", [
		"新生.*报到", "军训", "辅导员.*来啦",
		"新生大数据", "入学.*攻略", "新生须知",
		"迎新", "开学典礼", "新生必读",
		"迎来.*新同学", "新生.*辅导员",
		"辅导员", "报到须知",
	], [], "title"),

	# 竞赛获奖 —— 标题匹配
	("竞赛获奖", [
		"擂主", "特等奖", "一等奖", "二等奖",
		"金奖", "🥇", "🥈", "🥉",
		"四冠", "优胜杯", "国奖", "勇夺",
		"全国.*等奖",
	], ["知行奖学金", "奖学金揭晓"], "title"),

	# 学术科研 —— 标题匹配
	("学术科研", [
		"Nature", "Science子刊", "子刊",
		"学术.*盛会", "学术.*会议", "ESCI", "CSCD",
		"INSPEC", "数据库收录", "算法",
		"世界前1%", "前1%学科", "重大项目",
		"国家级.*课程", "科研创新", "论文",
	], [], "title"),

	# 科技创新
	("科技创新", [
		"国家标准", "国家级.*平台", "科技进展",
		"科普基地", "无人驾驶", "低空经济",
		"新突破", "科技周", "工匠", "发明",
		"科技新星", "管道蜘蛛侠", "中国方案",
		"方案.*国家标准", "交小智",
	], [], "title"),

	# 荣誉表彰
	("荣誉表彰", [
		"表彰", "入选", "上榜", "光荣榜",
		"奖学金", "提名奖", "感动海淀",
		"知行奖", "国家级基地", "首批.*牵头",
		"强国.*科学家", "名单.*公布",
		"名单揭晓", "名单公布",
		"全优", "全国第一",
	], [], "title"),

	# 人物风采
	("人物风采", [
		"好样的", "全能绽放", "闪闪发光", "青年榜样",
		"实力圈粉", "追的星", "优秀不止",
		"圈粉", "登上.*人民日报",
		"逐梦", "答案在.*一线",
		"通感6G", "他是.*交大",
		"全球第三", "世界金奖",
	], [], "title"),

	# 校庆校友
	("校庆校友", [
		"校庆", "校友", "返校", "周年",
		"129岁", "130周年", "校友会",
		"欢迎.*回家", "传旗接力", "值年",
	], [], "title"),

	# 国际交流
	("国际交流", [
		"国际.*会议", "全球.*盛会", "海外",
		"欧洲", "意大利", "米兰", "澳门",
		"AI赋能.*国际", "菲尔兹奖", "冬奥",
		"卓越工程师.*国际",
	], [], "title"),

	# 媒体聚焦
	("媒体聚焦", [
		"新闻联播", "《人民日报》", "新华社",
		"央视", "光明日报", "北京日报",
		"交大声音",
	], [], "title"),

	# 文化艺术
	("文化艺术", [
		"话剧", "歌会", "歌声.*澎湃", "冯远征",
		"电影节", "北影节", "读书社",
		"沉浸式.*思政", "原创.*话剧",
	], [], "title"),

	# 节日祝福
	("节日祝福", [
		"新年", "拜年", "元宵", "春节",
		"国庆.*快乐", "521", "25周年快乐",
		"过新年", "贺.*新春", "新年贺词",
		"🧧", "年味", "爱你老己",
		"我爱你.*中国",
	], [], "title"),

	# 通知公告
	("通知公告", [
		"放假", "校历", "提醒(?!.*警)", "选课",
		"查分", "须知", "开学前",
		"暑期.*安排", "寒假.*时间",
		"生活指南", "放假.*通知",
		"放假.*安排", "开始选课",
	], [], "title"),

	# 校园活动 —— 标题匹配
	("校园活动", [
		"开放日", "抢票", "限时",
		"科普月", "招新", "预告",
		"直播", "现场直击", "开市",
		"开讲", "带娃",
	], [], "title"),

	# 校园风光
	("校园风光", [
		"花开", "发芽", "郁金香", "银杏",
		"下雪", "好春光", "红果园",
		"赏花", "春光", "拍照指南",
		"北交大的秋",
	], [], "title"),

	# 校园生活 —— 标题
	("校园生活", [
		"减脂餐", "酸奶", "美食", "月饼",
		"打卡", "PPT", "上新",
		"就.*是.*玩", "干货", "走心", "贴心",
		"月历", "年味图鉴", "嗖.*上新",
		"专属", "好运连连", "创意",
		"亲测好用", "舍不得删",
		"爸爸妈妈", "薛定谔", "心.*驿站",
		"525", "鲜鲜鲜", "心理",
	], [], "title"),

	# ── 以下为"正文辅助匹配"规则，仅对标题无法判定的文章生效 ──

	# 正文匹配：党建思政（正文中明确出现的主题教育关键词）
	("党建思政", [
		"中央八项规定", "学习教育实施方案",
		"主题教育.*工作会", "从严治党",
	], [], "both"),

	# 正文匹配：本科招生
	("本科招生", [
		"高考.*倒计时", "高考.*加油", "高考.*冲刺",
		"高招咨询", "招生政策宣讲",
	], [], "both"),

	# 正文匹配：竞赛获奖
	("竞赛获奖", [
		"全国.*特等奖", "全国.*一等奖", "全国.*金奖",
		"喜报.*获奖", "再传喜报",
	], [], "both"),

	# 正文匹配：校园活动
	("校园活动", [
		"运动会", "春季田径", "体育嘉年华",
		"暑期.*社会实践", "志愿服务", "学雷锋",
		"科普.*活动", "社团.*招新",
	], [], "both"),

	# 正文匹配：荣誉表彰
	("荣誉表彰", [
		"荣获.*称号", "授予.*荣誉",
		"获北交大.*最高", "优秀.*导师",
		"年度.*人物", "全国.*团队",
		"先进集体", "先进个人",
	], [], "both"),

	# 正文匹配：人物风采
	("人物风采", [
		"成长故事", "拍的铁路", "机器人大会",
		"个人事迹",
	], [], "both"),

	# 正文匹配：迎新季
	("迎新季", [
		"军训.*检阅", "请检阅", "阅兵",
		"开学第一课",
	], [], "both"),

	# 正文匹配：研究生招生
	("研究生招生", [
		"研招.*确认", "考研.*最后",
		"考研.*倒计时", "稳住.*能赢",
	], [], "both"),

	# 正文匹配：学术科研
	("学术科研", [
		"学术.*论坛", "高端.*论坛",
		"重磅.*发布.*学术", "研究报告.*发布",
	], [], "both"),

	# 正文匹配：校庆校友
	("校庆校友", [
		"校友.*大会", "校友.*聚",
		"母校.*生日",
	], [], "both"),

	# 标题兜底：通用模式
	("校园活动", [
		"直击", "超燃", "大场面", "现场",
		"出征", "集结", "见证",
	], [], "title"),

	("人物风采", [
		"加油", "好样", "又美又飒",
		"交大首位", "优秀.*优秀",
	], [], "title"),

	("荣誉表彰", [
		"祝贺", "点赞", "喜报",
		"榜单", "重磅.*团队",
		"奖项.*\\+", "成果.*\\+",
	], [], "title"),

	("校园生活", [
		"解锁", "揭秘.*实力", "爱上.*课",
		"读.*书.*青春", "新学期",
		"寒.*假.*帮", "暖.*帮",
	], [], "title"),

	("校园发展", [
		"新学院", "合作.*启新程",
		"领袖.*关怀", "景观.*落成",
		"首创.*思享", "搜索.*交大青年",
		"发榜.*年终",
	], [], "title"),

	# 兜底：正文匹配通知公告
	("通知公告", [
		"重要考试", "今起报名", "开始报名",
		"等你确认",
	], [], "title"),

	# 兜底：世界第一/第二等宽泛标题
	("竞赛获奖", [
		"世界第一", "助力.*第一",
	], [], "title"),

	# 兜底：正文匹配不信类
	("校园生活", [
		"不信.*不信", "最最棒",
		"读.*书.*好.*青.*春",
	], [], "title"),
]


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def strip_html(html_text):
	"""去除 HTML 标签，返回纯文本"""
	text = re.sub(r"<[^>]+>", " ", html_text)
	text = re.sub(r"&nbsp;", " ", text)
	text = re.sub(r"&[a-zA-Z]+;", " ", text)
	text = re.sub(r"\s+", " ", text)
	return text.strip()


def read_article(filepath):
	"""读取文章文件，返回 (标题, 纯文本内容)"""
	with open(filepath, encoding="utf-8") as f:
		html = f.read()
	title_match = re.search(r"<title>(.*?)</title>", html)
	title = title_match.group(1) if title_match else ""
	text = strip_html(html)
	return title, text


def normalize_title(title):
	"""去除标题中的空格和特殊装饰字符，便于关键词匹配"""
	t = re.sub(r"[\s\u200b\u00a0]+", "", title)
	t = re.sub(r"[꧁꧂⏰‼️📸🧡💓🤗🥰🤩🥳🧧]+", "", t)
	return t


def classify_article(title, text):
	"""
	根据标题和正文内容，匹配分类规则，返回类别名称。
	按优先级顺序匹配，首个命中的类别即为结果。
	scope="title" 仅匹配标题，scope="both" 匹配标题+正文。
	"""
	norm_title = normalize_title(title)
	title_target = title + " " + norm_title
	combined = title_target + " " + text

	for category, keywords, excludes, scope in CATEGORY_RULES:
		target = title_target if scope == "title" else combined

		excluded = False
		for exc in excludes:
			if re.search(exc, target):
				excluded = True
				break
		if excluded:
			continue

		for kw in keywords:
			if re.search(kw, target):
				return category

	return "其他"


def extract_departments(text):
	"""
	从文章纯文本中提取"特别支持 | XXX"中的部门名称。
	多个部门用空格分隔时拆分为独立部门。
	返回部门名称列表，未找到则返回空列表。
	"""
	match = re.search(r"特别支持\s*[|｜]\s*(.+?)(?:\s{2,}|$)", text)
	if not match:
		return []

	raw = match.group(1).strip()
	# 截取到下一个署名标签
	raw = re.split(
		r"\s+(通讯员|本期编辑|责任编辑|编辑|文字|摄影|排版|制图|来源"
		r"|采写|拍摄|动图|视频|图片|资料|风景图|活动组织)",
		raw
	)[0].strip()

	# 去除可能残留的媒体来源和人名
	raw = re.split(r"\s+(?:图\s*\||摄\s*\|)", raw)[0].strip()

	if not raw or len(raw) > 200:
		return []

	# 先处理 HTML 标签拆词导致的碎片（如"后勤服务 产业 集团"应为"后勤服务产业集团"）
	# 合并过短的碎片（<=2字）到相邻词
	tokens = re.split(r"\s+", raw)
	merged = []
	for t in tokens:
		if merged and len(t) <= 2 and not re.search(r"[（(）)]", t):
			merged[-1] = merged[-1] + t
		else:
			merged.append(t)

	# 再次检查：如果前一个片段很短，也向后合并
	final_tokens = []
	for t in merged:
		if final_tokens and len(final_tokens[-1]) <= 2 and not re.search(r"[（(）)]", final_tokens[-1]):
			final_tokens[-1] = final_tokens[-1] + t
		else:
			final_tokens.append(t)

	# 合并用顿号连接的部门名称（如"纪委办公室、监察处"）
	departments = []
	i = 0
	while i < len(final_tokens):
		part = final_tokens[i]
		while i + 1 < len(final_tokens) and (part.endswith("、") or final_tokens[i + 1].startswith("、")):
			i += 1
			part = part + final_tokens[i]
		# 拆分无空格但包含多个"X处（部）Y处（部）"形式的粘连部门
		sub_parts = re.findall(r"[\u4e00-\u9fff]+(?:[（(][^）)]*[）)])?", part)
		if len(sub_parts) > 1 and all(len(s) >= 2 for s in sub_parts):
			departments.extend(sub_parts)
		elif part and len(part) <= 40:
			departments.append(part)
		i += 1

	return departments


def print_separator(char="─", width=55):
	print(char * width)


def copy_file(src, dst_dir, filename):
	"""拷贝文件到目标目录，自动创建目录"""
	os.makedirs(dst_dir, exist_ok=True)
	dst = os.path.join(dst_dir, filename)
	shutil.copy2(src, dst)
	return dst


# ──────────────────────────────────────────────
# 命令1：按类型分类
# ──────────────────────────────────────────────

def classify_articles():
	"""
	扫描 articles/ 目录，按文章类型分类，
	将文件拷贝到 articles_classified/<类型>/ 下。
	"""
	if not os.path.isdir(ARTICLES_DIR):
		print("✘ 未找到 articles/ 目录，请先下载文章")
		sys.exit(1)

	html_files = sorted([
		f for f in os.listdir(ARTICLES_DIR)
		if f.endswith(".html")
	])

	if not html_files:
		print("✘ articles/ 目录中没有 HTML 文件")
		sys.exit(1)

	print_separator("=")
	print("文章分类（按类型）")
	print_separator("=")
	print()
	print(f"  源目录：{ARTICLES_DIR}")
	print(f"  输出目录：{CLASSIFIED_DIR}")
	print(f"  待分类：{len(html_files)} 篇")
	print()

	# 清理旧的输出目录
	if os.path.exists(CLASSIFIED_DIR):
		shutil.rmtree(CLASSIFIED_DIR)

	stats = defaultdict(int)

	for i, filename in enumerate(html_files):
		filepath = os.path.join(ARTICLES_DIR, filename)
		title, text = read_article(filepath)
		category = classify_article(title, text)

		dst_dir = os.path.join(CLASSIFIED_DIR, category)
		copy_file(filepath, dst_dir, filename)
		stats[category] += 1

		if (i + 1) % 50 == 0 or (i + 1) == len(html_files):
			print(f"  已处理 {i + 1}/{len(html_files)} 篇...")

	print()
	print_separator()
	print("分类统计：")
	print_separator()
	print()
	for cat, cnt in sorted(stats.items(), key=lambda x: -x[1]):
		print(f"  {cnt:3d} 篇  {cat}")
	print()
	print(f"  共 {len(stats)} 个类别，{sum(stats.values())} 篇文章")
	print()
	print_separator("=")
	print(f"✔ 分类完成！文件已保存到：{CLASSIFIED_DIR}")
	print_separator("=")


# ──────────────────────────────────────────────
# 命令2：按类型 + 部门分类
# ──────────────────────────────────────────────

def classify_by_department():
	"""
	在类型分类的基础上，进一步按"特别支持"署名中的部门分类。
	输出结构：articles_by_dept/<部门>/<类型>/文件.html
	无部门署名的文章放入 未标注部门/<类型>/ 下。
	"""
	if not os.path.isdir(ARTICLES_DIR):
		print("✘ 未找到 articles/ 目录，请先下载文章")
		sys.exit(1)

	html_files = sorted([
		f for f in os.listdir(ARTICLES_DIR)
		if f.endswith(".html")
	])

	if not html_files:
		print("✘ articles/ 目录中没有 HTML 文件")
		sys.exit(1)

	print_separator("=")
	print("文章分类（按类型 + 部门）")
	print_separator("=")
	print()
	print(f"  源目录：{ARTICLES_DIR}")
	print(f"  输出目录：{BY_DEPT_DIR}")
	print(f"  待分类：{len(html_files)} 篇")
	print()

	# 清理旧的输出目录
	if os.path.exists(BY_DEPT_DIR):
		shutil.rmtree(BY_DEPT_DIR)

	cat_stats = defaultdict(int)
	dept_stats = defaultdict(int)
	has_dept = 0

	for i, filename in enumerate(html_files):
		filepath = os.path.join(ARTICLES_DIR, filename)
		title, text = read_article(filepath)
		category = classify_article(title, text)
		departments = extract_departments(text)

		if departments:
			has_dept += 1
			for dept in departments:
				dept_stats[dept] += 1
				dst_dir = os.path.join(BY_DEPT_DIR, dept, category)
				copy_file(filepath, dst_dir, filename)
		else:
			dst_dir = os.path.join(BY_DEPT_DIR, "未标注部门", category)
			copy_file(filepath, dst_dir, filename)

		cat_stats[category] += 1

		if (i + 1) % 50 == 0 or (i + 1) == len(html_files):
			print(f"  已处理 {i + 1}/{len(html_files)} 篇...")

	print()
	print_separator()
	print("类型分类统计：")
	print_separator()
	print()
	for cat, cnt in sorted(cat_stats.items(), key=lambda x: -x[1]):
		print(f"  {cnt:3d} 篇  {cat}")

	print()
	print_separator()
	print("部门统计（仅含已标注部门的文章）：")
	print_separator()
	print()
	for dept, cnt in sorted(dept_stats.items(), key=lambda x: -x[1]):
		print(f"  {cnt:3d} 篇  {dept}")

	print()
	print(f"  共 {len(cat_stats)} 个类别，{len(dept_stats)} 个部门")
	print(f"  有部门标注：{has_dept} 篇，无部门标注：{sum(cat_stats.values()) - has_dept} 篇")
	print()
	print_separator("=")
	print(f"✔ 分类完成！文件已保存到：{BY_DEPT_DIR}")
	print_separator("=")


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

def main():
	parser = argparse.ArgumentParser(
		description="微信公众号文章分类工具",
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog="""
示例：
  python classifier.py classify           # 按文章类型分类
  python classifier.py classify-dept      # 按类型 + 部门分类
		""",
	)
	subparsers = parser.add_subparsers(dest="command")
	subparsers.add_parser("classify", help="按文章类型分类到 articles_classified/")
	subparsers.add_parser("classify-dept", help="按类型 + 部门分类到 articles_by_dept/")

	args = parser.parse_args()

	if not args.command:
		parser.print_help()
		return

	if args.command == "classify":
		classify_articles()
	elif args.command == "classify-dept":
		classify_by_department()


if __name__ == "__main__":
	main()
