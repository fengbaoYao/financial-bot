"""The public web is the slow path; structured providers come first."""

from api.comparison_research import brief_needs_web_search, investor_brief_tools


def test_structured_question_does_not_require_web_search():
    # Quotes, filings, fundamentals and announcements are all served by
    # iFinD/Fuyao/SEC/news feeds, so no web search should be forced.
    for query in [
        "帮我看看腾讯现在多少钱",
        "分析一下 AAPL 的财报",
        "茅台最近有什么消息",
        "比较一下 MSFT 和 AAPL",
        "贵州茅台估值高不高",
    ]:
        tools = investor_brief_tools(query, us_company=False)
        assert "web_search" not in tools, query


def test_explicit_web_request_keeps_web_search():
    for query in [
        "联网查一下这家公司的官网说明",
        "搜一下监管政策原文",
        "帮我查外部资料确认这条新闻",
        "browse the official site for their press release",
    ]:
        assert "web_search" in investor_brief_tools(query, us_company=False), query


def test_quote_questions_still_need_price_data():
    tools = investor_brief_tools("腾讯股价最近涨跌如何", us_company=False)
    assert "get_stock_analysis" in tools
    assert "web_search" not in tools


def test_us_company_keeps_sec_but_not_web():
    tools = investor_brief_tools("分析一下英伟达的财报", us_company=True)
    assert "get_sec_company_data" in tools
    assert "web_search" not in tools


def test_detector_matches_only_explicit_web_intent():
    assert brief_needs_web_search("搜一下这家公司的官网")
    assert not brief_needs_web_search("腾讯股价多少")
