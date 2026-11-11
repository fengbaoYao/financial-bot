from api.datasources.announcement_text import is_noisy_announcement_title, sanitize_announcement_title


RAW = (
    "00700, 05093, 05094, 05542, 05962, 05963, 40240, 40241, 40242, 40470, "
    "TENCENT N2801, TENCENT N3801, TENCENT N3507, Publication of Pricing of "
    "US$1,300,000,000 6.10% Senior Notes Due 2036 and US$500,000,000 6.75% "
    "Senior Notes Due 2046 under the Global Medium Term Note Programme"
)


def test_sanitize_mtn_code_soup_zh():
    assert is_noisy_announcement_title(RAW)
    title = sanitize_announcement_title(RAW, language="zh")
    assert "00700, 05093" not in title
    assert "TENCENT N2801" not in title
    assert "腾讯" in title
    assert "票据" in title or "Senior Notes" in title
    assert len(title) <= 160


def test_sanitize_keeps_clean_chinese_title():
    clean = "腾讯控股有限公司刊发全球中期票据计划定价公告"
    assert sanitize_announcement_title(clean) == clean
    assert not is_noisy_announcement_title(clean)
