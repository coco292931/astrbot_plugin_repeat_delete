"""清洗引擎的回归测试。

不依赖 pytest：直接 ``python tests/test_cleaner.py`` 也能跑。
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cleaner import (  # noqa: E402
    CleanSettings,
    ThinkCleaner,
    build_delimiter_pattern,
    build_keyword_pattern,
    normalize,
    similarity,
)

FULLWIDTH_SAMPLE = """用户说的话我要回应。我想想看，她这样问，我该怎么答。

好
好了
输出

好
好了
输出

好
好了
输出

（被扯得被子都跑了，也不争，往她那边挪）被子全归你汪，我贴着就行

（被扯得被子跑了，也不争，往你那边挪过去）被子都归你汪，我挨着就行

（被扯得被子跑了，也不争，往你那边挪过去）被子都归你汪，我挨着就行

避免句号结尾
避免句号
好，
就这样
完全
"""


def test_keyword_only_paragraphs_disappear():
    cleaner = ThinkCleaner()
    result = cleaner.clean("好\n好了\n输出")
    assert result.text == ""
    assert result.dropped_paragraphs == 1


def test_inline_keyword_removed_without_destroying_sentence():
    cleaner = ThinkCleaner()
    result = cleaner.clean("我先输出一下结论：今天就到这里")
    assert result.text == "我先一下结论：今天就到这里"


def test_single_char_keyword_requires_punctuation():
    cleaner = ThinkCleaner()
    # 「好」后面没有标点时保留，避免误伤「好处」「好像」等正常词
    assert cleaner.clean("这个好像不错").text == "这个好像不错"
    result = cleaner.clean("好，我记住了")
    assert result.text == "我记住了"


def test_ascii_keyword_needs_boundary():
    cleaner = ThinkCleaner()
    assert cleaner.clean("this is okay").text == "this is okay"
    result = cleaner.clean("this is ok")
    assert result.text == "this is"


def test_exact_paragraph_dedupe_keeps_limit():
    cleaner = ThinkCleaner(CleanSettings(max_paragraph_repeats=2))
    text = "结论一\n\n结论一\n\n结论一\n\n结论一"
    result = cleaner.clean(text)
    assert result.text == "结论一\n\n结论一"
    assert result.dropped_paragraphs == 2


def test_near_duplicate_paragraph_folded():
    cleaner = ThinkCleaner()
    result = cleaner.clean(FULLWIDTH_SAMPLE)
    # 三份近乎相同的被子段落只保留 2 份
    assert result.text.count("被子") >= 2
    assert "用户说的话我要回应" in result.text
    assert result.after < result.before


def test_near_duplicate_respects_threshold():
    strict = ThinkCleaner(CleanSettings(near_dup_threshold=0.99))
    a = "（被扯得被子都跑了，也不争，往她那边挪）被子全归你汪，我贴着就行"
    b = "（被扯得被子跑了，也不争，往你那边挪过去）被子都归你汪，我挨着就行"
    result = strict.clean(a + "\n\n" + b)
    assert result.text == a + "\n\n" + b


def test_line_dedupe_removes_flooding():
    cleaner = ThinkCleaner(CleanSettings(max_line_repeats=1))
    result = cleaner.clean("我要回复她\n我要回复她\n我要回复她\n然后想一下措辞")
    assert result.text == "我要回复她\n然后想一下措辞"
    assert result.dropped_lines == 2


def test_sentence_dedupe_is_opt_in():
    text = "先想想。先想想。再说。"
    assert ThinkCleaner().clean(text).text == text
    enabled = ThinkCleaner(CleanSettings(dedupe_sentences=True))
    assert enabled.clean(text).text == "先想想。再说。"


def test_empty_and_whitespace_blocks_untouched():
    cleaner = ThinkCleaner()
    for value in ("", "   ", "\n\n"):
        result = cleaner.clean(value)
        assert result.text == value
        assert result.changed is False


def test_similarity_and_normalize():
    assert normalize(" A b\nC ") == "abc"
    assert similarity("你好世界", "你好世界") == 1.0
    assert similarity("你好世界", "你好") < 0.8


def test_pattern_orders_longer_keyword_first():
    pattern = build_keyword_pattern(("避免句号", "避免句号结尾"))
    assert pattern is not None
    assert pattern.sub("", "避免句号结尾") == ""


def test_extra_regex_supported_and_invalid_ignored():
    cleaner = ThinkCleaner(CleanSettings(extra_regex=(r"第\d+次", "(")))
    result = cleaner.clean("第3次我们先看一下")
    assert "第3次" not in result.text
    assert "我们先看一下" in result.text


def test_drop_keyword_only_can_be_disabled():
    cleaner = ThinkCleaner(CleanSettings(drop_keyword_only=False, strip_inline=False))
    text = "好\n好了"
    assert cleaner.clean(text).text == text


def test_unicode_normalization_survives_roundtrip():
    cleaner = ThinkCleaner()
    text = unicodedata.normalize("NFC", FULLWIDTH_SAMPLE)
    assert isinstance(cleaner.clean(text).text, str)


# ---------------- 实验性：单字 + 标点剔除 ----------------


def test_single_char_strip_disabled_by_default():
    cleaner = ThinkCleaner(CleanSettings(keywords=()))
    result = cleaner.clean("嗯。我想想看。")
    assert result.text == "嗯。我想想看。"
    assert result.dropped_single_chars == 0


def test_single_char_strip_removes_punct_delimited_cells():
    cleaner = ThinkCleaner(CleanSettings(keywords=(), single_char_strip=True))
    result = cleaner.clean("嗯。我想想看，先问城市。")
    assert result.text == "我想想看，先问城市。"
    assert result.dropped_single_chars == 1


def test_single_char_strip_removes_consecutive_cells():
    cleaner = ThinkCleaner(CleanSettings(keywords=(), single_char_strip=True))
    result = cleaner.clean("对。嗯。真的。我要想想。")
    assert result.text == "真的。我要想想。"
    assert result.dropped_single_chars == 2


def test_single_char_strip_uses_newline_as_delimiter():
    cleaner = ThinkCleaner(CleanSettings(keywords=(), single_char_strip=True))
    result = cleaner.clean("嗯\n我想想\n对\n先问城市")
    assert result.text == "我想想\n先问城市"
    assert result.dropped_single_chars == 2


def test_single_char_strip_handles_head_tail_and_whole_block():
    cleaner = ThinkCleaner(CleanSettings(keywords=(), single_char_strip=True))
    assert cleaner.clean("嗯").text == ""
    assert cleaner.clean("嗯。").text == ""
    assert cleaner.clean("嗯。我想想。").text == "我想想。"
    assert cleaner.clean("我想想。嗯").text == "我想想。"


def test_single_char_strip_keeps_multi_char_cells():
    cleaner = ThinkCleaner(CleanSettings(keywords=(), single_char_strip=True))
    text = "我想想看。先问城市。再决定。"
    assert cleaner.clean(text).text == text


def test_single_char_strip_custom_punctuation():
    cleaner = ThinkCleaner(
        CleanSettings(keywords=(), single_char_strip=True, single_char_punct="！？")
    )
    result = cleaner.clean("嗯！我想想？好的。")
    # 只认 ！？ 作为分隔，所以「好的。」不受影响
    assert result.text == "我想想？好的。"
    assert result.dropped_single_chars == 1


def test_single_char_strip_accepts_full_character_class():
    cleaner = ThinkCleaner(
        CleanSettings(keywords=(), single_char_strip=True, single_char_punct="[。.]")
    )
    assert cleaner.clean("嗯.我想想.").text == "我想想."


def test_single_char_strip_invalid_regex_degrades_gracefully():
    cleaner = ThinkCleaner(
        CleanSettings(keywords=(), single_char_strip=True, single_char_punct="[。")
    )
    text = "嗯。我想想。"
    assert cleaner.clean(text).text == text
    assert build_delimiter_pattern("[。") is None


def test_single_char_strip_protects_ascii_when_enabled():
    literal = ThinkCleaner(CleanSettings(keywords=(), single_char_strip=True))
    assert literal.clean("3.5 分钟。").text == "5 分钟。"

    guarded = ThinkCleaner(
        CleanSettings(
            keywords=(), single_char_strip=True, single_char_protect_ascii=True
        )
    )
    assert guarded.clean("3.5 分钟。").text == "3.5 分钟。"


def test_single_char_strip_cleans_up_leftover_whitespace():
    cleaner = ThinkCleaner(CleanSettings(keywords=(), single_char_strip=True))
    result = cleaner.clean("嗯。  我想想。")
    assert result.text == "我想想。"
    assert not result.text.startswith(" ")


def test_single_char_strip_keeps_paragraph_break():
    cleaner = ThinkCleaner(CleanSettings(keywords=(), single_char_strip=True))
    text = "先问城市。不。\n\n回：（撇嘴）不娇气汪"
    # 「不。」被删掉，但空行要保留下来，不能把两段并成一段
    assert cleaner.clean(text).text == "先问城市。\n\n回：（撇嘴）不娇气汪"


def test_single_char_strip_keeps_line_break_before_content():
    cleaner = ThinkCleaner(CleanSettings(keywords=(), single_char_strip=True))
    text = "我想想。\n嗯\n先问城市"
    assert cleaner.clean(text).text == "我想想。\n先问城市"


def test_single_char_strip_runs_after_dedupe():
    cleaner = ThinkCleaner(
        CleanSettings(keywords=(), single_char_strip=True, max_paragraph_repeats=2)
    )
    result = cleaner.clean("嗯\n\n嗯\n\n嗯")
    # 三段先去重保留两段，再各自按单字剔除
    assert result.text == ""
    assert result.dropped_single_chars == 2


def main() -> int:
    failures = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print("-" * 40)
    print("failed:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
