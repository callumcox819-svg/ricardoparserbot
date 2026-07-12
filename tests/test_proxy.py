from parser.proxy import parse_playwright_proxy


def test_host_port_user_pass_format():
    proxy = parse_playwright_proxy("proxy.lomaproxy.com:38175:fVLm8A9YP4:0NH53d4bHa")
    assert proxy == {
        "server": "http://proxy.lomaproxy.com:38175",
        "username": "fVLm8A9YP4",
        "password": "0NH53d4bHa",
    }


def test_standard_url_format():
    proxy = parse_playwright_proxy("http://fVLm8A9YP4:0NH53d4bHa@proxy.lomaproxy.com:38175")
    assert proxy["server"] == "http://proxy.lomaproxy.com:38175"
    assert proxy["username"] == "fVLm8A9YP4"
    assert proxy["password"] == "0NH53d4bHa"
