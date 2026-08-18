#!/usr/bin/env python3
"""检查小霖 AI 产品文章中的硬性风格问题与高风险语言形状。"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from dataclasses import dataclass
from pathlib import Path


HARD_HUMOR = (
    "老铁",
    "兄弟们",
    "咱就是说",
    "笑死",
    "绝了",
    "绷不住",
    "一整个",
    "谁懂啊",
    "给我整不会了",
)

CLICKBAIT = (
    "史上最强",
    "彻底颠覆",
    "所有人都该看",
    "不看后悔",
    "震惊",
    "杀疯了",
    "赢麻了",
)

CONTEXT_JARGON = (
    "赋能",
    "抓手",
    "商业闭环",
    "价值闭环",
    "能力沉淀",
    "拉通",
    "底层逻辑",
    "顶层设计",
    "认知跃迁",
    "价值释放",
    "能力建设",
    "降本增效",
    "内容矩阵",
    "全链路",
    "组合拳",
    "结构性机会",
    "关键命题",
    "深层逻辑",
    "技术底座",
    "迭代闭环",
    "颗粒度",
    "对齐",
    "协同",
    "链路",
    "生态位",
    "心智",
    "范式",
    "方法论",
    "核心变量",
    "打法",
    "闭环",
)

ROAD_SIGNS = (
    "更微妙的是",
    "还有一层",
    "只说对了一半",
    "值得注意的是",
    "需要指出的是",
    "从某种意义上说",
    "更重要的是",
    "真正的问题是",
)

PIVOT_PATTERNS = (
    re.compile(r"(?:并)?不是[^。！？\n]{0,90}而是"),
    re.compile(r"并非[^。！？\n]{0,90}而是"),
    re.compile(r"不在于[^。！？\n]{0,90}而在于"),
    re.compile(r"与其说[^。！？\n]{0,90}(?:不如|毋宁|倒不如)"),
    re.compile(r"表面(?:上)?[^。！？\n]{0,90}(?:其实|实际|实则)"),
    re.compile(r"看似[^。！？\n]{0,90}(?:其实|实际|实则)"),
)

NOMINALIZATION_PATTERNS = (
    re.compile(
        r"进行(?:了|一次|一场|着)?[^。，！？\n]{0,10}"
        r"(?:调整|优化|升级|分析|讨论|沟通|梳理|复盘|迭代|探索|尝试|思考|规划|布局)"
    ),
    re.compile(
        r"实现了?[^。，！？\n]{0,14}的?[^。，！？\n]{0,6}"
        r"(?:提升|增长|突破|转变|跃升|落地)"
    ),
    re.compile(r"完成了?对[^。，！？\n]{0,16}的"),
    re.compile(r"起到了?[^。，！？\n]{0,12}的?作用"),
    re.compile(r"具有[^。，！？\n]{0,10}(?:意义|价值)"),
)

CONJUNCTIONS = (
    "因为",
    "所以",
    "但是",
    "然而",
    "同时",
    "此外",
    "而且",
    "并且",
    "因此",
    "不仅",
)

REPEATED_OPENERS = (
    "其实",
    "不过",
    "当然",
    "所以",
    "但是",
    "很多人",
    "问题是",
    "更重要的是",
    "说到这里",
)

INTERPRETIVE_WORDS = (
    "说明",
    "意味着",
    "反映",
    "原因",
    "影响",
    "判断",
    "相比",
    "口径",
    "因此",
    "对应",
)


@dataclass
class Paragraph:
    position: int
    text: str
    han: int
    sentences: int


def han_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def excerpt(value: str, width: int = 60) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= width else value[: width - 1] + "…"


def mask_non_prose(text: str) -> str:
    """屏蔽元数据、代码、链接和 Markdown 引文，避免把引用当作者口吻。"""

    def mask(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group())

    patterns = (
        re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL),
        re.compile(r"\x60\x60\x60.*?\x60\x60\x60", re.DOTALL),
        re.compile(r"\x60[^\x60\n]*\x60"),
        re.compile(r"\]\([^\n)]*\)"),
        re.compile(r"https?://[^\s)>]+"),
        re.compile(r"(?m)^>\s?.*$"),
    )
    masked = text
    for pattern in patterns:
        masked = pattern.sub(mask, masked)
    return masked


def non_overlapping_terms(text: str, terms: tuple[str, ...]):
    matches = []
    occupied = []
    for term in sorted(terms, key=len, reverse=True):
        for match in re.finditer(re.escape(term), text):
            start, end = match.span()
            if any(start < old_end and end > old_start for old_start, old_end in occupied):
                continue
            matches.append((start, term))
            occupied.append((start, end))
    return sorted(matches)


def all_matches(text: str, patterns: tuple[re.Pattern[str], ...]):
    matches = []
    for pattern in patterns:
        matches.extend(pattern.finditer(text))
    return sorted(matches, key=lambda match: match.start())


def first_title(text: str) -> tuple[int, str] | None:
    match = re.search(r"(?m)^#\s+(.+)$", text)
    return (match.start(1), match.group(1).strip()) if match else None


def prose_paragraphs(text: str) -> list[Paragraph]:
    paragraphs = []
    cursor = 0
    for block in re.split(r"\n\s*\n", text):
        position = text.find(block, cursor)
        cursor = max(position + len(block), cursor)
        clean = re.sub(r"[>*_]", "", block).strip()
        if not clean or clean.startswith(("#", "http", "![")):
            continue
        if re.match(r"^(?:[-+*]|\d+[.、])\s", clean):
            continue
        count = han_count(clean)
        if count < 4:
            continue
        sentences = max(1, len(re.findall(r"[。！？!?]", clean)))
        paragraphs.append(Paragraph(position, clean, count, sentences))
    return paragraphs


def sentence_length_cv(text: str):
    lengths = [
        han_count(match.group())
        for match in re.finditer(r"[^。！？!?\n]+[。！？!?]", text)
        if han_count(match.group()) >= 4
    ]
    if len(lengths) < 12:
        return None
    mean = sum(lengths) / len(lengths)
    variance = sum((value - mean) ** 2 for value in lengths) / len(lengths)
    return (variance ** 0.5) / mean, len(lengths)


def anaphora_runs(text: str, minimum: int = 3):
    matches = []
    for sentence in re.finditer(r"[^。！？!?\n]+(?:[。！？!?]|$)", text):
        clauses = [
            clause.strip()
            for clause in re.split(r"[，、；,;]", sentence.group())
            if han_count(clause) >= 3
        ]
        run = 1
        for previous, current in zip(clauses, clauses[1:]):
            if previous[:2] == current[:2] and re.match(r"[\u4e00-\u9fff]{2}", current):
                run += 1
                if run >= minimum:
                    matches.append(sentence)
                    break
            else:
                run = 1
    return matches


def data_heavy(paragraphs: list[Paragraph]):
    results = []
    for paragraph in paragraphs:
        numbers = re.findall(r"\d+(?:\.\d+)?%?", paragraph.text)
        if len(numbers) < 3:
            continue
        if any(word in paragraph.text for word in INTERPRETIVE_WORDS):
            continue
        results.append(paragraph)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="检查小霖 AI 产品文章的风格风险")
    parser.add_argument("path", help="Markdown 或文本文件路径，使用 - 从标准输入读取")
    args = parser.parse_args()

    try:
        text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        print(f"无法读取稿件。{error}", file=sys.stderr)
        return 2

    prose = mask_non_prose(text)
    total_han = han_count(prose)
    if total_han == 0:
        print("没有检测到汉字。", file=sys.stderr)
        return 2

    failures = []
    warnings = []

    humor_hits = non_overlapping_terms(prose, HARD_HUMOR)
    for position, phrase in humor_hits:
        failures.append(f"过度口语或网络梗，第 {line_number(text, position)} 行，{phrase}")

    title = first_title(text)
    title_hits = []
    if title:
        position, value = title
        for phrase in CLICKBAIT:
            if phrase in value:
                title_hits.append(phrase)
                failures.append(f"标题夸大，第 {line_number(text, position)} 行，{phrase}")

    punctuation = {}
    for symbol, label in (("：", "冒号"), (":", "英文冒号"), ("—", "破折号"), ("–", "连接号式破折号")):
        count = prose.count(symbol)
        punctuation[label] = count
        limit = max(4, total_han // 180)
        if count > limit:
            warnings.append(f"{label}共 {count} 处，当前提醒线为 {limit} 处。检查是否连续用于制造气势。")

    pivots = all_matches(prose, PIVOT_PATTERNS)
    pivot_limit = max(2, total_han // 700)
    if len(pivots) > pivot_limit:
        samples = "；".join(
            f"第 {line_number(text, match.start())} 行“{excerpt(match.group(), 36)}”"
            for match in pivots[:4]
        )
        warnings.append(
            f"对照或翻转句共 {len(pivots)} 处，当前提醒线为 {pivot_limit} 处。"
            f"真实比较可以保留，模板化抬价需要改写。{samples}"
        )

    road_hits = non_overlapping_terms(prose, ROAD_SIGNS)
    road_limit = max(2, total_han // 900)
    if len(road_hits) > road_limit:
        samples = "、".join(dict.fromkeys(term for _, term in road_hits))
        warnings.append(f"段落路标共 {len(road_hits)} 处，重点检查 {samples}。")

    jargon_hits = non_overlapping_terms(prose, CONTEXT_JARGON)
    jargon_limit = max(3, total_han // 500)
    if len(jargon_hits) > jargon_limit:
        samples = "、".join(dict.fromkeys(term for _, term in jargon_hits))
        warnings.append(
            f"产品或商业术语共 {len(jargon_hits)} 处，当前提醒线为 {jargon_limit} 处。"
            f"本义准确时保留，用来抬高普通事情时改写。重点检查 {samples}。"
        )

    question_count = len(re.findall(r"[？?]", prose))
    question_limit = max(4, total_han // 350)
    if question_count > question_limit:
        warnings.append(
            f"问号共 {question_count} 处，当前提醒线为 {question_limit} 处。"
            "检查是否连续反问或替读者制造情绪。"
        )

    second_person = len(re.findall(r"你", prose))
    second_limit = max(8, total_han // 180)
    if second_person > second_limit:
        warnings.append(
            f"第二人称“你”共 {second_person} 处，当前提醒线为 {second_limit} 处。"
            "检查文章是否变成训话或口播。"
        )

    nominalizations = all_matches(prose, NOMINALIZATION_PATTERNS)
    if nominalizations:
        samples = "；".join(
            f"第 {line_number(text, match.start())} 行“{excerpt(match.group(), 32)}”"
            for match in nominalizations[:4]
        )
        warnings.append(f"发现 {len(nominalizations)} 处名词化表达。优先还原成具体动作。{samples}")

    anaphoras = anaphora_runs(prose)
    if anaphoras:
        samples = "；".join(
            f"第 {line_number(text, match.start())} 行“{excerpt(match.group(), 38)}”"
            for match in anaphoras[:3]
        )
        warnings.append(f"发现 {len(anaphoras)} 处三连以上同构排比。检查是否为了制造节奏。{samples}")

    conjunction_hits = non_overlapping_terms(prose, CONJUNCTIONS)
    if total_han >= 600 and len(conjunction_hits) * 1000 / total_han > 8:
        common = "、".join(
            f"{term} {count} 次"
            for term, count in collections.Counter(
                term for _, term in conjunction_hits
            ).most_common(4)
        )
        warnings.append(
            f"连词密度偏高，每千字 {len(conjunction_hits) * 1000 // total_han} 个。{common}。"
        )

    cv_result = sentence_length_cv(prose)
    if cv_result and cv_result[0] < 0.40:
        warnings.append(
            f"全文 {cv_result[1]} 个句子长度过于接近，变异系数 {cv_result[0]:.2f}。"
            "检查是否每句都使用同一节奏。"
        )

    paragraphs = prose_paragraphs(prose)
    if len(paragraphs) >= 10:
        one_sentence = sum(paragraph.sentences <= 1 for paragraph in paragraphs)
        ratio = one_sentence / len(paragraphs)
        if ratio >= 0.75:
            warnings.append(f"可识别段落中有 {ratio:.0%} 只有一句话，可能形成统一的短段鼓点。")

    opener_counts = collections.Counter()
    for paragraph in paragraphs:
        value = paragraph.text.lstrip("“‘\"（(")
        for opener in REPEATED_OPENERS:
            if value.startswith(opener):
                opener_counts[opener] += 1
                break
    repeated = [(opener, count) for opener, count in opener_counts.items() if count >= 4]
    if repeated:
        details = "、".join(f"{opener} {count} 次" for opener, count in repeated)
        warnings.append(f"段落开场重复。{details}。")

    heavy = data_heavy(paragraphs)
    if heavy:
        samples = "；".join(
            f"第 {line_number(text, paragraph.position)} 行“{excerpt(paragraph.text, 44)}”"
            for paragraph in heavy[:3]
        )
        warnings.append(
            f"发现 {len(heavy)} 个数字密集但缺少解释信号的段落。"
            f"检查是否写成资料汇总。{samples}"
        )

    print(f"汉字数 {total_han}")
    print(
        f"过度幽默 {len(humor_hits)}，标题夸大 {len(title_hits)}，"
        f"对照句 {len(pivots)}，反问 {question_count}，第二人称 {second_person}，"
        f"商业术语 {len(jargon_hits)}，名词化 {len(nominalizations)}，"
        f"数据密集段 {len(heavy)}"
    )

    if failures:
        print("\n需要修改")
        for item in failures:
            print(f"- {item}")

    if warnings:
        print("\n需要人工判断")
        for item in warnings:
            print(f"- {item}")

    if not failures and not warnings:
        print("\n未发现这份检查器覆盖的问题。")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
