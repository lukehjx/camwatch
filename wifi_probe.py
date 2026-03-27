#!/usr/bin/env python3
"""
WiFi 探针模块 v2 - 通过 SSH 从 H3C 路由器获取 ARP 表
直接查询路由器 192.168.23.x 网段的在线设备，比本地 ping 扫描准确
"""

import subprocess
import time
import re
import json
import os
import logging

log = logging.getLogger(__name__)

# ─── 配置 ─────────────────────────────────────────────────────────────────────
ROUTER_HOST    = "192.168.100.1"
ROUTER_USER    = "admin"
ROUTER_PASS    = "Sidex@123456"
WIFI_SUBNET    = "192.168.23"       # 只统计这个网段
CACHE_FILE     = "/root/camwatch/wifi_probe_cache.json"
CACHE_TTL      = 90                 # 秒

# H3C SSH 兼容参数
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=8",
    "-o", "KexAlgorithms=+diffie-hellman-group1-sha1,diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1",
    "-o", "HostKeyAlgorithms=+ssh-rsa,ssh-dss",
    "-o", "Ciphers=+aes128-cbc,3des-cbc,aes192-cbc,aes256-cbc,aes128-ctr",
]

# 固定设备 IP（排除，不算人员）
FIXED_IPS = {
    "192.168.23.1",    # 路由器网关
}


def _fetch_arp_from_router() -> list:
    """SSH 到 H3C 路由器，执行 display arp，解析 23 网段设备列表"""
    cmd = ["sshpass", "-p", ROUTER_PASS, "ssh"] + SSH_OPTS + [
        f"{ROUTER_USER}@{ROUTER_HOST}",
        f"display arp | include {WIFI_SUBNET}"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        log.warning("[WiFi探针] SSH 超时")
        return []
    except Exception as e:
        log.warning(f"[WiFi探针] SSH 失败: {e}")
        return []

    # 解析 H3C ARP 输出格式：
    # 192.168.23.10  341c-f0db-896e 22  GE1/0/14  130  D
    devices = []
    for line in output.splitlines():
        line = line.strip()
        # 匹配 IP 开头的行
        m = re.match(r'^(192\.168\.23\.\d+)\s+([0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4})', line)
        if not m:
            continue
        ip  = m.group(1)
        mac_raw = m.group(2)
        # 统一转成 xx:xx:xx:xx:xx:xx 格式
        mac = ":".join(
            part for segment in mac_raw.split("-") for part in [segment[:2], segment[2:]]
        )
        devices.append({"ip": ip, "mac": mac})

    return devices


def scan_wifi(use_cache: bool = True) -> dict:
    """
    扫描 WiFi 在线设备，返回结果字典
    """
    # 检查缓存
    if use_cache and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
            age = time.time() - cache.get("scan_ts", 0)
            if age < CACHE_TTL:
                cache["from_cache"] = True
                cache["cache_age_seconds"] = int(age)
                return cache
        except Exception:
            pass

    start = time.time()
    log.info(f"[WiFi探针] SSH 查询路由器 ARP 表...")

    all_devices_raw = _fetch_arp_from_router()
    all_devices  = []
    mobile_devices = []

    for d in all_devices_raw:
        ip = d["ip"]
        is_fixed = ip in FIXED_IPS
        entry = {**d, "is_fixed": is_fixed, "desc": "路由器/网关" if is_fixed else ""}
        all_devices.append(entry)
        if not is_fixed:
            mobile_devices.append(entry)

    elapsed = round(time.time() - start, 1)
    log.info(f"[WiFi探针] 完成，耗时 {elapsed}s，总在线: {len(all_devices)}，移动设备: {len(mobile_devices)}")

    result = {
        "total_online":      len(all_devices),
        "estimated_people":  len(mobile_devices),
        "devices":           sorted(all_devices, key=lambda x: tuple(int(p) for p in x["ip"].split("."))),
        "mobile_devices":    sorted(mobile_devices, key=lambda x: tuple(int(p) for p in x["ip"].split("."))),
        "scan_time":         __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scan_elapsed":      elapsed,
        "from_cache":        False,
        "scan_ts":           time.time(),
        "source":            "h3c_router_ssh",
    }

    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"写缓存失败: {e}")

    return result


def get_presence_hint(use_cache: bool = True) -> str:
    """给 AI Prompt 用的辅助信息"""
    try:
        result = scan_wifi(use_cache=use_cache)
        n = result["estimated_people"]
        devices = result["mobile_devices"]
        ips = ", ".join(d["ip"] for d in devices[:8])
        more = f"等{len(devices)}台" if len(devices) > 8 else f"{len(devices)}台"

        if n == 0:
            hint = "【WiFi探针】未检测到任何移动设备在线，推断办公室当前无人。"
        elif n <= 5:
            hint = f"【WiFi探针】检测到 {more} 移动设备在线（{ips}），推断办公室有少量人员（约{n}人）。"
        else:
            hint = f"【WiFi探针】检测到 {more} 移动设备在线（{ips}），推断办公室有较多人员在场（约{n}人）。"

        if result.get("from_cache"):
            age = result.get("cache_age_seconds", 0)
            hint += f"（数据缓存自 {age}s 前）"

        return hint
    except Exception as e:
        log.warning(f"WiFi探针失败: {e}")
        return ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    import sys
    use_cache = "--no-cache" not in sys.argv
    result = scan_wifi(use_cache=use_cache)
    print(json.dumps({
        "total_online": result["total_online"],
        "estimated_people": result["estimated_people"],
        "scan_time": result["scan_time"],
        "scan_elapsed": result["scan_elapsed"],
        "from_cache": result["from_cache"],
        "source": result.get("source"),
    }, ensure_ascii=False, indent=2))
    print()
    print(get_presence_hint(use_cache=False))
