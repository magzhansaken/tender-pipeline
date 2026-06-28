#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║  ALIBABA.COM - ФИНАЛЬНЫЙ РАБОЧИЙ ПАРСЕР v5                      ║
║  Найденные паттерны:                                            ║
║  - JSON arrays: offerList, normalList, galleryOfferList         ║
║  - Цены: US $X.XX - $X.XX (диапазон), priceInfo object         ║
║  - MOQ: "moq", "minOrderQty", "X Pieces (Min. Order)"          ║
║  - Suppliers: supplierName, companyName                         ║
╚══════════════════════════════════════════════════════════════════╝
"""

import requests
import json
import re
import time
from urllib.parse import quote
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════
# ТЕСТОВЫЕ ТОВАРЫ
# ═══════════════════════════════════════════════════════════════════

TEST_PRODUCTS = [
    {"id": 1, "category": "Electronics", "query": "laptop notebook i7"},
    {"id": 2, "category": "Electronics", "query": "wireless earbuds TWS"},
    {"id": 3, "category": "Electronics", "query": "LED TV 4K 55 inch"},
    {"id": 4, "category": "Electronics", "query": "smartphone android 5G"},
    {"id": 5, "category": "Home", "query": "blender mixer kitchen"},
    {"id": 6, "category": "Home", "query": "robot vacuum cleaner"},
    {"id": 7, "category": "Fashion", "query": "leather wallet men"},
    {"id": 8, "category": "Fashion", "query": "sneakers running shoes"},
    {"id": 9, "category": "Industrial", "query": "solar panel monocrystalline"},
    {"id": 10, "category": "Industrial", "query": "electric motor 380V"},
]

@dataclass
class ProductResult:
    query: str
    found: bool = False
    title: Optional[str] = None
    price: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    currency: str = "USD"
    moq: Optional[str] = None
    moq_unit: Optional[str] = None
    supplier: Optional[str] = None
    supplier_country: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    product_id: Optional[str] = None
    rating: Optional[float] = None
    reviews: Optional[int] = None
    time_sec: float = 0.0
    error: Optional[str] = None

# ═══════════════════════════════════════════════════════════════════
# ФИНАЛЬНЫЙ ПАРСЕР
# ═══════════════════════════════════════════════════════════════════

class AlibabaParser:
    """Рабочий парсер Alibaba.com"""
    
    BASE_URL = "https://www.alibaba.com"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0',
        })
    
    def search(self, query: str) -> ProductResult:
        """Поиск товара на Alibaba.com"""
        start = time.time()
        result = ProductResult(query=query)
        
        try:
            url = f"{self.BASE_URL}/trade/search?searchText={quote(query)}"
            resp = self.session.get(url, timeout=30)
            
            if resp.status_code != 200:
                result.error = f"HTTP {resp.status_code}"
                result.time_sec = time.time() - start
                return result
            
            html = resp.text
            
            # Проверка на защиту
            if 'captcha' in html.lower() or 'robot' in html.lower():
                result.error = "Anti-bot protection detected"
                result.time_sec = time.time() - start
                return result
            
            # ═══════════════════════════════════════════════════════════
            # МЕТОД 1: JSON Extraction (основной метод)
            # ═══════════════════════════════════════════════════════════
            
            json_extracted = self._extract_json_data(html)
            
            if json_extracted:
                result.found = True
                result.title = json_extracted.get('title')
                result.price = json_extracted.get('price')
                result.price_min = json_extracted.get('price_min')
                result.price_max = json_extracted.get('price_max')
                result.moq = json_extracted.get('moq')
                result.moq_unit = json_extracted.get('moq_unit')
                result.supplier = json_extracted.get('supplier')
                result.supplier_country = json_extracted.get('country')
                result.url = json_extracted.get('url')
                result.image_url = json_extracted.get('image')
                result.product_id = json_extracted.get('id')
                result.rating = json_extracted.get('rating')
                result.reviews = json_extracted.get('reviews')
            
            # ═══════════════════════════════════════════════════════════
            # МЕТОД 2: Regex Fallback
            # ═══════════════════════════════════════════════════════════
            
            if not result.found or not result.price:
                regex_data = self._extract_regex_data(html)
                
                if regex_data:
                    if not result.title:
                        result.title = regex_data.get('title')
                    if not result.price:
                        result.price = regex_data.get('price')
                        result.price_min = regex_data.get('price_min')
                        result.price_max = regex_data.get('price_max')
                    if not result.moq:
                        result.moq = regex_data.get('moq')
                    if not result.supplier:
                        result.supplier = regex_data.get('supplier')
                    if not result.url:
                        result.url = regex_data.get('url')
                    
                    result.found = True
            
            # ═══════════════════════════════════════════════════════════
            # МЕТОД 3: Форматирование цены
            # ═══════════════════════════════════════════════════════════
            
            if result.found and not result.price and result.price_min:
                if result.price_max and result.price_max != result.price_min:
                    result.price = f"${result.price_min:,.2f} - ${result.price_max:,.2f}"
                else:
                    result.price = f"${result.price_min:,.2f}"
            
            result.time_sec = time.time() - start
            return result
            
        except Exception as e:
            result.error = str(e)[:100]
            result.time_sec = time.time() - start
            return result
    
    def _extract_json_data(self, html: str) -> Optional[Dict]:
        """Извлекает данные из JSON в HTML"""
        
        # Паттерны для JSON массивов
        patterns = [
            r'"offerList"\s*:\s*(\[[\s\S]*?\])(?=\s*[,}])',
            r'"normalList"\s*:\s*(\[[\s\S]*?\])(?=\s*[,}])',
            r'"galleryOfferList"\s*:\s*(\[[\s\S]*?\])(?=\s*[,}])',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html)
            for match in matches:
                try:
                    # Пробуем парсить JSON
                    items = json.loads(match)
                    if items and isinstance(items, list) and len(items) > 0:
                        item = items[0]
                        if isinstance(item, dict):
                            return self._parse_item(item)
                except (json.JSONDecodeError, IndexError):
                    continue
        
        return None
    
    def _parse_item(self, item: Dict) -> Dict:
        """Парсит один товар из JSON"""
        result = {}
        
        # Название
        result['title'] = (
            item.get('title') or 
            item.get('subject') or 
            item.get('name') or 
            item.get('productTitle')
        )
        if result['title']:
            result['title'] = result['title'][:200]
        
        # Цена
        price_info = item.get('priceInfo') or item.get('price') or {}
        
        if isinstance(price_info, dict):
            # Пробуем разные поля
            price_str = (
                price_info.get('price') or 
                price_info.get('localPrice') or
                price_info.get('salePrice') or
                price_info.get('displayPrice')
            )
            result['price'] = price_str
            
            # Min/Max
            if 'minPrice' in price_info:
                try:
                    result['price_min'] = float(str(price_info['minPrice']).replace(',', '').replace('$', ''))
                except:
                    pass
            if 'maxPrice' in price_info:
                try:
                    result['price_max'] = float(str(price_info['maxPrice']).replace(',', '').replace('$', ''))
                except:
                    pass
        elif price_info:
            result['price'] = str(price_info)
            # Парсим числа из строки
            nums = re.findall(r'[\d,.]+', str(price_info))
            if nums:
                try:
                    result['price_min'] = float(nums[0].replace(',', ''))
                    if len(nums) > 1:
                        result['price_max'] = float(nums[-1].replace(',', ''))
                except:
                    pass
        
        # MOQ
        moq = item.get('moq') or item.get('minOrderQty') or item.get('minOrder')
        if moq:
            result['moq'] = str(moq)
        
        moq_unit = item.get('moqUnit') or item.get('unit')
        if moq_unit:
            result['moq_unit'] = str(moq_unit)
        
        # Поставщик
        result['supplier'] = (
            item.get('supplierName') or 
            item.get('companyName') or
            item.get('seller')
        )
        
        if isinstance(item.get('company'), dict):
            result['supplier'] = result['supplier'] or item['company'].get('name')
            result['country'] = item['company'].get('country') or item['company'].get('location')
        
        result['country'] = result.get('country') or item.get('country') or item.get('location')
        
        # URL
        result['url'] = item.get('productUrl') or item.get('detailUrl') or item.get('url')
        if result['url'] and not result['url'].startswith('http'):
            result['url'] = f"{self.BASE_URL}{result['url']}"
        
        # Image
        result['image'] = item.get('image') or item.get('imageUrl') or item.get('imgUrl')
        if isinstance(item.get('images'), list) and item['images']:
            result['image'] = result['image'] or item['images'][0]
        
        # ID
        result['id'] = str(item.get('productId') or item.get('offerId') or item.get('id') or '')
        
        # Rating
        if item.get('rating'):
            try:
                result['rating'] = float(item['rating'])
            except:
                pass
        
        # Reviews
        if item.get('reviewCount') or item.get('reviews'):
            try:
                result['reviews'] = int(item.get('reviewCount') or item.get('reviews'))
            except:
                pass
        
        return result
    
    def _extract_regex_data(self, html: str) -> Optional[Dict]:
        """Извлекает данные через regex"""
        result = {}
        
        # Название
        title_patterns = [
            r'"title"\s*:\s*"([^"]{15,200})"',
            r'"subject"\s*:\s*"([^"]{15,200})"',
            r'"productTitle"\s*:\s*"([^"]{15,200})"',
        ]
        
        for pattern in title_patterns:
            titles = re.findall(pattern, html)
            titles = [t for t in titles if not any(x in t.lower() for x in 
                ['alibaba', 'search', 'filter', 'category', 'menu', 'home', 'login', 'sign'])]
            if titles:
                result['title'] = titles[0][:200]
                break
        
        # Цена
        price_patterns = [
            (r'US\s*\$\s*([\d,.]+)\s*-\s*US?\s*\$\s*([\d,.]+)', 'range'),
            (r'\$([\d,.]+)\s*-\s*\$([\d,.]+)', 'range'),
            (r'US\s*\$\s*([\d,.]+)', 'single'),
            (r'"price"\s*:\s*"?\$?([\d,.]+)"?', 'json'),
        ]
        
        for pattern, ptype in price_patterns:
            prices = re.findall(pattern, html)
            if prices:
                p = prices[0]
                if ptype == 'range' and isinstance(p, tuple):
                    result['price'] = f"${p[0]} - ${p[1]}"
                    try:
                        result['price_min'] = float(p[0].replace(',', ''))
                        result['price_max'] = float(p[1].replace(',', ''))
                    except:
                        pass
                else:
                    val = p if isinstance(p, str) else p[0]
                    result['price'] = f"${val}"
                    try:
                        result['price_min'] = float(val.replace(',', ''))
                    except:
                        pass
                break
        
        # MOQ
        moq_patterns = [
            r'"moq"\s*:\s*"?(\d+)"?',
            r'"minOrderQty"\s*:\s*"?(\d+)"?',
            r'(\d+)\s*(?:Piece|Pieces|Unit|Units|Set|Sets)',
        ]
        
        for pattern in moq_patterns:
            moqs = re.findall(pattern, html, re.IGNORECASE)
            if moqs:
                result['moq'] = moqs[0]
                break
        
        # Поставщик
        supplier_patterns = [
            r'"supplierName"\s*:\s*"([^"]+)"',
            r'"companyName"\s*:\s*"([^"]+)"',
        ]
        
        for pattern in supplier_patterns:
            suppliers = re.findall(pattern, html)
            if suppliers:
                result['supplier'] = suppliers[0]
                break
        
        # URL
        product_urls = re.findall(r'"(https://www\.alibaba\.com/product-detail/[^"]+)"', html)
        if product_urls:
            result['url'] = product_urls[0]
        
        return result if result else None


# ═══════════════════════════════════════════════════════════════════
# ТЕСТИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════════

def run_test():
    print(f"\n🕐 Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("\n╔══════════════════════════════════════════════════════════════════╗")
    print("║  ALIBABA.COM - ФИНАЛЬНЫЙ ТЕСТ ПАРСЕРА v5                         ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(f"\n📦 Тестовых товаров: {len(TEST_PRODUCTS)}")
    
    parser = AlibabaParser()
    
    results = []
    found = 0
    with_price = 0
    total_time = 0
    
    print("\n" + "=" * 70)
    
    for product in TEST_PRODUCTS:
        print(f"🔍 {product['query'][:35]:<35}", end=" ", flush=True)
        
        result = parser.search(product['query'])
        results.append(result)
        total_time += result.time_sec
        
        if result.found:
            found += 1
            status = "✅"
        else:
            status = "❌"
        
        if result.price:
            with_price += 1
            price_str = f"💰 {result.price[:25]}"
        elif result.price_min:
            with_price += 1
            price_str = f"💰 ${result.price_min:,.2f}"
        else:
            price_str = "❌ no price"
        
        print(f"{status} {price_str}")
        
        if result.title:
            print(f"   📦 {result.title[:55]}...")
        if result.moq:
            print(f"   📊 MOQ: {result.moq} {result.moq_unit or 'pcs'}")
        if result.supplier:
            country = f" ({result.supplier_country})" if result.supplier_country else ""
            print(f"   🏭 {result.supplier[:40]}{country}")
        if result.url:
            print(f"   🔗 {result.url[:55]}...")
        if result.error:
            print(f"   ⚠️ {result.error[:50]}")
        
        time.sleep(1.5)
    
    # Итоги
    print("\n" + "=" * 70)
    print("📊 ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 70)
    
    success_rate = (with_price / len(TEST_PRODUCTS) * 100)
    
    print(f"\n   ✅ Найдено товаров: {found}/{len(TEST_PRODUCTS)} ({found/len(TEST_PRODUCTS)*100:.0f}%)")
    print(f"   💰 С ценами:        {with_price}/{len(TEST_PRODUCTS)} ({success_rate:.0f}%)")
    print(f"   ⏱️ Общее время:     {total_time:.1f}с")
    print(f"   ⚡ Среднее время:   {total_time/len(TEST_PRODUCTS):.2f}с/товар")
    
    print("\n" + "=" * 70)
    if success_rate >= 70:
        print(f"🏆 УСПЕХ! Парсер работает на {success_rate:.0f}%")
    elif success_rate >= 50:
        print(f"⚠️ ЧАСТИЧНЫЙ УСПЕХ: {success_rate:.0f}%")
    else:
        print(f"❌ ТРЕБУЕТСЯ ДОРАБОТКА: только {success_rate:.0f}%")
        print("   → Alibaba имеет серьёзную защиту от ботов")
        print("   → Рекомендуется использовать:")
        print("      • Playwright/Selenium для JS рендеринга")
        print("      • curl_cffi для обхода TLS fingerprinting")
        print("      • Rotating proxies")
    print("=" * 70)
    
    # Сохраняем результаты
    output = {
        "marketplace": "alibaba.com",
        "test_date": datetime.now().isoformat(),
        "summary": {
            "found": found,
            "with_price": with_price,
            "total": len(TEST_PRODUCTS),
            "success_rate": success_rate,
            "total_time": total_time
        },
        "results": [
            {
                "query": r.query,
                "found": r.found,
                "title": r.title,
                "price": r.price,
                "price_min": r.price_min,
                "price_max": r.price_max,
                "moq": r.moq,
                "supplier": r.supplier,
                "supplier_country": r.supplier_country,
                "url": r.url,
                "product_id": r.product_id,
                "time": r.time_sec,
                "error": r.error
            }
            for r in results
        ]
    }
    
    with open("alibaba_final_results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 Результаты: alibaba_final_results.json")
    print(f"\n🕐 Завершено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    return success_rate


if __name__ == "__main__":
    run_test()
