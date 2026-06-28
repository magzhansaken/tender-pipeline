#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║  ALIEXPRESS.RU - PLAYWRIGHT ФИНАЛЬНЫЙ                           ║
║  Извлечение цен через JS-рендеринг                              ║
╚══════════════════════════════════════════════════════════════════╝

Проблема: curl_cffi загружает страницу, но цены рендерятся JS
Решение: Playwright ждёт загрузки цен и извлекает их
"""

import re
import time
import json
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from urllib.parse import quote

# ═══════════════════════════════════════════════════════════════════
# ТЕСТОВЫЕ ТОВАРЫ
# ═══════════════════════════════════════════════════════════════════

TEST_PRODUCTS = [
    {"id": 1, "query": "HDMI кабель"},
    {"id": 2, "query": "TWS наушники"},
    {"id": 3, "query": "LED лампа E27"},
    {"id": 4, "query": "USB Type-C кабель"},
    {"id": 5, "query": "чехол iPhone"},
]

# ═══════════════════════════════════════════════════════════════════
# РЕЗУЛЬТАТЫ
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Product:
    title: str
    price: str
    price_value: float
    url: str
    image: str = ""

@dataclass
class SearchResult:
    query: str
    found: bool
    products: List[Product]
    time_sec: float
    error: str = ""

# ═══════════════════════════════════════════════════════════════════
# PLAYWRIGHT ПАРСЕР
# ═══════════════════════════════════════════════════════════════════

class AliExpressPlaywright:
    """
    Playwright парсер для AliExpress.ru с полным JS-рендерингом
    """
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser = None
        self.context = None
        
        try:
            from playwright.sync_api import sync_playwright
            self.playwright_module = sync_playwright
            print("✅ Playwright доступен")
        except ImportError:
            print("❌ Playwright не установлен!")
            print("   pip install playwright")
            print("   playwright install chromium")
            self.playwright_module = None
    
    def search(self, query: str, max_products: int = 5) -> SearchResult:
        """
        Поиск товаров на AliExpress.ru
        """
        if not self.playwright_module:
            return SearchResult(
                query=query,
                found=False,
                products=[],
                time_sec=0,
                error="Playwright не установлен"
            )
        
        start_time = time.time()
        
        try:
            with self.playwright_module() as p:
                # Запуск браузера
                browser = p.chromium.launch(
                    headless=self.headless,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-infobars',
                        '--window-size=1920,1080',
                        '--disable-extensions',
                    ]
                )
                
                # Контекст браузера
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='ru-RU',
                    timezone_id='Europe/Moscow',
                )
                
                # Скрываем признаки автоматизации
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});
                    window.chrome = { runtime: {} };
                """)
                
                page = context.new_page()
                
                # Блокируем тяжёлые ресурсы для скорости
                page.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda route: route.abort())
                page.route("**/analytics/**", lambda route: route.abort())
                page.route("**/tracking/**", lambda route: route.abort())
                page.route("**/beacon/**", lambda route: route.abort())
                
                # Переход на страницу поиска
                search_url = f"https://aliexpress.ru/wholesale?SearchText={quote(query)}"
                print(f"      → Загрузка: {search_url[:60]}...")
                
                page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
                
                # ═══ ОЖИДАНИЕ ЗАГРУЗКИ ЦЕН ═══
                print("      → Ожидание загрузки цен...")
                
                # Список селекторов для цен на AliExpress
                price_selectors = [
                    '[class*="snow-price"]',
                    '[class*="price_price"]',
                    '[class*="product-price"]',
                    '[class*="Price"]',
                    '[data-spm="price"]',
                    '.price',
                ]
                
                price_loaded = False
                for selector in price_selectors:
                    try:
                        page.wait_for_selector(selector, timeout=5000)
                        price_loaded = True
                        print(f"      → Цены найдены по селектору: {selector}")
                        break
                    except:
                        continue
                
                # Дополнительное ожидание для полной загрузки
                if not price_loaded:
                    print("      → Ожидание network idle...")
                    try:
                        page.wait_for_load_state('networkidle', timeout=10000)
                    except:
                        pass
                    page.wait_for_timeout(2000)
                
                # ═══ ИЗВЛЕЧЕНИЕ ТОВАРОВ ═══
                print("      → Извлечение товаров...")
                
                products = []
                
                # Метод 1: Через JavaScript evaluation
                try:
                    js_products = page.evaluate("""
                        () => {
                            const results = [];
                            
                            // Ищем карточки товаров
                            const cards = document.querySelectorAll(
                                '[class*="product-card"], [class*="ProductCard"], ' +
                                '[class*="item-card"], [class*="SearchProductFeed"] > div, ' +
                                '[data-spm="product"], [class*="product-snippet"]'
                            );
                            
                            for (let i = 0; i < Math.min(cards.length, 10); i++) {
                                const card = cards[i];
                                
                                // Название
                                let title = '';
                                const titleEl = card.querySelector(
                                    '[class*="title"], [class*="name"], h3, h2, ' +
                                    '[class*="snippet_name"], [class*="product__title"]'
                                );
                                if (titleEl) title = titleEl.innerText.trim();
                                
                                // Цена
                                let price = '';
                                let priceValue = 0;
                                const priceEl = card.querySelector(
                                    '[class*="price"], [class*="Price"], ' +
                                    '[data-spm="price"], [class*="snow-price"]'
                                );
                                if (priceEl) {
                                    price = priceEl.innerText.trim();
                                    // Извлекаем число
                                    const match = price.replace(/\\s/g, '').match(/([\\d.,]+)/);
                                    if (match) {
                                        priceValue = parseFloat(match[1].replace(',', '.'));
                                    }
                                }
                                
                                // URL
                                let url = '';
                                const linkEl = card.querySelector('a[href*="/item/"], a[href*="product"]');
                                if (linkEl) url = linkEl.href;
                                
                                // Изображение
                                let image = '';
                                const imgEl = card.querySelector('img[src*="ae"], img[src*="alicdn"]');
                                if (imgEl) image = imgEl.src;
                                
                                if (title || priceValue > 0) {
                                    results.push({
                                        title: title.substring(0, 100),
                                        price: price,
                                        priceValue: priceValue,
                                        url: url,
                                        image: image
                                    });
                                }
                            }
                            
                            return results;
                        }
                    """)
                    
                    for p in js_products[:max_products]:
                        if p.get('title') or p.get('priceValue', 0) > 0:
                            products.append(Product(
                                title=p.get('title', 'Без названия'),
                                price=p.get('price', ''),
                                price_value=p.get('priceValue', 0),
                                url=p.get('url', ''),
                                image=p.get('image', '')
                            ))
                except Exception as e:
                    print(f"      ⚠️ JS evaluation ошибка: {e}")
                
                # Метод 2: Через HTML парсинг (fallback)
                if not products:
                    print("      → Fallback: HTML парсинг...")
                    html = page.content()
                    
                    # Ищем JSON в HTML
                    json_match = re.search(r'"items"\s*:\s*\[(.*?)\]', html, re.DOTALL)
                    if json_match:
                        items_str = '[' + json_match.group(1) + ']'
                        try:
                            # Пытаемся распарсить как JSON
                            items_data = json.loads(items_str)
                            for item in items_data[:max_products]:
                                products.append(Product(
                                    title=item.get('title', '')[:100],
                                    price=str(item.get('price', {}).get('minPrice', '')),
                                    price_value=float(item.get('price', {}).get('minPrice', 0) or 0),
                                    url=item.get('productDetailUrl', ''),
                                    image=item.get('image', '')
                                ))
                        except:
                            pass
                    
                    # Regex fallback
                    if not products:
                        titles = re.findall(r'"title"\s*:\s*"([^"]{10,100})"', html)
                        prices = re.findall(r'"minPrice"\s*:\s*"?([\d.]+)"?', html)
                        urls = re.findall(r'"productDetailUrl"\s*:\s*"([^"]+)"', html)
                        
                        for i in range(min(len(titles), max_products)):
                            price_val = float(prices[i]) if i < len(prices) else 0
                            products.append(Product(
                                title=titles[i][:100],
                                price=f"{price_val} ₽" if price_val else "",
                                price_value=price_val,
                                url=urls[i] if i < len(urls) else "",
                                image=""
                            ))
                
                browser.close()
                elapsed = time.time() - start_time
                
                # Фильтруем товары с ценами
                products_with_prices = [p for p in products if p.price_value > 0]
                
                return SearchResult(
                    query=query,
                    found=len(products) > 0,
                    products=products,
                    time_sec=elapsed
                )
                
        except Exception as e:
            elapsed = time.time() - start_time
            return SearchResult(
                query=query,
                found=False,
                products=[],
                time_sec=elapsed,
                error=str(e)[:100]
            )

