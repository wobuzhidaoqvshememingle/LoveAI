# -*- coding: utf-8 -*-
from app.providers.registry import zero_config_entries, FREE_PROVIDER_CATALOG
zc = zero_config_entries()
print(f"catalog total={len(FREE_PROVIDER_CATALOG)}, zero_config={len(zc)}")
print("\n零配置列表 (按 verified_status 排序):")
for e in zc[:20]:
    print(f"  [{e['verified_status']:14s}] {e['name']:20s} {e.get('verified_note','')[:70]}")
