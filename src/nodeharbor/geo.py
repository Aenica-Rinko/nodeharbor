"""Country labels inferred from node names; no network or GeoIP lookup."""


import re


COUNTRY_FLAG = {
    "US": "🇺🇸", "HK": "🇭🇰", "JP": "🇯🇵", "SG": "🇸🇬",
    "KR": "🇰🇷", "TW": "🇹🇼", "CN": "🇨🇳",
    "GB": "🇬🇧", "DE": "🇩🇪", "FR": "🇫🇷", "NL": "🇳🇱",
    "RU": "🇷🇺", "IN": "🇮🇳", "BR": "🇧🇷", "CA": "🇨🇦",
    "AU": "🇦🇺", "SE": "🇸🇪", "VN": "🇻🇳", "TH": "🇹🇭",
    "MY": "🇲🇾", "ID": "🇮🇩", "TR": "🇹🇷", "AE": "🇦🇪",
    "RO": "🇷🇴", "IT": "🇮🇹", "ES": "🇪🇸",
    "AR": "🇦🇷", "CH": "🇨🇭", "AT": "🇦🇹", "BE": "🇧🇪",
    "PL": "🇵🇱", "CZ": "🇨🇿", "DK": "🇩🇰", "FI": "🇫🇮",
    "NO": "🇳🇴", "PT": "🇵🇹", "IE": "🇮🇪", "GR": "🇬🇷",
    "ZA": "🇿🇦", "IL": "🇮🇱", "UA": "🇺🇦", "PH": "🇵🇭",
    "PK": "🇵🇰", "BD": "🇧🇩", "NG": "🇳🇬", "EG": "🇪🇬",
    "MX": "🇲🇽", "CO": "🇨🇴", "CL": "🇨🇱", "PE": "🇵🇪",
    "HU": "🇭🇺", "LV": "🇱🇻", "LT": "🇱🇹", "KZ": "🇰🇿",
    "BG": "🇧🇬", "SK": "🇸🇰", "SI": "🇸🇮", "EE": "🇪🇪",
    "HR": "🇭🇷", "RS": "🇷🇸",
    "IR": "🇮🇷", "MD": "🇲🇩", "EE": "🇪🇪",
    "EC": "🇪🇨", "MT": "🇲🇹", "PA": "🇵🇦",
    "LU": "🇱🇺", "IS": "🇮🇸", "CY": "🇨🇾",
}


COUNTRY_NAME = {
    "AE": "阿联酋", "US": "美国", "HK": "香港", "JP": "日本", "SG": "新加坡",
    "KR": "韩国", "TW": "台湾", "CN": "中国",
    "GB": "英国", "DE": "德国", "FR": "法国", "NL": "荷兰",
    "RU": "俄罗斯", "IN": "印度", "BR": "巴西", "CA": "加拿大",
    "AU": "澳大利亚", "SE": "瑞典", "VN": "越南", "TH": "泰国",
    "MY": "马来西亚", "ID": "印尼", "TR": "土耳其",
    "RO": "罗马尼亚", "IT": "意大利", "ES": "西班牙",
    "AR": "阿根廷", "CH": "瑞士", "AT": "奥地利", "BE": "比利时",
    "PL": "波兰", "CZ": "捷克", "DK": "丹麦", "FI": "芬兰",
    "NO": "挪威", "PT": "葡萄牙", "IE": "爱尔兰", "GR": "希腊",
    "ZA": "南非", "IL": "以色列", "UA": "乌克兰",
    "PH": "菲律宾", "PK": "巴基斯坦",
    "MX": "墨西哥", "CO": "哥伦比亚", "CL": "智利",
    "HU": "匈牙利", "LV": "拉脱维亚", "LT": "立陶宛",
    "KZ": "哈萨克", "BG": "保加利亚", "SK": "斯洛伐克",
    "SI": "斯洛文尼亚", "EE": "爱沙尼亚", "HR": "克罗地亚",
    "RS": "塞尔维亚", "IR": "伊朗", "MD": "摩尔多瓦",
    "EE": "爱沙尼亚", "EC": "厄瓜多尔", "MT": "马耳他",
    "PA": "巴拿马", "LU": "卢森堡", "IS": "冰岛",
    "CY": "塞浦路斯",
}


EMOJI_FLAG_RE = re.compile(r'[\U0001F1E6-\U0001F1FF]{2}')


