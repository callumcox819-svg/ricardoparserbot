from __future__ import annotations

import re
from urllib.parse import urlparse


def parse_playwright_proxy(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None

    raw = proxy_url.strip()
    if not raw:
        return None

    # host:port:user:pass
    host_port_user_pass = re.match(
        r"^(?P<host>[^:]+):(?P<port>\d+):(?P<user>[^:]+):(?P<password>.+)$",
        raw,
    )
    if host_port_user_pass:
        groups = host_port_user_pass.groupdict()
        return {
            "server": f"http://{groups['host']}:{groups['port']}",
            "username": groups["user"],
            "password": groups["password"],
        }

    # user:pass@host:port
    if "@" in raw and "://" not in raw:
        raw = f"http://{raw}"

    if "://" not in raw:
        parts = raw.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            return {"server": f"http://{parts[0]}:{parts[1]}"}
        raise ValueError(
            "PROXY_URL must look like http://user:pass@host:port or host:port:user:pass"
        )

    parsed = urlparse(raw)
    if not parsed.hostname or not parsed.port:
        raise ValueError(
            "PROXY_URL must look like http://user:pass@host:port or host:port:user:pass"
        )

    scheme = parsed.scheme or "http"
    proxy: dict[str, str] = {"server": f"{scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy
