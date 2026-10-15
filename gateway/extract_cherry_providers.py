# -*- coding: utf-8 -*-
"""从Cherry Studio源码批量提取provider配置，转换成Python格式"""
import os
import re
import json

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROVIDERS_DIR = os.path.join(_PROJECT_ROOT, "cherry-studio-src", "cherry-studio-main",
                             "packages", "provider-registry", "src", "providers")

def parse_provider_file(filepath):
    """解析单个provider TS文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    result = {}
    
    # 提取id
    m = re.search(r"id:\s*'([^']+)'", content)
    if m:
        result['id'] = m.group(1)
    
    # 提取name
    m = re.search(r"name:\s*'([^']+)'", content)
    if m:
        result['name'] = m.group(1)
    
    # 提取baseUrl
    m = re.search(r"baseUrl:\s*'([^']+)'", content)
    if m:
        result['base_url'] = m.group(1)
    
    # 提取defaultChatEndpoint
    m = re.search(r"defaultChatEndpoint:\s*'([^']+)'", content)
    if m:
        result['default_endpoint'] = m.group(1)
    
    # 提取adapterFamily
    m = re.search(r"adapterFamily:\s*'([^']+)'", content)
    if m:
        result['adapter_family'] = m.group(1)
    
    return result

# 批量处理
providers = []
for filename in os.listdir(PROVIDERS_DIR):
    if filename.endswith('.ts') and not filename.startswith('__') and filename not in ['types.ts', 'parameterSupports.ts', 'wires.ts']:
        filepath = os.path.join(PROVIDERS_DIR, filename)
        try:
            p = parse_provider_file(filepath)
            if p.get('id') and p.get('base_url'):
                providers.append(p)
                print(f"  ✅ {p['id']}: {p['name']} -> {p['base_url']}")
        except Exception as e:
            print(f"  ❌ {filename}: {e}")

print(f"\n总共提取了 {len(providers)} 个provider")

# 保存成JSON
output_path = os.path.join(_PROJECT_ROOT, "gateway", "app", "data", "cherry_providers.json")
os.makedirs(os.path.dirname(output_path), exist_ok=True)
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(providers, f, ensure_ascii=False, indent=2)

print(f"已保存到: {output_path}")
