"""Web reference regressions: deterministic fixtures, no network."""
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, MagicMock, patch
from app.reporting import route_admin as ra


class ParagraphRecoveryTests(unittest.TestCase):
    def client(self, html):
        client = Mock()
        client.get.return_value = Mock(text=html, headers={"content-type": "text/html"})
        return client

    def test_preserves_inline_markup_and_chinese_punctuation(self):
        text = "腰椎退变主要表现为椎间盘信号减低，椎间隙变窄；还应描述椎管、侧隐窝及神经根受压情况。"
        client = self.client("<nav>菜单噪声</nav><p>腰椎退变主要表现为<b>椎间盘信号减低</b>，椎间隙变窄；还应描述椎管、侧隐窝及神经根受压情况。</p><script>噪声</script>")
        actual = ra._complete_snippet("腰椎退变主要表现为椎间盘信号减低, ...", "https://example.com/a", client)
        self.assertEqual(actual, text)

    def test_mid_snippet_ellipsis_can_recover_paragraph(self):
        text = "腰椎间盘的结构评估应逐节段描述，结合形态及信号改变评估退变程度，并检查周围结构。"
        actual = ra._complete_snippet("腰椎间盘的结构评估应逐节段描述...周围结构", "https://example.com/a", self.client("<p>"+text+"</p>"))
        self.assertEqual(actual, text)

    def test_failed_or_unmatched_pages_keep_original_summary(self):
        original = "腰椎检查的摘要..."
        client = Mock()
        client.get.side_effect = RuntimeError("network unavailable")
        self.assertEqual(ra._complete_snippet(original, "https://example.com/a", client), original)
        self.assertEqual(ra._complete_snippet(original, "", client), original)
        self.assertEqual(ra._complete_snippet(original, "https://example.com/a", self.client("<p>无关内容</p>")), original)

    def test_long_paragraph_is_not_cut(self):
        text = "腰椎影像学检查的描述要素：" + "检查要素及相关改变。" * 160
        actual = ra._complete_snippet("腰椎影像学检查的描述要素...", "https://example.com/a", self.client("<p>"+text+"</p>"))
        self.assertEqual(actual, text)
        self.assertGreater(len(actual), 1200)

    def test_single_relevant_result_is_not_lost(self):
        with patch.object(ra, "_ctx_budgets", return_value={"doc_max_chars": 10000}):
            text, matched, filtered = ra._select_relevant("腰椎的影像学报告要素", "腰椎")
        self.assertEqual((matched, filtered), (1, 0))
        self.assertIn("腰椎", text)


class WebReferenceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(ra, "_WEB_FACETS", [("one", "书写要素", "要素"), ("two", "分级", "分级")]))
        self.stack.enter_context(patch.object(ra, "_WEB_PROVIDERS", ["test"]))
        self.stack.enter_context(patch.object(ra, "_web_provider_cache", {"name": "", "ts": 0}))
        self.stack.enter_context(patch.object(ra, "_ctx_budgets", return_value={"doc_max_chars":100000,"web_search_max_chars":100000,"final_ref_max_chars":100000}))
        self.client = MagicMock()
        self.client.__enter__.return_value = self.client
        self.stack.enter_context(patch("httpx.Client", return_value=self.client))
        self.search = self.stack.enter_context(patch.object(ra, "_web_search"))
        self.pages = self.stack.enter_context(patch.object(ra, "_reference_page_paragraphs", return_value=[]))

    def test_only_complete_source_paragraphs_are_used(self):
        full = "腰椎退变主要表现为椎间盘信号改变，应详细记录改变节段，并说明椎管与神经根的受压情况。"
        self.search.side_effect = lambda *a: [
            {"snippet":"腰椎退变主要表现...","url":"https://example.com/a"},
            {"snippet":"腰椎其他摘要...","url":"https://example.com/b"}]
        self.pages.side_effect = lambda url, client: [full] if url.endswith("/a") else []
        result = ra.web_search_reference("腰椎")
        self.assertIn(full, result["text"])
        self.assertNotIn("腰椎其他摘要", result["text"])
        self.assertNotIn("未补全", result["text"])
        self.assertNotIn("...", result["text"])
        self.assertEqual(result["summary_count"], 0)
        self.assertEqual(result["expanded_count"], 1)
        self.assertEqual(result["matched_lines"], 1)
        self.assertEqual(self.pages.call_count, 2)
        self.assertEqual(result["chars"], len(result["text"]))

    def test_no_complete_source_raises_instead_of_returning_summaries(self):
        self.search.side_effect = lambda *a: [{"snippet":"腰椎摘要...","url":"https://example.com/a"}]
        with self.assertRaisesRegex(ValueError, "未填入搜索摘要"):
            ra.web_search_reference("腰椎")

    def test_provider_fallback_finds_full_source(self):
        self.stack.enter_context(patch.object(ra, "_WEB_PROVIDERS", ["first","second"]))
        self.search.side_effect = lambda query, client, provider: [
            {"snippet":"腰椎摘要...","url":"https://example.com/"+provider}]
        full = "腰椎的影像报告需要完整描述病变部位、形态和信号，以及周围结构的表现，并按相关规范记录诊断意见。"
        self.pages.side_effect = lambda url, client: [full] if url.endswith("/second") else []
        result = ra.web_search_reference("腰椎")
        self.assertEqual(result["expanded_count"], 1)
        self.assertIn(full, result["text"])
        self.assertEqual(self.pages.call_count, 2)

    def test_page_requests_remain_bounded_when_all_fail(self):
        self.stack.enter_context(patch.object(ra, "_WEB_FACETS", [(str(i),str(i),str(i)) for i in range(4)]))
        self.stack.enter_context(patch.object(ra, "_WEB_PROVIDERS", ["a","b","c","d"]))
        self.search.side_effect = lambda query, client, provider: [
            {"snippet":"腰椎摘要...","url":"https://example.com/"+str(hash(query))+provider+str(i)} for i in range(16)]
        with self.assertRaisesRegex(ValueError, "完整原文"):
            ra.web_search_reference("腰椎")
        self.assertEqual(self.pages.call_count, 24)

    def test_capacity_omits_whole_paragraphs_without_cutting(self):
        first = "腰椎原文甲。" * 12
        second = "腰椎原文乙。" * 12
        records = [{"facet":"书写要素","url":"https://example.com/a","text":first},
                   {"facet":"分级分型","url":"https://example.com/b","text":second}]
        one = len(ra._pack_full_references(records[:1], 10000)[0])
        text, accepted, omitted = ra._pack_full_references(records, one)
        self.assertIn(first, text)
        self.assertNotIn(second, text)
        self.assertEqual(len(text), one)
        self.assertEqual((len(accepted), omitted), (1,1))
        self.assertNotIn("已截断", text)

    def test_paragraph_that_already_has_ellipsis_is_excluded(self):
        text = "腰椎原文需要说明这些内容。" * 5
        actual = ra._full_reference_paragraphs([text, text+"..."], "腰椎")
        self.assertEqual(actual, [text])

    def test_empty_search_has_clear_error(self):
        self.search.return_value = []
        with self.assertRaisesRegex(ValueError, "完整原文"):
            ra.web_search_reference("腰椎")


class ReferenceCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_draft_rejects_over_limit_without_calling_model(self):
        from unittest.mock import AsyncMock
        from app.reporting import kb_reference
        with patch.object(ra, "_ctx_budgets", return_value={"final_ref_max_chars":30}), \
             patch.object(ra, "chat_stream", new=AsyncMock()) as model, \
             patch.object(kb_reference, "kb_reference_for_route") as retrieve:
            with self.assertRaisesRegex(ValueError, "31.*30"):
                await ra.generate_route_draft("腰椎", body_part="腰椎", reference_text="甲"*31)
            model.assert_not_awaited()
            retrieve.assert_not_called()

    async def test_exact_limit_is_sent_in_full(self):
        from unittest.mock import AsyncMock
        from app.reporting import kb_reference
        reference = "甲"*28 + "😀中"
        with patch.object(ra, "_ctx_budgets", return_value={"final_ref_max_chars":30}), \
             patch.object(ra, "chat_stream", new=AsyncMock(return_value='{"prompt_md":"test"}')) as model, \
             patch.object(kb_reference, "kb_reference_for_route", return_value={"count":0}):
            await ra.generate_route_draft("腰椎", body_part="腰椎", reference_text=reference)
            self.assertIn(reference, model.call_args.kwargs["messages"][1]["content"])

    async def test_old_incomplete_summary_cannot_be_submitted(self):
        with self.assertRaisesRegex(ValueError, "旧版未补全摘要"):
            await ra.generate_route_draft("腰椎", body_part="腰椎", reference_text="【搜索摘要（未补全）】腰椎...")


if __name__ == "__main__":
    unittest.main()
