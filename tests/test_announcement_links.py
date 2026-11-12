from api.datasources.announcement_links import merge_official_announcement_urls


def test_missing_ifind_urls_match_the_official_cninfo_title():
    primary = [{
        "title": "贵州茅台：贵州茅台关于公司高级管理人员2026年度薪酬兑现方案的公告",
        "date": "2026-10-31",
        "url": None,
    }]
    official = [{
        "title": "贵州茅台关于公司高级管理人员2026年度薪酬兑现方案的公告",
        "date": "2026-10-31",
        "url": "https://www.cninfo.com.cn/new/disclosure/detail?annoId=sample",
    }]

    result = merge_official_announcement_urls(primary, official)

    assert result[0]["url"] == official[0]["url"]
    assert result[0]["date"] == primary[0]["date"]


def test_unmatched_announcement_keeps_missing_url_explicit():
    primary = [{"title": "尚无对应公告", "date": "2026-10-31", "url": None}]

    assert merge_official_announcement_urls(primary, []) == primary
