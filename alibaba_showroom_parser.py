#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  ALIBABA.COM - SHOWROOM PARSER v6                                            ║
║  Парсер через endpoint /showroom/ - обходит защиту CAPTCHA                   ║
║                                                                              ║
║  Преимущества Showroom:                                                      ║
║  • Меньше CAPTCHA блокировок                                                 ║
║  • Данные в JSON формате внутри HTML (window._PAGE_DATA_)                    ║
║  • USD цены доступны напрямую                                                ║
║  • Полная информация о товарах и поставщиках                                 ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import re
import json
import time
import random
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
from urllib.parse import quote

# Requests
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# curl_cffi (лучше обходит TLS fingerprinting)
try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False


@dataclass
class ProductResult:
    """Результат парсинга одного товара"""
    title: Optional[str] = None
    price: Optional[str] = None           # Форматированная строка "US $X.XX-$Y.YY"
    price_min: Optional[float] = None     # Минимальная цена (число)
    price_max: Optional[float] = None     # Максимальная цена (число)
    currency: str = "USD"
    moq: Optional[int] = None             # Minimum Order Quantity
    moq_unit: Optional[str] = None        # Единица измерения (Piece, Set)
    supplier: Optional[str] = None        # Название поставщика
    supplier_country: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    product_id: Optional[str] = None
    rating: Optional[float] = None
    reviews: Optional[int] = None
    transaction_level: Optional[int] = None  # Уровень транзакций поставщика
    response_rate: Optional[str] = None      # Скорость ответа поставщика


