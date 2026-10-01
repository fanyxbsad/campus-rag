# -*- coding: utf-8 -*-
"""
清洗 data/raw/ 原始文档 → data/clean/<标准化文件>.txt

处理内容：
1. 修复 PDF 提取乱码（渊→（ 冤→） 尧→、 咱2017暂→〔2017〕 也2017页→〔2017〕 附件N院→附件N：）
2. 删除噪声行：页码（单独数字）、装饰行（· ·）、网页复制日期行
3. 合并 PDF 硬换行（正文每行约 25 字被截断 → 按「空行 + 编号行」边界重组段落）
4. 学生手册(xueshengshouce.txt) 按目录拆成 28 个独立子文档，
   丢弃：前言、目录、纯表格附件（社团表单）、一览表
5. 每个输出文件首行写入 TITLE: <文档标题>，供切分脚本使用

用法: python scripts/clean.py
"""
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CLEAN = ROOT / "data" / "clean"

# ──────────────────────────────────────────────
# 1. 乱码修复（PDF 字体映射错误）
# ──────────────────────────────────────────────
MOJIBAKE_SIMPLE = {"渊": "（", "冤": "）", "尧": "、"}
MOJIBAKE_PATTERNS = [
    (re.compile(r"咱\s*(\d{4})\s*暂"), r"〔\1〕"),    # 杭电本咱2017暂179号 → 〔2017〕
    (re.compile(r"咱\s*(\d{4})\s*[\]］]"), r"〔\1〕"),  # 杭电教 咱2013] 145号 → 〔2013〕
    (re.compile(r"也\s*(\d{4})\s*页"), r"〔\1〕"),    # 杭电本 也2017页 180号 → 〔2017〕
    (re.compile(r"^附([件表])(\d*)院$"), r"附\1\2："),  # 附件1院 → 附件1：
]


def fix_mojibake(s: str) -> str:
    for k, v in MOJIBAKE_SIMPLE.items():
        s = s.replace(k, v)
    for pat, rep in MOJIBAKE_PATTERNS:
        s = pat.sub(rep, s)
    return s


# ──────────────────────────────────────────────
# 2. 噪声行判断
# ──────────────────────────────────────────────
NOISE_PATTERNS = [
    re.compile(r"^\d{1,3}$"),               # 页码
    re.compile(r"^([·•．.]\s*)+$"),         # · · 装饰行
    re.compile(r"^\d{1,3}[·•．.]{1,3}$"),   # 123··
    re.compile(r"^\d{4}-\d{2}-\d{2}\s*\d{0,2}[:\d]*$"),  # 网页复制日期 2023-04-24 10:40
]


def is_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return any(p.fullmatch(s) for p in NOISE_PATTERNS)


# ──────────────────────────────────────────────
# 3. 段落重组：合并 PDF 硬换行
#    规则：空行 或 编号行（第X条/（一）/1./①…）开新段，其余行拼接
# ──────────────────────────────────────────────
NEW_PARA = re.compile(
    r"^(第[一二三四五六七八九十百零\d]+[章节条]"
    r"|[一二三四五六七八九十]+、"
    r"|[（(][一二三四五六七八九十\d]+[)）]"
    r"|[①②③④⑤⑥⑦⑧⑨⑩]"
    r"|\d+[.、．]"
    r"|注[：:]"
    r"|表\d+)"
)


def rebuild_paragraphs(lines):
    paras, cur = [], ""

    def flush():
        nonlocal cur
        if cur.strip():
            paras.append(cur.strip())
        cur = ""

    for raw in lines:
        s = fix_mojibake(raw).strip()
        if not s:
            flush()
            continue
        if is_noise(raw):
            continue
        if not cur:
            cur = s
        elif NEW_PARA.match(s):
            flush()
            cur = s
        else:
            # 防止数字/字母跨行粘连（如 95～100 + 5.0 → 95～100 5.0）
            if re.search(r"[0-9A-Za-z%．]$", cur) and re.match(r"^[0-9A-Za-z%]", s):
                cur += " " + s
            else:
                cur += s
    flush()

    # 丢弃网页表格残留的表头短段
    return [p for p in paras if p not in ("类型", "简介")]


