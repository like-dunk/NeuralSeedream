#!/usr/bin/env python3
"""测试代理连接"""

import httpx

proxy = "http://montage:MontageJKY114@139.224.65.76:7890"

print(f"使用代理: {proxy.split('@')[-1]}")

with httpx.Client(proxy=proxy, timeout=30) as client:
    # 1. 检查出口 IP
    r = client.get("https://api.ipify.org?format=json")
    print(f"出口 IP: {r.json()}")
    
    # 2. 测试 OpenRouter 连接
    r = client.get("https://openrouter.ai/api/v1/models")
    print(f"OpenRouter 状态: {r.status_code}")

print("代理测试通过!")