FLAG_TO_CODE = {
    "🇺🇸": "US", "🇭🇰": "HK", "🇯🇵": "JP", "🇸🇬": "SG",
    "🇰🇷": "KR", "🇹🇼": "TW", "🇨🇳": "CN",
    "🇬🇧": "GB", "🇩🇪": "DE", "🇫🇷": "FR", "🇳🇱": "NL",
    "🇷🇺": "RU", "🇮🇳": "IN", "🇧🇷": "BR", "🇨🇦": "CA",
    "🇦🇺": "AU", "🇸🇪": "SE", "🇻🇳": "VN", "🇹🇭": "TH",
    "🇲🇾": "MY", "🇮🇩": "ID", "🇹🇷": "TR",
    "🇮🇹": "IT", "🇪🇸": "ES", "🇦🇷": "AR", "🇨🇭": "CH",
    "🇦🇹": "AT", "🇧🇪": "BE", "🇵🇱": "PL", "🇨🇿": "CZ",
    "🇩🇰": "DK", "🇫🇮": "FI", "🇳🇴": "NO", "🇵🇹": "PT",
    "🇮🇪": "IE", "🇬🇷": "GR", "🇷🇴": "RO",
    "🇿🇦": "ZA", "🇮🇱": "IL", "🇺🇦": "UA",
    "🇵🇭": "PH", "🇲🇽": "MX",
    "🇦🇪": "AE", "🇹🇷": "TR",
    "🇭🇺": "HU", "🇱🇻": "LV", "🇱🇹": "LT", "🇰🇿": "KZ",
    "🇧🇬": "BG", "🇸🇰": "SK", "🇸🇮": "SI", "🇪🇪": "EE",
    "🇭🇷": "HR", "🇷🇸": "RS",
    "🇮🇷": "IR", "🇲🇩": "MD", "🇪🇪": "EE",
    "🇪🇨": "EC", "🇲🇹": "MT", "🇵🇦": "PA",
    "🇱🇺": "LU", "🇮🇸": "IS", "🇨🇾": "CY",
}