# ──────────────────────────────────────────────
# 4. 学生手册：按目录拆分子文档
#    锚点 = 各子文档标题首行（规范化后按序匹配），跨行标题靠顺序消费
# ──────────────────────────────────────────────
SHOUCE_SECTIONS = [
    # (锚点行前缀, 完整文档标题, 是否保留, 是否需要文号确认)
    ("普通高等学校学生管理规定", "普通高等学校学生管理规定（教育部令第41号）", True, True),
    ("高等学校学生行为准则", "高等学校学生行为准则（教育部令第21号）", True, True),
    ("学生伤害事故处理办法", "学生伤害事故处理办法（教育部令第12号）", True, True),
    ("杭州电子科技大学", "杭州电子科技大学本科学生学籍管理规定", True, True),
    ("杭州电子科技大学", "杭州电子科技大学本科学生成绩管理规定", True, True),
    ("杭州电子科技大学", "杭州电子科技大学本科学生转学实施细则（试行）", True, True),
    ("杭州电子科技大学转专业", "杭州电子科技大学转专业（类）与大类分流实施办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学本科毕业生学士学位授予细则", True, True),
    ("杭州电子科技大学", "杭州电子科技大学学生纪律处分实施细则", True, True),
    ("杭州电子科技大学", "杭州电子科技大学学生校内申诉管理规定", True, True),
    ("考场规则", "杭州电子科技大学考场规则", True, True),
    ("杭州电子科技大学学生考试", "杭州电子科技大学学生考试违规、作弊的认定办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学本科生选课管理办法", True, True),
    ("杭州电子科技大学本科生", "杭州电子科技大学本科生修读辅修专业、第二本科专业、第二学士学位的管理办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学本科生课外教育管理规定", True, True),
    ("关于学生参加毕业", "关于学生参加毕业设计（论文）的若干规定", True, True),
    ("杭州电子科技大学", "杭州电子科技大学创新与拓展学分认定管理办法", True, True),
    ("创新与拓展学分认定标准一览表", "创新与拓展学分认定标准一览表", False, False),
    ("课堂管理规则", "杭州电子科技大学课堂管理规则", True, True),
    ("杭州电子科技大学", "杭州电子科技大学学生证、校徽和火车票优惠卡管理办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学三好学生、优秀学生干部评比办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学本科生奖学金评审办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学学生综合测评实施办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学国家奖学金、省政府奖学金、国家励志奖学金和国家助学金评比办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学家庭经济困难学生认定办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学学生校内勤工助学管理办法", True, True),
    ("杭州电子科技大学", "杭州电子科技大学学生住宿管理规定", True, True),
    ("杭州电子科技大学", "杭州电子科技大学学生医疗相关规定", True, True),
    ("杭州电子科技大学", "杭州电子科技大学学生社团管理条例（试行）", True, True),
    ("附件", "学生社团管理条例附件表单", False, False),
]

# 文号/章标题特征：正文标题后 1~3 行内必现其一，用于排除目录项
CONFIRM = re.compile(r"(〔\d{4}〕|\[\d{4}\]|令第|第[一二三四五六七八九十百零\d]+章|号\s*$)")


def norm(line: str) -> str:
    return re.sub(r"\s+", "", fix_mojibake(line))


