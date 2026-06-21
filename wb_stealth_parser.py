#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║  WILDBERRIES - STEALTH ПАРСЕР v3                                 ║
║                                                                  ║
║  Улучшения:                                                      ║
║  - Stealth режим (обход детекта автоматизации)                  ║
║  - Retry с перезапуском браузера                                ║
║  - Случайные задержки                                           ║
║  - Эмуляция человеческого поведения                             ║
╚══════════════════════════════════════════════════════════════════╝

Установка:
    pip install playwright --break-system-packages
    playwright install chromium

Запуск:
    python wb_stealth_parser.py
"""

import json
import time
import random
import re
from urllib.parse import quote
from dataclasses import dataclass
from typing import Optional, List, Dict
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("⚠️ Playwright не установлен!")
    print("   pip install playwright --break-system-packages")
    print("   playwright install chromium")


# ═══════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════════

TEST_PRODUCTS = [
    {"id": 1, "query": "Ноутбук Lenovo"},
    {"id": 2, "query": "Телевизор Samsung"},
    {"id": 3, "query": "iPhone 15"},
    {"id": 4, "query": "Холодильник LG"},
    {"id": 5, "query": "Наушники Sony"},
]

# Stealth скрипт для обхода детекта
STEALTH_JS = """
() => {
    // Переопределяем webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });
    
    // Переопределяем plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5]
    });
    
    // Переопределяем languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['ru-RU', 'ru', 'en-US', 'en']
    });
    
    // Скрываем automation
    window.chrome = {
        runtime: {}
    };
    
    // Переопределяем permissions
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
            Promise.resolve({ state: Notification.permission }) :
            originalQuery(parameters)
    );
    
    // Убираем признаки headless
    Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32'
    });
    
    Object.defineProperty(navigator, 'productSub', {
        get: () => '20030107'
    });
    
    // Добавляем реалистичный user agent data
    Object.defineProperty(navigator, 'userAgentData', {
        get: () => ({
            brands: [
                { brand: 'Google Chrome', version: '120' },
                { brand: 'Chromium', version: '120' },
                { brand: 'Not_A Brand', version: '24' }
            ],
            mobile: false,
            platform: 'Windows'
        })
    });
}
"""


@dataclass
class ProductResult:
    query: str
    found: bool = False
    title: Optional[str] = None
    brand: Optional[str] = None
    price: Optional[float] = None
    price_str: Optional[str] = None
    article: Optional[int] = None
    url: Optional[str] = None
    time_sec: float = 0.0
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# STEALTH PARSER
# ═══════════════════════════════════════════════════════════════════

class WBStealthParser:
    """
    Stealth парсер Wildberries с обходом блокировок
    """
    
    def __init__(self, headless: bool = True):
        if not HAS_PLAYWRIGHT:
            raise ImportError("Playwright не установлен")
        
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.request_count = 0
    
    def _start_browser(self):
        """Запуск браузера со stealth настройками"""
        
        if self.browser:
            self._stop_browser()
        
        self.playwright = sync_playwright().start()
        
        # Запускаем с расширенными настройками
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--window-size=1920,1080',
                '--start-maximized',
            ]
        )
        
        # Создаём контекст с реалистичными параметрами
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='ru-RU',
            timezone_id='Europe/Moscow',
            geolocation={'latitude': 55.7558, 'longitude': 37.6173},  # Москва
            permissions=['geolocation'],
            color_scheme='light',
            java_script_enabled=True,
            has_touch=False,
            is_mobile=False,
        )
        
        # Добавляем stealth скрипт
        self.context.add_init_script(STEALTH_JS)
        
        self.page = self.context.new_page()
        
        # Устанавливаем дополнительные headers
        self.page.set_extra_http_headers({
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
        })
    
    def _stop_browser(self):
        """Остановка браузера"""
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except:
            pass
        
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None
    
    def _human_delay(self, min_sec: float = 1.0, max_sec: float = 3.0):
        """Имитация человеческой задержки"""
        time.sleep(random.uniform(min_sec, max_sec))
    
    def _warm_up(self):
        """Прогрев - сначала заходим на главную"""
        try:
            print("   🔥 Прогрев: заходим на главную...")
            self.page.goto('https://www.wildberries.ru/', 
                          wait_until='domcontentloaded', 
                          timeout=60000)
            self._human_delay(2, 4)
            
            # Прокручиваем немного страницу
            self.page.evaluate('window.scrollBy(0, 500)')
            self._human_delay(1, 2)
            
            print("   ✅ Прогрев завершён")
            return True
        except Exception as e:
            print(f"   ⚠️ Прогрев не удался: {e}")
            return False
    
    def search(self, query: str, retry: int = 2) -> ProductResult:
        """Поиск товара с retry и перезапуском браузера"""
        
        start = time.time()
        result = ProductResult(query=query)
        
        for attempt in range(retry + 1):
            try:
                # Перезапускаем браузер каждые 3 запроса или при ошибке
                if self.request_count % 3 == 0 or not self.browser:
                    print(f"   🔄 {'Перезапуск' if self.browser else 'Запуск'} браузера...")
                    self._start_browser()
                    self._warm_up()
                
                self.request_count += 1
                
                # Случайная задержка перед запросом
                self._human_delay(2, 5)
                
                # Формируем URL
                search_url = f"https://www.wildberries.ru/catalog/0/search.aspx?search={quote(query)}"
                
                # Перехват API
                api_data = None
                
                def handle_response(response):
                    nonlocal api_data
                    if 'search.wb.ru' in response.url and '/search' in response.url:
                        try:
                            api_data = response.json()
                        except:
                            pass
                
                self.page.on('response', handle_response)
                
                # Загружаем страницу с увеличенным timeout
                self.page.goto(search_url, wait_until='domcontentloaded', timeout=60000)
                
                # Ждём загрузки
                self._human_delay(3, 5)
                
                # Прокручиваем страницу (имитация человека)
                self.page.evaluate('window.scrollBy(0, 300)')
                self._human_delay(1, 2)
                
                # Проверяем API данные
                if api_data:
                    products = api_data.get('data', {}).get('products', [])
                    
                    if products:
                        product = products[0]
                        result.found = True
                        result.article = product.get('id')
                        result.brand = product.get('brand', '')
                        result.title = product.get('name', '')
                        
                        price_raw = product.get('salePriceU') or product.get('priceU') or 0
                        result.price = price_raw / 100
                        result.price_str = f"{result.price:,.0f} ₽"
                        
                        if result.article:
                            result.url = f"https://www.wildberries.ru/catalog/{result.article}/detail.aspx"
                        
                        result.time_sec = time.time() - start
                        return result
                
                # Если API не сработал - парсим HTML
                try:
                    self.page.wait_for_selector('[data-nm-id]', timeout=10000)
                    cards = self.page.query_selector_all('[data-nm-id]')
                    
                    if cards:
                        card = cards[0]
                        result.found = True
                        
                        article_str = card.get_attribute('data-nm-id')
                        if article_str:
                            result.article = int(article_str)
                            result.url = f"https://www.wildberries.ru/catalog/{result.article}/detail.aspx"
                        
                        # Название и цена из HTML
                        name_el = card.query_selector('.product-card__name')
                        if name_el:
                            result.title = name_el.inner_text().strip()
                        
                        price_el = card.query_selector('.price__lower-price')
                        if price_el:
                            price_text = price_el.inner_text()
                            price_match = re.search(r'[\d\s]+', price_text.replace('\xa0', ''))
                            if price_match:
                                price_str = price_match.group().replace(' ', '')
                                result.price = float(price_str)
                                result.price_str = f"{result.price:,.0f} ₽"
                        
                        result.time_sec = time.time() - start
                        return result
                except:
                    pass
                
                result.error = "Товары не найдены"
                
            except Exception as e:
                error_msg = str(e)[:80]
                result.error = error_msg
                
                # При timeout - перезапускаем браузер
                if 'Timeout' in error_msg:
                    print(f"   ⚠️ Timeout, перезапуск браузера (попытка {attempt + 1})...")
                    self._stop_browser()
                    self._human_delay(5, 10)
                    continue
        
        result.time_sec = time.time() - start
        return result
    
    def close(self):
        """Закрытие браузера"""
        self._stop_browser()


# ═══════════════════════════════════════════════════════════════════
# ТЕСТИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════════

def run_test():
    """Запуск тестирования"""
    
    if not HAS_PLAYWRIGHT:
        print("❌ Playwright не установлен!")
        return
    
    print(f"\n🕐 Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("\n╔══════════════════════════════════════════════════════════════════╗")
    print("║  WILDBERRIES - STEALTH ПАРСЕР v3                                 ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    
    print("\n⚠️  ВАЖНО: Если IP заблокирован, используйте VPN!")
    print("    Рекомендуется: NordVPN, ExpressVPN или любой бесплатный VPN")
    print("    Выберите сервер в России для лучших результатов\n")
    
    print(f"📦 Тестовых товаров: {len(TEST_PRODUCTS)}")
    
    parser = WBStealthParser(headless=True)
    
    results = []
    found = 0
    with_price = 0
    total_time = 0
    
    print("\n" + "=" * 70)
    
    try:
        for i, product in enumerate(TEST_PRODUCTS):
            print(f"\n🔍 [{i+1}/{len(TEST_PRODUCTS)}] {product['query']}")
            
            result = parser.search(product['query'])
            results.append(result)
            total_time += result.time_sec
            
            if result.found:
                found += 1
                if result.price:
                    with_price += 1
                    print(f"   ✅ {result.price_str}")
                    if result.title:
                        brand = f"[{result.brand}] " if result.brand else ""
                        print(f"   📦 {brand}{result.title[:50]}...")
                else:
                    print(f"   ⚠️ Найден, но без цены")
            else:
                print(f"   ❌ {result.error or 'не найден'}")
            
            # Увеличенная пауза между запросами
            if i < len(TEST_PRODUCTS) - 1:
                delay = random.uniform(5, 10)
                print(f"   ⏳ Пауза {delay:.1f} сек...")
                time.sleep(delay)
    
    finally:
        parser.close()
    
    # Итоги
    print("\n\n" + "=" * 70)
    print("📊 ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 70)
    
    success_rate = with_price / len(TEST_PRODUCTS) * 100
    
    print(f"\n   ✅ Найдено товаров: {found}/{len(TEST_PRODUCTS)} ({found/len(TEST_PRODUCTS)*100:.0f}%)")
    print(f"   💰 С ценами:        {with_price}/{len(TEST_PRODUCTS)} ({success_rate:.0f}%)")
    print(f"   ⏱️ Общее время:     {total_time:.1f}с")
    
    print("\n" + "=" * 70)
    if success_rate >= 70:
        print(f"🏆 УСПЕХ! Парсер работает на {success_rate:.0f}%")
    elif success_rate >= 30:
        print(f"⚠️ ЧАСТИЧНО: {success_rate:.0f}%")
        print("\n   Рекомендации:")
        print("   1. Используйте VPN (сервер в России)")
        print("   2. Подождите 1-2 часа и попробуйте снова")
    else:
        print(f"❌ IP ЗАБЛОКИРОВАН: только {success_rate:.0f}%")
        print("\n   Решения:")
        print("   1. ⭐ Включите VPN (любой, сервер в России)")
        print("   2. Перезагрузите роутер для нового IP")
        print("   3. Подождите 2-4 часа")
    print("=" * 70)
    
    # Сохраняем
    output = {
        "marketplace": "wildberries.ru",
        "parser": "Stealth v3",
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
                "brand": r.brand,
                "title": r.title,
                "price": r.price,
                "article": r.article,
                "url": r.url,
                "time": r.time_sec,
                "error": r.error
            }
            for r in results
        ]
    }
    
    with open("wb_stealth_results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 Результаты: wb_stealth_results.json")


if __name__ == "__main__":
    run_test()
