#!/usr/bin/env python3
import requests
import re

codes = ['300400', '300552', '002837', '300613', '603876', '600900', '600845']
prefixed = []
for c in codes:
    if c.startswith(('5','6','7','9')):
        prefixed.append(f'sh{c}')
    else:
        prefixed.append(f'sz{c}')

url = 'http://hq.sinajs.cn/list=' + ','.join(prefixed)
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Referer': 'http://finance.sina.com.cn'}
resp = requests.get(url, headers=headers, timeout=10)
resp.encoding = 'gbk'

print("="*60)
print("集合竞价实时数据 (09:23)")
print("="*60)
for line in resp.text.split('\n'):
    m = re.search(r'hq_str_(\w+)="(.+?)"', line)
    if not m:
        continue
    code = m.group(1).replace('sh','').replace('sz','')
    parts = m.group(2).split(',')
    if len(parts) < 32:
        continue
    name = parts[0]
    curr = float(parts[3]) if parts[3] not in ('0', '') else 0
    prev = float(parts[2]) if parts[2] else 0
    ref_price = float(parts[1]) if parts[1] not in ('0', '') else prev
    high_lim = float(parts[4]) if parts[4] else 0
    low_lim = float(parts[5]) if parts[5] else 0
    vol = parts[8] if len(parts) > 8 else '0'
    time_str = parts[31] if len(parts) > 31 else ''

    if curr == 0:
        curr = ref_price
    pct = (curr - prev) / prev * 100 if prev > 0 else 0

    # 判断是否在买入区间
    buy_low_map = {'300400': 27.50, '300552': 26.50, '002837': 98.00, '300613': 54.00, '603876': 29.50, '600900': 25.00, '600845': 22.00}
    buy_high_map = {'300400': 29.00, '300552': 27.50, '002837': 102.00, '300613': 72.00, '603876': 35.00, '600900': 30.00, '600845': 30.00}
    in_zone = ""
    if code in buy_low_map and buy_low_map[code] <= curr <= buy_high_map[code]:
        in_zone = " [买入区间!]"
    elif code in buy_low_map and curr < buy_low_map[code]:
        in_zone = " [低于买入区间]"
    elif code in buy_low_map and curr > buy_high_map[code]:
        in_zone = " [高于买入区间]"

    print(f"{name}({code}): 现价{curr:.2f} 昨收{prev:.2f} ({pct:+.2f}%) 时间:{time_str}{in_zone}")

print("="*60)