class AlibabaShowroomParser:
    """
    Парсер Alibaba.com через Showroom endpoint
    
    Showroom - это SEO-оптимизированные страницы Alibaba,
    которые содержат JSON данные о товарах в HTML.
    Меньше подвержены блокировке CAPTCHA.
    """
    
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
    
    def __init__(self, use_curl_cffi: bool = True, delay_range: tuple = (1.5, 3.0)):
        """
        Args:
            use_curl_cffi: Использовать curl_cffi для обхода TLS fingerprinting
            delay_range: Диапазон задержки между запросами (сек)
        """
        self.use_curl_cffi = use_curl_cffi and CURL_CFFI_AVAILABLE
        self.delay_range = delay_range
        self.session = None
        self._init_session()
    
    def _init_session(self):
        """Инициализация HTTP сессии"""
        if self.use_curl_cffi:
            self.session = curl_requests.Session(impersonate="chrome120")
        elif REQUESTS_AVAILABLE:
            self.session = requests.Session()
            self.session.headers.update(self.HEADERS)
        else:
            raise ImportError("Требуется requests или curl_cffi")
    
    def _make_request(self, url: str) -> Optional[str]:
        """
        Выполнение HTTP запроса
        
        Returns:
            HTML контент или None при ошибке
        """
        try:
            if self.use_curl_cffi:
                response = self.session.get(url, headers=self.HEADERS, timeout=30)
            else:
                response = self.session.get(url, timeout=30)
            
            response.raise_for_status()
            return response.text
            
        except Exception as e:
            print(f"   ⚠️ Request error: {e}")
            return None
    
    def _detect_protection(self, html: str) -> Dict[str, bool]:
        """Обнаружение защиты"""
        return {
            'captcha': 'captcha' in html.lower() or 'slider' in html.lower(),
            'blocked': 'access denied' in html.lower() or 'forbidden' in html.lower(),
            'bot_detection': 'unusual traffic' in html.lower() or 'automated' in html.lower()
        }
    
    def _extract_page_data(self, html: str) -> Optional[Dict[str, Any]]:
        """
        Извлечение window._PAGE_DATA_ из HTML
        
        Returns:
            Словарь с данными или None
        """
        # Паттерн для извлечения JSON из window._PAGE_DATA_
        patterns = [
            r'window\._PAGE_DATA_\s*=\s*(\{.*?\});?\s*(?:</script>|window\.)',
            r'window\._PAGE_DATA_\s*=\s*(\{.*?\})\s*;?\s*</script>',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
        
        return None
    
    def _parse_price(self, price_str: str) -> tuple:
        """
        Парсинг строки цены в числа
        
        Args:
            price_str: Строка типа "$221.00" или "US $221.00-$235.00"
        
        Returns:
            (min_price, max_price) как float или (None, None)
        """
        if not price_str:
            return None, None
        
        # Убираем символы валюты и пробелы
        clean = re.sub(r'[^\d.,\-]', '', price_str.replace(',', ''))
        
        # Диапазон цен
        range_match = re.search(r'([\d.]+)\s*-\s*([\d.]+)', clean)
        if range_match:
            try:
                return float(range_match.group(1)), float(range_match.group(2))
            except ValueError:
                pass
        
        # Одиночная цена
        single_match = re.search(r'([\d.]+)', clean)
        if single_match:
            try:
                price = float(single_match.group(1))
                return price, price
            except ValueError:
                pass
        
        return None, None
    
    def _parse_moq(self, moq_str: str) -> tuple:
        """
        Парсинг MOQ строки
        
        Args:
            moq_str: Строка типа "1 piece" или "100 sets"
        
        Returns:
            (quantity, unit) или (None, None)
        """
        if not moq_str:
            return None, None
        
        match = re.search(r'(\d+)\s*(\w+)', moq_str)
        if match:
            try:
                return int(match.group(1)), match.group(2)
            except ValueError:
                pass
        
        return None, None
    
    def _parse_item(self, item: Dict[str, Any]) -> Optional[ProductResult]:
        """
        Парсинг одного товара из JSON
        
        Args:
            item: Словарь товара из itemInfoList
        
        Returns:
            ProductResult или None
        """
        offer = item.get('offer', {})
        if not offer:
            return None
        
        result = ProductResult()
        
        # Название товара
        info = offer.get('information', {})
        result.title = (
            info.get('enPureTitle') or 
            offer.get('image', {}).get('alt') or
            info.get('title')
        )
        
        # ID товара
        result.product_id = str(offer.get('id', ''))
        
        # Цены (USD)
        lower_price = offer.get('lowerPrice', '')
        upper_price = offer.get('upperPrice', '')
        
        if lower_price and upper_price:
            result.price = f"US {lower_price}-{upper_price}"
            result.price_min, _ = self._parse_price(lower_price)
            _, result.price_max = self._parse_price(upper_price)
        elif lower_price:
            result.price = f"US {lower_price}"
            result.price_min, result.price_max = self._parse_price(lower_price)
        
        # Альтернативный источник цен - tradePrice
        trade_price = offer.get('tradePrice', {})
        if trade_price and not result.price:
            result.price = trade_price.get('price', '')
            if not result.price_min:
                result.price_min, result.price_max = self._parse_price(result.price)
        
        # MOQ
        min_order = trade_price.get('minOrder', '')
        result.moq, result.moq_unit = self._parse_moq(min_order)
        
        # Поставщик
        company = offer.get('company', {})
        if company:
            result.supplier_country = company.get('expCountry', '')
            result.transaction_level = company.get('transactionLevel')
            result.response_rate = company.get('record', {}).get('responseRate')
        
        # URL товара
        eurl = info.get('eurl', '')
        if eurl:
            result.url = eurl if eurl.startswith('http') else f"https:{eurl}"
        elif result.product_id:
            result.url = f"https://www.alibaba.com/product-detail/__{result.product_id}.html"
        
        # Изображение
        image = offer.get('image', {})
        main_image = image.get('mainImage') or image.get('productImage')
        if main_image:
            result.image_url = main_image if main_image.startswith('http') else f"https:{main_image}"
        
        # Рейтинг и отзывы
        reviews_data = offer.get('reviews', {})
        if isinstance(reviews_data, dict):
            result.reviews = reviews_data.get('count')
            result.rating = reviews_data.get('starScore')
        
        return result
    
    def search(self, keyword: str, max_items: int = 50) -> List[ProductResult]:
        """
        Поиск товаров по ключевому слову
        
        Args:
            keyword: Поисковый запрос
            max_items: Максимальное количество товаров
        
        Returns:
            Список ProductResult
        """
        # Формируем URL для Showroom
        keyword_clean = keyword.replace(' ', '-').lower()
        url = self.BASE_URL.format(keyword=quote(keyword_clean, safe='-'))
        
        # Запрос
        html = self._make_request(url)
        if not html:
            return []
        
        # Проверка защиты
        protection = self._detect_protection(html)
        if protection['captcha']:
            print(f"   ⚠️ CAPTCHA обнаружена")
        if protection['blocked']:
            print(f"   ⚠️ Доступ заблокирован")
        
        # Извлечение JSON данных
        page_data = self._extract_page_data(html)
        if not page_data:
            # Fallback: попробуем извлечь товары через regex
            return self._extract_via_regex(html, max_items)
        
        # Парсинг товаров
        results = []
        
        # Товары в offerResultData
        offer_data = page_data.get('offerResultData', {})
        items = offer_data.get('itemInfoList', [])
        
        for item in items[:max_items]:
            product = self._parse_item(item)
            if product and product.title:
                results.append(product)
        
        # Также проверим firstProductCachedData
        first_product = page_data.get('firstProductCachedData', {})
        if first_product and len(results) < max_items:
            product = self._parse_item({'offer': first_product.get('offer', {})})
            if product and product.title:
                # Проверяем на дубликат
                if not any(r.product_id == product.product_id for r in results):
                    results.insert(0, product)
        
        return results
    
    def _extract_via_regex(self, html: str, max_items: int = 50) -> List[ProductResult]:
        """
        Fallback метод: извлечение товаров через regex
        
        Используется когда window._PAGE_DATA_ недоступен
        """
        results = []
        
        # Паттерны для извлечения
        # Названия из alt атрибутов изображений
        title_pattern = r'alt="([^"]{20,200})"[^>]*product-detail'
        
        # URL товаров
        url_pattern = r'(//www\.alibaba\.com/product-detail/[^"]+_\d+\.html)'
        
        # Цены USD
        price_pattern = r'US\s*\$\s*([\d,.]+(?:\s*-\s*\$?\s*[\d,.]+)?)'
        
        # Извлекаем URL
        urls = list(set(re.findall(url_pattern, html)))[:max_items]
        
        # Извлекаем цены
        prices = re.findall(price_pattern, html)
        
        for i, url in enumerate(urls):
            result = ProductResult()
            result.url = f"https:{url}" if not url.startswith('http') else url
            
            # Извлечение ID из URL
            id_match = re.search(r'_(\d+)\.html', url)
            if id_match:
                result.product_id = id_match.group(1)
            
            # Извлечение названия из URL
            name_match = re.search(r'/product-detail/([^_]+)_', url)
            if name_match:
                result.title = name_match.group(1).replace('-', ' ')
            
            # Присваиваем цену если есть
            if i < len(prices):
                result.price = f"US ${prices[i]}"
                result.price_min, result.price_max = self._parse_price(prices[i])
            
            if result.url:
                results.append(result)
        
        return results
    
    def search_multiple(self, keywords: List[str], delay: bool = True) -> Dict[str, List[ProductResult]]:
        """
        Поиск по нескольким ключевым словам
        
        Args:
            keywords: Список ключевых слов
            delay: Добавлять задержку между запросами
        
        Returns:
            Словарь {keyword: [results]}
        """
        all_results = {}
        
        for i, keyword in enumerate(keywords):
            print(f"🔍 [{i+1}/{len(keywords)}] {keyword}...")
            
            results = self.search(keyword)
            all_results[keyword] = results
            
            print(f"   ✅ Найдено: {len(results)} товаров")
            
            # Задержка между запросами
            if delay and i < len(keywords) - 1:
                sleep_time = random.uniform(*self.delay_range)
                time.sleep(sleep_time)
        
        return all_results


def test_parser():
    """Тестирование парсера"""
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  ALIBABA.COM - SHOWROOM PARSER v6 TEST                           ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()
    
    # Тестовые запросы
    test_keywords = [
        "laptop",
        "wireless-earbuds",
        "solar-panel",
        "led-bulb",
        "bluetooth-speaker",
    ]
    
    # Инициализация парсера
    use_curl = CURL_CFFI_AVAILABLE
    print(f"📋 HTTP клиент: {'curl_cffi' if use_curl else 'requests'}")
    print(f"📦 Тестовых запросов: {len(test_keywords)}")
    print()
    
    parser = AlibabaShowroomParser(use_curl_cffi=use_curl)
    
    # Результаты
    all_results = []
    total_found = 0
    total_with_price = 0
    
    start_time = time.time()
    
    for i, keyword in enumerate(test_keywords):
        print(f"{'='*60}")
        print(f"🔍 [{i+1}/{len(test_keywords)}] {keyword}")
        print(f"{'='*60}")
        
        results = parser.search(keyword, max_items=10)
        
        found = len(results)
        with_price = len([r for r in results if r.price_min])
        
        total_found += found
        total_with_price += with_price
        
        print(f"   📦 Найдено: {found}")
        print(f"   💰 С ценой: {with_price}")
        
        # Показать первые 3 товара
        for j, product in enumerate(results[:3]):
            print()
            print(f"   {j+1}. {(product.title or 'N/A')[:50]}...")
            print(f"      💰 {product.price or 'N/A'}")
            if product.price_min:
                print(f"      📊 ${product.price_min:.2f} - ${product.price_max:.2f}")
            if product.moq:
                print(f"      📦 MOQ: {product.moq} {product.moq_unit or ''}")
            if product.supplier_country:
                print(f"      🌍 {product.supplier_country[:30]}...")
        
        all_results.extend([{
            'query': keyword,
            **asdict(r)
        } for r in results])
        
        # Пауза между запросами
        if i < len(test_keywords) - 1:
            time.sleep(random.uniform(1.5, 2.5))
    
    elapsed = time.time() - start_time
    
    # Итоги
    print()
    print("="*60)
    print("📊 ИТОГОВАЯ СТАТИСТИКА")
    print("="*60)
    print()
    print(f"   ✅ Всего товаров: {total_found}")
    print(f"   💰 С ценами:      {total_with_price}")
    print(f"   ⏱️ Время:         {elapsed:.1f}с")
    print(f"   ⚡ Среднее:       {elapsed/len(test_keywords):.2f}с/запрос")
    
    # Сохранение результатов
    output = {
        'marketplace': 'alibaba.com',
        'parser': 'showroom_v6',
        'test_date': datetime.now().isoformat(),
        'summary': {
            'total_found': total_found,
            'with_price': total_with_price,
            'queries': len(test_keywords),
            'elapsed_time': elapsed
        },
        'results': all_results
    }
    
    output_file = 'alibaba_showroom_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print()
    print(f"💾 Результаты: {output_file}")
    print()
    
    return output


if __name__ == "__main__":
    test_parser()
