#!/usr/bin/env python3
"""盘前外盘数据获取脚本"""
import urllib.request
import json

def get_sina_us_data():
    """获取新浪财经美股数据"""
    symbols = ['gb_ixic', 'gb_dji', 'gb_inx', 'gb_baba', 'gb_bidu', 'gb_jd', 'gb_pdd', 'gb_nio']
    url = 'https://hq.sinajs.cn/list=' + ','.join(symbols)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'http://finance.sina.com.cn'
    })
    
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read().decode('gbk', errors='replace')
    
    results = []
    for line in data.strip().split('\n'):
        if '=' in line:
            content = line.split('=\"')[1].strip('\";')
            fields = content.split(',')
            if len(fields) > 3:
                results.append({
                    'name': fields[0],
                    'price': fields[1],
                    'pct': fields[2],
                })
    return results

def generate_report():
    """生成市场情绪报告"""
    data = get_sina_us_data()
    
    # 分类
    indices = ['纳斯达克', '道琼斯', '标普500指数']
    china_stocks = ['阿里巴巴', '百度', '京东', '拼多多', '蔚来']
    
    print('=' * 50)
    print('盘前外盘市场情绪报告')
    print('=' * 50)
    print()
    
    # 美股指数
    print('【美股三大指数】')
    for item in data:
        if item['name'] in indices:
            pct = float(item['pct'])
            emoji = '🟢' if pct > 0 else '🔴' if pct < 0 else '⚪'
            print(f"  {emoji} {item['name']}: {item['price']} ({item['pct']}%)")
    print()
    
    # 中概股
    print('【中概股】')
    for item in data:
        if item['name'] in china_stocks:
            pct = float(item['pct'])
            emoji = '🟢' if pct > 0 else '🔴' if pct < 0 else '⚪'
            print(f"  {emoji} {item['name']}: {item['price']} ({item['pct']}%)")
    print()
    
    # 市场情绪判断
    avg_pct = sum(float(item['pct']) for item in data if item['name'] not in ['京东']) / len([item for item in data if item['name'] not in ['京东']])
    
    if avg_pct > 1:
        sentiment = '乐观 🟢'
        prediction = 'A股大概率高开，关注科技/互联网板块'
    elif avg_pct > 0:
        sentiment = '中性偏乐观 ⚪'
        prediction = 'A股大概率高开或平开'
    else:
        sentiment = '谨慎 🔴'
        prediction = 'A股可能承压，注意防御'
    
    print('【市场情绪】' + sentiment)
    print(f'【A股预判】{prediction}')
    print()
    print('=' * 50)

if __name__ == '__main__':
    generate_report()