# ═══════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ ТЕСТЕР
# ═══════════════════════════════════════════════════════════════════

def main():
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  ALIEXPRESS.RU - PLAYWRIGHT ФИНАЛЬНЫЙ                           ║")
    print("║  Извлечение цен через JS-рендеринг                              ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()
    print(f"🕐 Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Инициализация
    parser = AliExpressPlaywright(headless=True)
    
    if not parser.playwright_module:
        print("\n❌ Установите Playwright:")
        print("   pip install playwright")
        print("   playwright install chromium")
        return
    
    print(f"📦 Тестовых товаров: {len(TEST_PRODUCTS)}")
    print()
    print("=" * 70)
    
    all_results = []
    total_found = 0
    total_with_prices = 0
    
    for product in TEST_PRODUCTS:
        print(f"\n🔍 [{product['id']}/5] {product['query']}")
        print("-" * 50)
        
        result = parser.search(product['query'], max_products=3)
        all_results.append(result)
        
        if result.found:
            total_found += 1
            
            for i, p in enumerate(result.products[:3], 1):
                status = "✅" if p.price_value > 0 else "⚠️"
                price_str = f"{p.price_value:,.0f} ₽" if p.price_value > 0 else "Нет цены"
                
                if p.price_value > 0:
                    total_with_prices += 1
                
                print(f"   {status} [{i}] {p.title[:50]}...")
                print(f"       💰 {price_str}")
                if p.url:
                    print(f"       🔗 {p.url[:60]}...")
        else:
            print(f"   ❌ Ошибка: {result.error}")
        
        print(f"   ⏱️ Время: {result.time_sec:.1f}s")
        
        time.sleep(2)  # Пауза между запросами
    
    # Итоги
    print("\n")
    print("=" * 70)
    print("📊 ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 70)
    
    print(f"""
┌─────────────────────────────────────────────────────────────────┐
│  Метод: Playwright (headless Chrome + JS рендеринг)            │
├─────────────────────────────────────────────────────────────────┤
│  Товаров найдено:     {total_found}/5                                      │
│  Товаров с ценами:    {total_with_prices}/{total_found * 3 if total_found > 0 else 0}                                     │
│  Успешность поиска:   {(total_found / 5 * 100):.0f}%                                     │
│  Успешность цен:      {(total_with_prices / (total_found * 3) * 100) if total_found > 0 else 0:.0f}%                                     │
└─────────────────────────────────────────────────────────────────┘
""")
    
    if total_with_prices > 0:
        print("🏆 РЕЗУЛЬТАТ: Playwright РАБОТАЕТ для AliExpress!")
        print("   Можно интегрировать в tenderfinder")
    else:
        print("⚠️ РЕЗУЛЬТАТ: Цены не извлечены")
        print("   Возможные причины:")
        print("   - AliExpress заблокировал headless браузер")
        print("   - Нужен headless=False для отладки")
        print("   - Нужны дополнительные методы обхода")
    
    # Сохранение результатов
    filename = f"aliexpress_playwright_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    export_data = []
    for r in all_results:
        export_data.append({
            'query': r.query,
            'found': r.found,
            'time_sec': r.time_sec,
            'error': r.error,
            'products': [asdict(p) for p in r.products]
        })
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 Результаты: {filename}")
    print(f"\n🕐 Завершено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ═══════════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    main()