def split_shouce(lines):
    """按锚点顺序把手册切成子文档，返回 [(title, 段落列表), ...]（仅保留项）

    锚点匹配需通过「文号确认」：正文标题行后 1~3 行内应有文号行或章标题，
    目录页里的标题项后面跟的是页码/其他标题，借此排除目录误匹配。
    """
    sections, cur, idx = [], None, 0
    for i, line in enumerate(lines):
        if idx < len(SHOUCE_SECTIONS):
            anchor, title, keep, need_confirm = SHOUCE_SECTIONS[idx]
            m = norm(line)
            if m.startswith(anchor) and len(m) <= 22:
                ok = not need_confirm
                if not ok:
                    for j in range(i + 1, min(i + 4, len(lines))):
                        if CONFIRM.search(fix_mojibake(lines[j]).strip()):
                            ok = True
                            break
                if ok:
                    cur = {"title": title, "keep": keep, "lines": []}
                    sections.append(cur)
                    idx += 1
                    continue  # 锚点行（标题首行）不进正文
        if cur is not None and cur["keep"]:
            cur["lines"].append(line)
    if idx < len(SHOUCE_SECTIONS):
        print(f"  [警告] 手册有 {len(SHOUCE_SECTIONS) - idx} 个锚点未匹配: "
              f"{[s[1] for s in SHOUCE_SECTIONS[idx:]]}")
    out = []
    for sec in sections:
        if not sec["keep"] or not sec["lines"]:
            continue
        paras = rebuild_paragraphs(sec["lines"])
        # 丢弃首段「标题续行 + 文号」粘连段（如 本科学生学籍管理规定杭电本〔2017〕179号）
        if paras and len(paras[0]) <= 60 and not re.search(r"[。；；：]$", paras[0]):
            paras.pop(0)
        if paras:
            out.append((sec["title"], paras))
    return out


# ──────────────────────────────────────────────
# 5. 其他文件的标题映射
# ──────────────────────────────────────────────
FILE_TITLES = {
    "jiangxuejin.txt": "学生奖励与奖学金体系简介",
    "kaoqinzhidu.txt": "学生考勤与请假管理制度",
    "zhaunzhuanye.txt": "大学本科学生转专业工作管理办法",
    "sushe.txt": "大学学生公寓住宿管理办法",
    "tushuguan.txt": "大学图书馆图书借阅规则",
    "xiuxuefuxue.txt": "中国人民大学学生休学复学办理工作规程",
    "shuangxuewei.txt": "辅修与双学位修读常见问题",
    "huankao.txt": "上海交通大学本科生缓考管理办法",
    "jidian.txt": "大学本科生课程成绩管理办法",  # 原文无标题行，按内容指派
    "xiaoyuanka.txt": "上海大学校园卡管理规范",
    "xuanke.txt": "湖南大学本科生选课管理办法",
}


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def write_clean(filename: str, title: str, paras):
    path = CLEAN / filename
    path.write_text("TITLE: " + title + "\n\n" + "\n\n".join(paras) + "\n", encoding="utf-8")
    return path


# ──────────────────────────────────────────────
# 6. 校名匿名化：文献来自不同学校，统一抹去具体校名
#    （含文号缩写「杭电学〔2013〕113号」→「某大学〔2013〕113号」；
#      字符间夹空格的居中标题变体也处理，\s* 兜底）
# ──────────────────────────────────────────────
UNIV_REPLACEMENTS = [
    (re.compile(r"杭\s*州\s*电\s*子\s*科\s*技\s*大\s*学"), "某大学"),
    (re.compile(r"中\s*国\s*人\s*民\s*大\s*学"), "某大学"),
    (re.compile(r"上\s*海\s*交\s*通\s*大\s*学"), "某大学"),
    (re.compile(r"上\s*海\s*大\s*学"), "某大学"),
    (re.compile(r"湖\s*南\s*大\s*学"), "某大学"),
    (re.compile(r"杭\s*电"), "某大"),
    (re.compile(r"湖\s*大"), "某大"),   # 文号缩写：湖大教字〔2022〕10号
    (re.compile(r"（网址：i\.sjtu\.edu\.cn）"), ""),  # 校名拼音缩写藏在网址里
]

