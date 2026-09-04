# @author: liuqinhe
"""待签字符串校验：不接触密钥，覆盖知识库 SIGN_INVALID 四条排查要点中的三条。"""

import time

from app.tools.signature import (
    build_canonical,
    check_encoding,
    check_timestamp,
    diff_canonical,
    strip_secrets,
)


def _codes(issues):
    return [i["code"] for i in issues]


# ---------- 待签字符串构造（知识库 02 节步骤 1-4）----------

def test_按字典序拼接并追加secret占位符():
    canonical, keys = build_canonical(
        {"name": "张三", "idcard": "110101199001011234"},
        timestamp="1718000000",
        api_key="ak_live_xxx",
    )
    assert keys == ["api_key", "idcard", "name", "timestamp"]
    assert canonical == (
        "api_key=ak_live_xxx&idcard=110101199001011234&name=张三&timestamp=1718000000&secret=***"
    )


def test_secret永不出现真实值():
    canonical, _ = build_canonical({"a": "1"}, timestamp="1718000000", api_key=None)
    assert canonical.endswith("&secret=***")


def test_api_key缺省时不进入待签串():
    _, keys = build_canonical({"a": "1"}, timestamp="1718000000", api_key=None)
    assert keys == ["a", "timestamp"]


# ---------- 误贴密钥 ----------

def test_剔除误贴的密钥并告警():
    cleaned, issues = strip_secrets(
        {"name": "张三", "secret": "sk_real", "sign": "abc", "signature": "d", "secret_key": "e"}
    )
    assert cleaned == {"name": "张三"}
    assert "SECRET_LEAKED_IN_PARAMS" in _codes(issues)


def test_未贴密钥时无告警():
    cleaned, issues = strip_secrets({"name": "张三"})
    assert cleaned == {"name": "张三"} and issues == []


# ---------- 时间戳（文档：秒级、偏差 ≤ 5 分钟）----------

def test_毫秒级时间戳被指出():
    assert "TIMESTAMP_UNIT" in _codes(check_timestamp("1718000000123", now=1718000000))


def test_非数字时间戳被指出():
    assert "TIMESTAMP_NOT_NUMERIC" in _codes(check_timestamp("2024-06-10", now=1718000000))


def test_超出5分钟窗口被指出():
    issues = check_timestamp(str(1718000000 - 301), now=1718000000)
    assert "TIMESTAMP_SKEW" in _codes(issues)


def test_窗口内不报错():
    assert check_timestamp(str(1718000000 - 299), now=1718000000) == []


def test_默认用当前时间():
    assert check_timestamp(str(int(time.time()))) == []


# ---------- URL 编码 ----------

def test_检测出url编码的值():
    issues = check_encoding({"name": "%E5%BC%A0%E4%B8%89"})
    assert "VALUE_URL_ENCODED" in _codes(issues)
    assert "name" in issues[0]["detail"]


def test_正常值不报编码问题():
    assert check_encoding({"name": "张三", "rate": "99%"}) == []


# ---------- 与用户自拼串 diff ----------

def test_顺序错误定位到第几个参数():
    issues = diff_canonical(
        ["api_key", "idcard", "name", "timestamp"],
        "api_key=x&name=张三&idcard=110&timestamp=1718000000&secret=***",
    )
    order = [i for i in issues if i["code"] == "ORDER_MISMATCH"]
    assert order and "第 2 个" in order[0]["detail"]


def test_缺少参数被指出():
    issues = diff_canonical(
        ["api_key", "idcard", "timestamp"], "api_key=x&timestamp=1718000000&secret=***"
    )
    missing = [i for i in issues if i["code"] == "PARAM_MISSING"]
    assert missing and "idcard" in missing[0]["detail"]


def test_多余参数被指出():
    issues = diff_canonical(
        ["api_key", "timestamp"], "api_key=x&debug=1&timestamp=1718000000&secret=***"
    )
    extra = [i for i in issues if i["code"] == "PARAM_EXTRA"]
    assert extra and "debug" in extra[0]["detail"]


def test_漏掉secret后缀被指出():
    issues = diff_canonical(["api_key", "timestamp"], "api_key=x&timestamp=1718000000")
    assert "SECRET_SUFFIX_MISSING" in _codes(issues)


def test_完全正确时无问题():
    assert diff_canonical(
        ["api_key", "timestamp"], "api_key=x&timestamp=1718000000&secret=***"
    ) == []
