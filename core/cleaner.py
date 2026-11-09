"""think 文本清洗引擎。

规则沿用 repeat-detect/clean.py 的两层思路：

1. 关键词剥离：整段只剩关键词时整段消失；句中夹带关键词时只删词并整理标点。
2. 重复折叠：同一段落在一个 think 块内最多保留 N 次，完全相同或短段落相似度达到阈值都算重复。

在此基础上补充了行级折叠（处理单行刷屏）与可选的句子级折叠。
本模块只做纯文本处理，不依赖 AstrBot，便于单独测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache

# 与 repeat-detect/clean.py 中 KW 的默认关键词保持一致；
# 「好」「用」是单字关键词，默认要求后接标点才算噪声。
DEFAULT_KEYWORDS: tuple[str, ...] = (
    "避免句号结尾",
    "避免整齐结构",
    "避免句号",
    "避免整齐",
    "用上面那句",
    "用这个",
    "就这样",
    "好了",
    "够了",
    "完全",
    "输出",
    "ok",
    "end",
    "好",
    "用",
)

PUNCT_CHARS = '，。、！？；：,.!?;:）)】]」』"”’'

_PARAGRAPH_SPLIT = re.compile(r"\n[ \t]*\n+")
_ASCII_WORD = re.compile(r"[A-Za-z0-9_]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")
_BLANK_CHARS = re.compile(
    r"[\s，。、！？；：,.!?;:\"'“”‘’（）()【】\[\]「」『』…—~～\-]+"
)
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([，。、！？；：,.!?;:）)\]】」』\"”’])")
_DUP_PUNCT = re.compile(r"([，。、；：,!?;:])\1+")
_PUNCT_BEFORE_END = re.compile(r"[，、,；;：:]+(?=[。！？.!?])")
_LEADING_PUNCT = re.compile(r"^[，。、！？；：,.!?;:\s]+")
_TRAILING_PUNCT = re.compile(r"[，、,；;：:]+$")

# 3-gram Jaccard 预筛阈值。最终是否重复仍由编辑距离判定，
# 这里只是为了在段落很多时避免昂贵的全量比较。
_GRAM_PREFILTER = 0.30


def _is_ascii_word(text: str) -> bool:
    return _ASCII_WORD.fullmatch(text) is not None


def normalize(text: str) -> str:
    """去掉所有空白并转小写，用于判重。"""
    return re.sub(r"\s+", "", text).lower()


def is_blank(text: str) -> bool:
    """仅剩空白或标点时为 True。"""
    return _BLANK_CHARS.sub("", text) == ""


def tidy_punctuation(text: str) -> str:
    """删词后留下的空格与标点整理。"""
    text = _MULTI_SPACE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _DUP_PUNCT.sub(r"\1", text)
    text = _PUNCT_BEFORE_END.sub("", text)
    text = _LEADING_PUNCT.sub("", text)
    text = _TRAILING_PUNCT.sub("", text)
    return text.strip()


def build_keyword_pattern(
    keywords: tuple[str, ...] | list[str],
    extra_regex: tuple[str, ...] | list[str] = (),
    *,
    ignore_case: bool = True,
    anchor_single_char: bool = True,
) -> re.Pattern[str] | None:
    """把关键词列表编译成单个正则。

    英文词要求两侧不是字母（避免 ``ok`` 命中 ``okay``），
    单字中文关键词要求后接标点（避免 ``好``/``用`` 误伤正常句子），
    其余关键词按字面匹配。
    """
    alternatives: list[tuple[str, int]] = []
    for raw in keywords:
        keyword = str(raw).strip()
        if not keyword:
            continue
        body = re.escape(keyword)
        if _is_ascii_word(keyword):
            alternatives.append((f"(?<![A-Za-z]){body}(?![A-Za-z])", len(keyword)))
        elif anchor_single_char and len(keyword) == 1:
            alternatives.append((f"{body}(?=[{re.escape(PUNCT_CHARS)}])", len(keyword)))
        else:
            alternatives.append((body, len(keyword)))

    for raw in extra_regex:
        pattern = str(raw).strip()
        if not pattern:
            continue
        try:
            re.compile(pattern)
        except re.error:
            continue
        alternatives.append((f"(?:{pattern})", len(pattern)))

    if not alternatives:
        return None
    # 同一位置优先匹配更长的关键词，例如「避免句号结尾」先于「避免句号」
    alternatives.sort(key=lambda item: item[1], reverse=True)
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile("|".join(item[0] for item in alternatives), flags)


@lru_cache(maxsize=4096)
def _grams(text: str, size: int = 3) -> frozenset[str]:
    if len(text) < size:
        return frozenset((text,))
    return frozenset(text[i : i + size] for i in range(len(text) - size + 1))


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (char_a != char_b),
                )
            )
        previous = current
    return previous[-1]


def similarity(a: str, b: str) -> float:
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return 1.0 - levenshtein(a, b) / longest


@dataclass(frozen=True)
class CleanSettings:
    """清洗判定参数。数量类参数表示「最多保留几次」。"""

    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    extra_regex: tuple[str, ...] = ()
    ignore_case: bool = True
    anchor_single_char: bool = True
    strip_inline: bool = True
    drop_keyword_only: bool = True
    max_paragraph_repeats: int = 2
    near_dup_enabled: bool = True
    near_dup_max_chars: int = 40
    near_dup_threshold: float = 0.80
    near_dup_length_ratio: float = 0.35
    dedupe_lines: bool = True
    max_line_repeats: int = 1
    dedupe_sentences: bool = False
    max_sentence_repeats: int = 1


@dataclass(frozen=True)
class BlockResult:
    """单个 think 块的清洗结果。"""

    text: str
    before: int = 0
    after: int = 0
    changed: bool = False
    keyword_hits: int = 0
    dropped_paragraphs: int = 0
    dropped_lines: int = 0
    dropped_sentences: int = 0


def _group_dedupe(
    items: list[str],
    keep: int,
    *,
    near_enabled: bool,
    max_chars: int,
    threshold: float,
    length_ratio: float,
) -> tuple[list[str], int]:
    """每个重复组只保留前 keep 个元素，返回 (保留列表, 删除数量)。"""
    if len(items) <= 1:
        return list(items), 0

    keep = max(1, keep)
    groups: list[dict] = []
    by_length: dict[int, list[dict]] = {}
    exact: dict[str, dict] = {}

    for index, item in enumerate(items):
        key = normalize(item)
        group = exact.get(key)
        if group is None and near_enabled and 0 < len(key) <= max_chars:
            group = _find_near_group(
                key, groups, by_length, max_chars, threshold, length_ratio
            )
        if group is None:
            group = {"key": key, "members": []}
            groups.append(group)
            by_length.setdefault(len(key), []).append(group)
        exact.setdefault(key, group)
        group["members"].append(index)

    kept_indexes: set[int] = set()
    for group in groups:
        kept_indexes.update(group["members"][:keep])

    kept = [item for index, item in enumerate(items) if index in kept_indexes]
    return kept, len(items) - len(kept)


def _find_near_group(
    key: str,
    groups: list[dict],
    by_length: dict[int, list[dict]],
    max_chars: int,
    threshold: float,
    length_ratio: float,
) -> dict | None:
    if not groups:
        return None
    length = len(key)
    lowest = max(1, int(length * (1.0 - length_ratio)))
    highest = min(max_chars, int(length / (1.0 - length_ratio)) + 1)
    key_grams = _grams(key)

    for candidate_length in range(lowest, highest + 1):
        for group in by_length.get(candidate_length, ()):
            other = group["key"]
            if not other or other == key:
                continue
            longest = max(len(key), len(other))
            if abs(len(key) - len(other)) > length_ratio * longest:
                continue
            other_grams = _grams(other)
            union = key_grams | other_grams
            if not union:
                continue
            if len(key_grams & other_grams) / len(union) < _GRAM_PREFILTER:
                continue
            if similarity(key, other) >= threshold:
                return group
    return None


class ThinkCleaner:
    """把一段 think 文本清洗为更短的内容。"""

    def __init__(self, settings: CleanSettings | None = None) -> None:
        raw = settings or CleanSettings()
        self.settings = replace(
            raw,
            max_paragraph_repeats=max(1, int(raw.max_paragraph_repeats)),
            max_line_repeats=max(1, int(raw.max_line_repeats)),
            max_sentence_repeats=max(1, int(raw.max_sentence_repeats)),
            near_dup_max_chars=max(4, int(raw.near_dup_max_chars)),
            near_dup_threshold=min(1.0, max(0.0, float(raw.near_dup_threshold))),
            near_dup_length_ratio=min(1.0, max(0.0, float(raw.near_dup_length_ratio))),
        )
        self._pattern = build_keyword_pattern(
            self.settings.keywords,
            self.settings.extra_regex,
            ignore_case=self.settings.ignore_case,
            anchor_single_char=self.settings.anchor_single_char,
        )
        # 整行判纯关键词时使用不锚定标点的宽松版本，
        # 这样单独一行「好」也能被识别成噪声。
        self._loose_pattern = (
            build_keyword_pattern(
                self.settings.keywords,
                self.settings.extra_regex,
                ignore_case=self.settings.ignore_case,
                anchor_single_char=False,
            )
            if self._pattern is not None
            else None
        )

    # ---------------- 对外入口 ----------------

    def clean(self, text: str) -> BlockResult:
        if not isinstance(text, str):
            return BlockResult(text="")
        before = len(text)
        if not text.strip():
            return BlockResult(text=text, before=before, after=before)

        hits = [0]
        paragraphs: list[str] = []
        dropped_paragraphs = 0
        dropped_lines = 0
        dropped_sentences = 0

        for raw_paragraph in _split_paragraphs(text):
            cleaned, lines_removed, sentences_removed = self._clean_paragraph(
                raw_paragraph, hits
            )
            if cleaned is None:
                dropped_paragraphs += 1
                continue
            paragraphs.append(cleaned)
            dropped_lines += lines_removed
            dropped_sentences += sentences_removed

        paragraphs, removed = self._dedupe_paragraphs(paragraphs)
        dropped_paragraphs += removed

        new_text = "\n\n".join(paragraphs)
        return BlockResult(
            text=new_text,
            before=before,
            after=len(new_text),
            changed=new_text != text,
            keyword_hits=hits[0],
            dropped_paragraphs=dropped_paragraphs,
            dropped_lines=dropped_lines,
            dropped_sentences=dropped_sentences,
        )

    # ---------------- 内部实现 ----------------

    def _strip_line(self, line: str, hits: list[int]) -> str | None:
        """删词后的行文本；返回 None 表示整行丢弃。"""
        if self._pattern is None:
            return None if self.settings.drop_keyword_only and is_blank(line) else line

        if self.settings.drop_keyword_only:
            pure_hits = self._pure_keyword_hits(line)
            if pure_hits:
                hits[0] += pure_hits
                return None

        replaced, hit_count = self._pattern.subn("", line)
        stripped = line
        if hit_count:
            hits[0] += hit_count
            if self.settings.strip_inline:
                stripped = tidy_punctuation(replaced)
            elif self.settings.drop_keyword_only and is_blank(replaced):
                stripped = ""

        if self.settings.drop_keyword_only and is_blank(stripped):
            return None
        return stripped

    def _pure_keyword_hits(self, line: str) -> int:
        """整行只由关键词和标点组成时返回关键词个数，否则返回 0。"""
        pattern = self._loose_pattern
        if pattern is None:
            return 0
        rest = line
        matched = 0
        while True:
            match = pattern.search(rest)
            if match is None or match.end() == match.start():
                break
            matched += 1
            rest = rest[: match.start()] + rest[match.end() :]
        if matched and is_blank(rest):
            return matched
        return 0

    def _clean_paragraph(
        self, paragraph: str, hits: list[int]
    ) -> tuple[str | None, int, int]:
        """返回 (清洗后段落或 None, 删除行数, 删除句子数)。"""
        dropped_lines = 0

        if not self.settings.dedupe_lines:
            cleaned = self._strip_line(paragraph, hits)
            if cleaned is None:
                return None, 0, 0
            cleaned, dropped_sentences = self._dedupe_sentences(cleaned)
            if not cleaned.strip():
                return None, 0, dropped_sentences
            return cleaned, 0, dropped_sentences

        kept_lines: list[str] = []
        for line in paragraph.split("\n"):
            cleaned = self._strip_line(line, hits)
            if cleaned is None:
                dropped_lines += 1
                continue
            kept_lines.append(cleaned)

        if not kept_lines:
            return None, dropped_lines, 0

        kept_lines, removed = _group_dedupe(
            kept_lines,
            self.settings.max_line_repeats,
            near_enabled=False,
            max_chars=self.settings.near_dup_max_chars,
            threshold=self.settings.near_dup_threshold,
            length_ratio=self.settings.near_dup_length_ratio,
        )
        dropped_lines += removed

        dropped_sentences = 0
        if self.settings.dedupe_sentences:
            rebuilt: list[str] = []
            for line in kept_lines:
                line_text, removed_sentences = self._dedupe_sentences(line)
                dropped_sentences += removed_sentences
                if line_text.strip():
                    rebuilt.append(line_text)
            kept_lines = rebuilt

        result = "\n".join(kept_lines).strip()
        if not result:
            return None, dropped_lines, dropped_sentences
        return result, dropped_lines, dropped_sentences

    def _dedupe_sentences(self, line: str) -> tuple[str, int]:
        if not self.settings.dedupe_sentences:
            return line, 0
        parts = _SENTENCE_SPLIT.split(line)
        if len(parts) <= 1:
            return line, 0
        kept, removed = _group_dedupe(
            parts,
            self.settings.max_sentence_repeats,
            near_enabled=False,
            max_chars=self.settings.near_dup_max_chars,
            threshold=self.settings.near_dup_threshold,
            length_ratio=self.settings.near_dup_length_ratio,
        )
        if removed == 0:
            return line, 0
        return "".join(kept), removed

    def _dedupe_paragraphs(self, paragraphs: list[str]) -> tuple[list[str], int]:
        if len(paragraphs) <= 1:
            return paragraphs, 0
        return _group_dedupe(
            paragraphs,
            self.settings.max_paragraph_repeats,
            near_enabled=self.settings.near_dup_enabled,
            max_chars=self.settings.near_dup_max_chars,
            threshold=self.settings.near_dup_threshold,
            length_ratio=self.settings.near_dup_length_ratio,
        )


def _split_paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [part.strip() for part in _PARAGRAPH_SPLIT.split(text) if part.strip()]