# PDF 表格被压平成一行后的修复（rebuild 产出的精确原文 → 手工整理的通顺表述）
# 住宿管理规定第二十二条作息时间表：3行×3列被压平后数值交错，恢复为分档表述
TABLE_FIXES = [
    (
        "本科生晚上自主熄灯、统一熄灯和关开门时间如下：时 间 周日—周四 周五、周六 节假日"
        "自主熄灯时间 22:50 23:50统一熄灯时间 23:00 24:00 23:50 24:00"
        "公寓楼开关门时间 6:20—23:00 6:20—24:00 6:20—24:00夏季、冬季通宵供电。",
        "本科生作息时间表（按周日至周四、周五周六、节假日三档）："
        "自主熄灯时间，周日至周四为22:50，周五、周六为23:50，节假日为23:50；"
        "统一熄灯时间，周日至周四为23:00，周五、周六为24:00，节假日为24:00；"
        "公寓楼开关门时间，周日至周四为早上6:20开门、晚上23:00关门，"
        "周五、周六为早上6:20开门、晚上24:00关门，"
        "节假日为早上6:20开门、晚上24:00关门。夏季、冬季通宵供电。",
    ),
]


def anonymize(text: str) -> str:
    for pat, new in UNIV_REPLACEMENTS:
        text = pat.sub(new, text)
    return text


def finalize(paras):
    """段落出库前最后一道工序：校名匿名化 → 页码残留清除 → 表格压平修复"""
    out = []
    for p in paras:
        p = anonymize(p)
        # PDF 页码残留：段落中间混入的「— 2 —」「— 3 —」标记（只匹配 破折号+数字+破折号，
        # 不会误伤 6:20—23:00、90－100 这类合法区间写法）
        p = re.sub(r"—\s*\d+\s*—", "", p)
        for old, new in TABLE_FIXES:
            if old in p:
                p = p.replace(old, new)
        out.append(p)
    return out


def main():
    CLEAN.mkdir(parents=True, exist_ok=True)
    # 全量重建：先清空旧产物，避免改名后新旧文件并存（chunk 会重复读）
    stale = list(CLEAN.glob("*.txt"))
    for p in stale:
        p.unlink()
    if stale:
        print(f"已清空旧产物 {len(stale)} 个文件\n")

    # -- 手册拆分（拆分锚点按原文匹配，匿名化在拆分之后做）--
    print("== 处理 xueshengshouce.txt ==")
    raw_lines = (RAW / "xueshengshouce.txt").read_text(encoding="utf-8").splitlines()
    for i, (title, paras) in enumerate(split_shouce(raw_lines), 1):
        title, paras = anonymize(title), finalize(paras)
        p = write_clean(f"shouce_{i:02d}_{safe_filename(title)}.txt", title, paras)
        print(f"  {p.name}  ({len(paras)} 段, {sum(len(x) for x in paras)} 字)")

    # -- 其他文件 --
    print("== 处理其余文件 ==")
    for fname, title in FILE_TITLES.items():
        path = RAW / fname
        if not path.exists():
            print(f"  [跳过] {fname} 不存在")
            continue
        paras = rebuild_paragraphs(path.read_text(encoding="utf-8").splitlines())
        # 标题与正文同步匿名化，下面的首段去重比较才对得上
        title, paras = anonymize(title), finalize(paras)
        # 去掉首行标题（避免与 TITLE 重复）：支持「段首含标题」或「标题含整段」两种形态
        if paras and (paras[0].startswith(title[:10]) or title.startswith(paras[0])):
            paras[0] = paras[0][len(title):].strip() if paras[0].startswith(title) else ""
            if not paras[0]:
                paras.pop(0)
        p = write_clean(safe_filename(title) + ".txt", title, paras)
        print(f"  {p.name}  ({len(paras)} 段, {sum(len(x) for x in paras)} 字)")

    print(f"\n完成，输出目录: {CLEAN}")


if __name__ == "__main__":
    main()
