"""Chunker：段落/句界边界 / 1200± / overlap 100 / ≤2000 硬上限 / 区间不变量（pack M3 STEP 8）。"""
import math
import unittest

from core.chunker import (HARD_CAP, OVERLAP, TARGET_SIZE, ChunkSpec,
                          chunk_text, split_paragraph_at_sentences)


def make_doc(n_paras: int, para_len: int = 200, with_headings: bool = True) -> tuple:
    """构造 n 段文档，段落形如「第{i}段：」+ 正文。返回 (text, sections)。"""
    paras = []
    sections = []
    for i in range(n_paras):
        filler = "这是一个关于成长与选择的班会故事，真实发生。" * (para_len // 20)
        if with_headings and i % 10 == 0:
            heading = f"第{i // 10 + 1}章"
            paras.append(heading)
            paras.append(f"第{i}段：{filler}")
        else:
            paras.append(f"第{i}段：{filler}")
    text = "\n\n".join(paras)
    # 章节：每 10 段一个标题
    if with_headings:
        pos = 0
        for p in paras:
            if p.startswith("第") and p.endswith("章"):
                level = 1
                start = pos
                # 找下一个标题位置
                sections.append({"heading": p, "level": level,
                                 "char_start": start, "char_end": len(text)})
            pos += len(p) + 2
        # 修正每个章节 end 为下一个章节 start
        for i in range(len(sections) - 1):
            sections[i]["char_end"] = sections[i + 1]["char_start"]
        sections[-1]["char_end"] = len(text)
    else:
        sections = [{"heading": "", "level": 1, "char_start": 0, "char_end": len(text)}]
    return text, sections


class ChunkerTests(unittest.TestCase):
    def test_small_text_single_chunk(self):
        text, sections = make_doc(3)
        chunks = chunk_text(text, sections)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, text)
        self.assertEqual(chunks[0].char_start, 0)
        self.assertEqual(chunks[0].char_end, len(text))

    def test_target_size_and_sequence(self):
        text, sections = make_doc(40)  # 40 × ~250 字符 ≈ 1 万字符
        chunks = chunk_text(text, sections)
        self.assertGreater(len(chunks), 3)
        for i, c in enumerate(chunks):
            self.assertEqual(c.sequence, i)
            # 目标块长 1200 ±：允许 overlap 与段落粒度造成的偏差，但不得远超
            self.assertLess(len(c.text), HARD_CAP)
        # 大部分块在目标附近（±60%，段落粒度允许偏差）
        own_lens = [len(c.text) - (OVERLAP + 2) for c in chunks if c.sequence > 0]
        self.assertTrue(any(abs(l - TARGET_SIZE) < TARGET_SIZE * 0.6 for l in own_lens))

    def test_hard_cap_never_exceeded(self):
        text, sections = make_doc(30, para_len=400)
        for c in chunk_text(text, sections):
            self.assertLessEqual(len(c.text), HARD_CAP, "≤2000 硬上限不可突破")

    def test_many_tiny_paragraphs_hard_cap(self):
        """审查锁：大量短段落（分隔符开销累计）也不得突破 2000 硬上限。"""
        text = "\n\n".join(["好诗"] * 700)
        chunks = chunk_text(text, [{"heading": "", "level": 1,
                                    "char_start": 0, "char_end": len(text)}])
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c.text), HARD_CAP)

    def test_multi_newline_separator_span_invariant(self):
        """审查锁：3+ 换行分隔符下，块自身内容仍严格等于文档切片（含原始分隔符）。"""
        text = "\n\n\n".join([f"第{i}段内容，关于成长与选择。" for i in range(40)])
        chunks = chunk_text(text, [{"heading": "", "level": 1,
                                    "char_start": 0, "char_end": len(text)}])
        for c in chunks:
            self.assertEqual(text[c.char_start:c.char_end],
                             c.text[len(c.text) - (c.char_end - c.char_start):],
                             f"chunk {c.sequence} 自身内容必须等于文档精确切片")

    def test_single_para_up_to_cap_own_chunk(self):
        """单段 1800 字符（> target、≤ hard_cap）：独立成块且不破上限。"""
        para = "这是一个超长段落的班会故事。" * 120  # ≈1800 字符
        text = "前段。\n\n" + para
        chunks = chunk_text(text, [{"heading": "", "level": 1,
                                    "char_start": 0, "char_end": len(text)}])
        for c in chunks:
            self.assertLessEqual(len(c.text), HARD_CAP)
        # para 所在块自身内容 == para
        big = [c for c in chunks if c.char_end - c.char_start >= len(para) - 2]
        self.assertTrue(big, "超长段落必须完整出现在一个块中")

    def test_span_invariant(self):
        """char 区间与文档内容严格对应：doc_text[start:end] == 块自身内容（overlap 前缀除外）。"""
        text, sections = make_doc(25)
        for c in chunk_text(text, sections):
            own = text[c.char_start:c.char_end]
            self.assertTrue(c.text.endswith(own), f"chunk {c.sequence} 尾部必须等于其区间文本")
            # overlap 前缀来自上一块尾部，长度受限
            prefix_len = len(c.text) - len(own)
            self.assertLessEqual(prefix_len, OVERLAP + 2)

    def test_overlap_bounded(self):
        """overlap 前缀必须来自上一块自身内容的尾部（有限重叠，不跨块乱接）。"""
        text, sections = make_doc(40)
        chunks = chunk_text(text, sections)
        for i in range(1, len(chunks)):
            prev_own = text[chunks[i - 1].char_start:chunks[i - 1].char_end]
            cur_own = text[chunks[i].char_start:chunks[i].char_end]
            prefix_len = len(chunks[i].text) - len(cur_own)
            if prefix_len > 2:
                prefix = chunks[i].text[:prefix_len].strip()
                self.assertIn(prefix, prev_own,
                              f"chunk {i} 的 overlap 前缀必须来自上一块尾部")

    def test_boundaries_fall_at_paragraph_heads(self):
        """块自身内容必须从段落头开始（绝不从段落中间起切）。"""
        text, sections = make_doc(30)
        for c in chunk_text(text, sections):
            own = text[c.char_start:c.char_end]
            if c.char_start > 0:
                self.assertEqual(text[c.char_start - 2:c.char_start], "\n\n",
                                 f"chunk {c.sequence} 必须从段落头开始")

    def test_huge_paragraph_sentence_split(self):
        """超长段落（>2000 字符）：句界硬切，每块 ≤2000。"""
        sentences = [f"这是第{i}个句子，讲述了一个真实的班会故事。" for i in range(200)]
        text = "".join(sentences)
        chunks = chunk_text(text, [{"heading": "", "level": 1, "char_start": 0,
                                    "char_end": len(text)}])
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c.text), HARD_CAP)
        # 句界切分：每块自身内容以完整句子结尾（句号）
        for c in chunks:
            own = text[c.char_start:c.char_end]
            self.assertTrue(own.endswith("。"), f"chunk {c.sequence} 必须落在句界")

    def test_hard_character_cut_no_sentence(self):
        """无句界的超长段落：按字符硬切（不无限切）。"""
        text = "字" * 5000
        pieces = split_paragraph_at_sentences(text)
        self.assertGreater(len(pieces), 1)
        for piece, rel_start, rel_end in pieces:
            self.assertLessEqual(len(piece), HARD_CAP)
            self.assertEqual(text[rel_start:rel_end], piece, "相对区间必须与原文严格对应")

    def test_estimated_tokens_ceil(self):
        chunks = chunk_text("你好" * 100, [{"heading": "", "level": 1,
                                            "char_start": 0, "char_end": 200}])
        c = chunks[0]
        self.assertEqual(c.estimated_tokens, max(1, math.ceil(len(c.text) / 1.6)))

    def test_heading_attached(self):
        text, sections = make_doc(40)
        chunks = chunk_text(text, sections)
        # 第一个块归属第一章标题
        self.assertEqual(chunks[0].heading, "第1章")

    def test_empty_text_no_chunks(self):
        self.assertEqual(chunk_text("", []), [])


if __name__ == "__main__":
    unittest.main()