def get_country_from_name(name: str) -> str | None:
    """从节点名称中提取国家信息"""
    # 1. 匹配国旗 emoji
    flags = EMOJI_FLAG_RE.findall(name)
    for flag in flags:
        if flag in FLAG_TO_CODE:
            return FLAG_TO_CODE[flag]

    # 2. 匹配国家代码关键词
    name_upper = name.upper()
    code_keywords = [
        ("HK", "HK"), ("HONG KONG", "HK"), ("HONGKONG", "HK"),
        ("US", "US"), ("USA", "US"), ("UNITED STATES", "US"),
        ("JP", "JP"), ("JAPAN", "JP"), ("TOKYO", "JP"), ("OSAKA", "JP"),
        ("SG", "SG"), ("SINGAPORE", "SG"), ("SINGAPORE", "SG"),
        ("KR", "KR"), ("KOREA", "KR"), ("SEOUL", "KR"),
        ("TW", "TW"), ("TAIWAN", "TW"), ("TAIPEI", "TW"),
        ("CN", "CN"), ("CHINA", "CN"),
        ("DE", "DE"), ("GERMANY", "DE"),
        ("FR", "FR"), ("FRANCE", "FR"),
        ("NL", "NL"), ("NETHERLANDS", "NL"),
        ("RU", "RU"), ("RUSSIA", "RU"),
        ("UK", "GB"), ("GB", "GB"), ("ENGLAND", "GB"), ("LONDON", "GB"),
        ("CA", "CA"), ("CANADA", "CA"),
        ("AU", "AU"), ("AUSTRALIA", "AU"),
        ("SWEDEN", "SE"), ("SE", "SE"),
        ("VIETNAM", "VN"), ("VN", "VN"),
        ("THAILAND", "TH"), ("TH", "TH"),
        ("MALAYSIA", "MY"), ("MY", "MY"),
        ("INDONESIA", "ID"), ("ID", "ID"),
        ("INDIA", "IN"), ("IN", "IN"),
        ("TURKEY", "TR"), ("TR", "TR"),
        ("ITALY", "IT"), ("IT", "IT"),
        ("SPAIN", "ES"), ("ES", "ES"),
        ("BRAZIL", "BR"), ("BR", "BR"),
        ("SWITZERLAND", "CH"), ("CH", "CH"),
        ("ROMANIA", "RO"), ("RO", "RO"),
        ("POLAND", "PL"), ("PL", "PL"),
        ("FINLAND", "FI"), ("FI", "FI"),
        ("NORWAY", "NO"), ("NO", "NO"),
        ("UAE", "AE"), ("DUBAI", "AE"),
        ("IRAN", "IR"), ("IR", "IR"),
        ("MOLDOVA", "MD"), ("MD", "MD"),
        ("ESTONIA", "EE"), ("EE", "EE"),
        ("ECUADOR", "EC"), ("EC", "EC"),
        ("MALTA", "MT"), ("MT", "MT"),
        ("PANAMA", "PA"), ("PA", "PA"),
        ("BULGARIA", "BG"), ("BG", "BG"),
        ("SLOVAKIA", "SK"), ("SK", "SK"),
        ("SLOVENIA", "SI"), ("SI", "SI"),
        ("CROATIA", "HR"), ("HR", "HR"),
        ("SERBIA", "RS"), ("RS", "RS"),
        ("KAZAKHSTAN", "KZ"), ("KZ", "KZ"),
        ("HUNGARY", "HU"), ("HU", "HU"),
        ("LATVIA", "LV"), ("LV", "LV"),
        ("LITHUANIA", "LT"), ("LT", "LT"),
    ]
    for keyword, code in code_keywords:
        if re.search(r"(?<![A-Z])" + re.escape(keyword) + r"(?![A-Z])", name_upper):
            return code

    # 3. 中文国家名（简体 + 繁体）
    cn_map = {
        # 简繁共用
        "香港": "HK", "日本": "JP", "新加坡": "SG",
        "韩国": "KR", "韓國": "KR", "台湾": "TW", "台灣": "TW",
        "中国": "CN", "中國": "CN",
        "印度": "IN", "巴西": "BR", "加拿大": "CA",
        "瑞典": "SE", "越南": "VN", "土耳其": "TR",
        "意大利": "IT", "西班牙": "ES", "瑞士": "CH",
        "阿根廷": "AR", "挪威": "NO",
        # 简体 → 繁体
        "美国": "US", "美國": "US",
        "英国": "GB", "英國": "GB",
        "德国": "DE", "德國": "DE",
        "法国": "FR", "法國": "FR",
        "荷兰": "NL", "荷蘭": "NL",
        "俄罗斯": "RU", "俄羅斯": "RU",
        "澳大利亚": "AU", "澳洲": "AU", "澳大利亞": "AU",
        "泰国": "TH", "泰國": "TH",
        "罗马尼亚": "RO", "羅馬尼亞": "RO",
        "波兰": "PL", "波蘭": "PL",
        "芬兰": "FI", "芬蘭": "FI",
        "奥地利": "AT", "奧地利": "AT",
        "捷克": "CZ",
        # 新增
        "匈牙利": "HU",
        "拉脱维亚": "LV", "拉脫維亞": "LV",
        "立陶宛": "LT",
        "哈萨克": "KZ", "哈薩克": "KZ",
        "丹麦": "DK", "丹麥": "DK",
        "比利时": "BE", "比利時": "BE",
        "葡萄牙": "PT",
        "爱尔兰": "IE", "愛爾蘭": "IE",
        "希腊": "GR", "希臘": "GR",
        "南非": "ZA",
        "乌克兰": "UA", "烏克蘭": "UA",
        "菲律宾": "PH", "菲律賓": "PH",
        "墨西哥": "MX",
        "印尼": "ID", "印度尼西亚": "ID", "印度尼西亞": "ID",
        "马来西亚": "MY", "馬來西亞": "MY",
        "以色列": "IL",
        "阿联酋": "AE", "阿聯酋": "AE", "迪拜": "AE",
        "伊朗": "IR",
        "摩尔多瓦": "MD", "摩爾多瓦": "MD",
        "爱沙尼亚": "EE", "愛沙尼亞": "EE",
        "厄瓜多尔": "EC", "厄瓜多爾": "EC",
        "保加利亚": "BG", "保加利亞": "BG",
        "斯洛伐克": "SK",
        "斯洛文尼亚": "SI", "斯洛文尼亞": "SI",
        "克罗地亚": "HR", "克羅地亞": "HR",
        "塞尔维亚": "RS",
        "卢森堡": "LU", "盧森堡": "LU",
        "冰岛": "IS", "冰島": "IS",
        "塞浦路斯": "CY",
    }
    for cn_name, code in sorted(cn_map.items(), key=lambda item: -len(item[0])):
        if cn_name in name:
            return code

    # 4. 国家代码前缀 (如 KZ, LV, LT 开头)
    code_prefix_re = re.compile(r'^([A-Z]{2})\b')
    m = code_prefix_re.match(name.strip())
    if m:
        code = m.group(1)
        # 验证是已知的国家代码
        if code in COUNTRY_NAME:
            return code

    return None
