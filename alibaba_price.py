#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alibaba_price.py — фетчер цен Alibaba через Showroom-эндпоинт.

Построен на РАБОЧЕЙ механике из alibaba_showroom_parser.py (которая достала
27 товаров с сервера): постоянная сессия curl_cffi Session(impersonate=chrome120)
+ полные браузерные заголовки + извлечение window._PAGE_DATA_. Добавлены ретраи
(Alibaba пускает «через раз» — несколько попыток ловят удачное окно) и
самопроверка разбора.

search(keyword) -> (список товаров, номер удачной попытки)
Каждый товар: {title, usd_min, usd_max, price_str, moq, country, url, product_id}

    python alibaba_price.py "drill bosch"
    python alibaba_price.py --selftest
"""
import re
import sys
import json
import time
from urllib.parse import quote

try:
    from curl_cffi import requests as curl_requests
except Exception:
    curl_requests = None
try:
    import requests as plain_requests
except Exception:
    plain_requests = None

BASE_URL = "https://www.alibaba.com/showroom/{keyword}.html"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache',
    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
}


class AlibabaPriceFetcher:
    def __init__(self, impersonate="chrome120"):
        self.use_curl = False
        if curl_requests is not None:
            self.session = curl_requests.Session(impersonate=impersonate)
            self.use_curl = True
        elif plain_requests is not None:
            self.session = plain_requests.Session()
            self.session.headers.update(HEADERS)
        else:
            raise ImportError("Нужен curl_cffi или requests")

    def _get(self, url):
        try:
            if self.use_curl:
                r = self.session.get(url, headers=HEADERS, timeout=30)
            else:
                r = self.session.get(url, timeout=30)
            return r.text
        except Exception:
            return None

    @staticmethod
    def _extract_page_data(html):
        # сначала твои паттерны
        for pat in (r'window\._PAGE_DATA_\s*=\s*(\{.*?\});?\s*(?:</script>|window\.)',
                    r'window\._PAGE_DATA_\s*=\s*(\{.*?\})\s*;?\s*</script>'):
            m = re.search(pat, html, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
        # запасной разбор по балансу скобок (если паттерн не сработал)
        m = re.search(r'_PAGE_DATA_\s*=\s*\{', html)
        if m:
            start = m.end() - 1
            depth = 0
            for i in range(start, len(html)):
                if html[i] == '{':
                    depth += 1
                elif html[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(html[start:i + 1])
                        except Exception:
                            return None
        return None

    @staticmethod
    def _num(s):
        if not s:
            return None
        m = re.search(r'[\d.]+', str(s).replace(',', ''))
        try:
            return float(m.group(0)) if m else None
        except Exception:
            return None

    @staticmethod
    def _parse_item(item):
        offer = item.get('offer', {}) or {}
        if not offer:
            return None
        info = offer.get('information', {}) or {}
        title = info.get('enPureTitle') or (offer.get('image', {}) or {}).get('alt') or info.get('title')
        if not title:
            return None
        low = offer.get('lowerPrice', '')
        up = offer.get('upperPrice', '')
        tp = offer.get('tradePrice', {}) or {}
        usd_min = AlibabaPriceFetcher._num(low) or AlibabaPriceFetcher._num(tp.get('price'))
        usd_max = AlibabaPriceFetcher._num(up) or usd_min
        if low and up:
            price_str = "US %s-%s" % (low, up)
        elif low:
            price_str = "US %s" % low
        else:
            price_str = tp.get('price', '') or ''
        moq = tp.get('minOrder', '') or ''
        country = (offer.get('company', {}) or {}).get('expCountry', '') or ''
        pid = str(offer.get('id', '') or '')
        eurl = info.get('eurl', '') or ''
        if eurl:
            url = eurl if eurl.startswith('http') else 'https:' + eurl
        elif pid:
            url = "https://www.alibaba.com/product-detail/__%s.html" % pid
        else:
            url = ''
        return {"title": title.strip(), "usd_min": usd_min, "usd_max": usd_max,
                "price_str": price_str, "moq": moq, "country": country,
                "url": url, "product_id": pid}

    def search(self, keyword, retries=3, max_items=20, delay=2.5):
        kw = keyword.strip().replace(' ', '-').lower()
        url = BASE_URL.format(keyword=quote(kw, safe='-'))
        for att in range(1, retries + 1):
            html = self._get(url)
            if html:
                pd = self._extract_page_data(html)
                if pd:
                    items = (pd.get('offerResultData', {}) or {}).get('itemInfoList', []) or []
                    out = []
                    for it in items[:max_items]:
                        p = self._parse_item(it)
                        if p:
                            out.append(p)
                    if out:
                        return out, att
            if att < retries:
                time.sleep(delay)
        return [], retries


def _selftest():
    sample = '<script>window._PAGE_DATA_ = ' + json.dumps({
        "offerResultData": {"itemInfoList": [
            {"offer": {"id": "1", "information": {"enPureTitle": "Cordless Drill 21V Brushless"},
                       "lowerPrice": "$12.50", "upperPrice": "$18.00",
                       "tradePrice": {"minOrder": "100 pieces"}, "company": {"expCountry": "China"}}},
            {"offer": {"id": "2", "information": {"enPureTitle": "Solar Panel 400W"},
                       "tradePrice": {"price": "US $0.49", "minOrder": "30000 watts"},
                       "company": {"expCountry": "Germany"}}},
        ]}}) + ';</script><div>more</div>'
    pd = AlibabaPriceFetcher._extract_page_data(sample)
    assert pd, "не извлёк _PAGE_DATA_"
    items = pd['offerResultData']['itemInfoList']
    a = AlibabaPriceFetcher._parse_item(items[0])
    b = AlibabaPriceFetcher._parse_item(items[1])
    assert a['usd_min'] == 12.5 and a['usd_max'] == 18.0 and '100' in a['moq'], a
    assert b['usd_min'] == 0.49 and '30000' in b['moq'] and b['country'] == 'Germany', b
    print("selftest OK:")
    print("  ", a)
    print("  ", b)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest':
        _selftest()
        return
    kw = sys.argv[1] if len(sys.argv) > 1 else "drill bosch"
    f = AlibabaPriceFetcher()
    rows, att = f.search(kw, retries=4)
    print("keyword=%r | удачная попытка=%d | товаров=%d\n" % (kw, att, len(rows)))
    for p in rows[:10]:
        print("  %-44s | %-18s | MOQ %-12s | %s"
              % (p['title'][:44], p['price_str'], p['moq'], p['country']))
    if not rows:
        print("  (пусто — окно не поймали за 4 попытки; запусти ещё раз)")


if __name__ == '__main__':
    main()
