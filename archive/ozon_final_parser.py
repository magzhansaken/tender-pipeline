#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║  OZON.KZ - ФИНАЛЬНЫЙ ПАРСЕР                                     ║
║  Использует найденные паттерны с тонким пробелом (\u2009)       ║
╚══════════════════════════════════════════════════════════════════╝

УСТАНОВКА:
pip install playwright playwright-stealth
playwright install chromium

КЛЮЧЕВОЕ ОТКРЫТИЕ:
Ozon.kz использует Unicode thin space (\u2009) в ценах:
  "393 488 ₸" где пробел = \u2009, а не обычный пробел
"""

import re
import time
from typing import Optional, List, Dict
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

# ═══════════════════════════════════════════════════════════════════
# ТЕСТОВЫЕ ТОВАРЫ
# ═══════════════════════════════════════════════════════════════════

TEST_PRODUCTS = [
    {"id": 1, "category": "Компьютеры", "query": "Ноутбук Lenovo IdeaPad"},
    {"id": 2, "category": "Компьютеры", "query": "Монитор Samsung 24"},
    {"id": 3, "category": "Телевизоры", "query": "Телевизор LG 43"},
    {"id": 4, "category": "Телевизоры", "query": "Телевизор Samsung 55"},
    {"id": 5, "category": "Бытовая техника", "query": "Холодильник Bosch"},
    {"id": 6, "category": "Бытовая техника", "query": "Стиральная машина LG"},
    {"id": 7, "category": "Смартфоны", "query": "iPhone 15 128GB"},
    {"id": 8, "category": "Смартфоны", "query": "Samsung Galaxy S24"},
    {"id": 9, "category": "Инструменты", "query": "Дрель Bosch"},
    {"id": 10, "category": "Стройматериалы", "query": "Кабель ВВГнг 3х2.5"},
]

# ═══════════════════════════════════════════════════════════════════
# РЕЗУЛЬТАТ
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SearchResult:
    method: str
    query: str
    found: bool
    product_title: str
    price: str
    url: str
    time_sec: float
    error: str = ""

# ═══════════════════════════════════════════════════════════════════
# ФИНАЛЬНЫЙ ПАРСЕР OZON.KZ
# ═══════════════════════════════════════════════════════════════════

class OzonFinalParser:
    """
    Финальный парсер Ozon.kz
    
    Ключевые паттерны (найдены анализом):
    1. Цены в формате: "393 488 ₸" с thin space (\u2009)
    2. Класс цены: tsHeadline400Small
    3. Ссылки: href="/product/название-товара-id/"
    """
    
    def __init__(self):
        self.browser = None
        self.playwright = None
        
        # Проверяем Playwright
        try:
            from playwright.sync_api import sync_playwright
            self.sync_playwright = sync_playwright
            print("   ✅ Playwright установлен")
        except ImportError:
            self.sync_playwright = None
            print("   ❌ Playwright не установлен!")
            print("      pip install playwright && playwright install chromium")
        
        # Проверяем stealth
        try:
            from playwright_stealth import stealth_sync
            self.stealth_sync = stealth_sync
            print("   ✅ playwright-stealth установлен")
        except ImportError:
            self.stealth_sync = None
            print("   ⚠️ playwright-stealth не установлен")
    
    def search(self, query: str) -> SearchResult:
        """Поиск товара на Ozon.kz"""
        start_time = time.time()
        
        if not self.sync_playwright:
            return SearchResult(
                method="Ozon Final",
                query=query,
                found=False,
                product_title="",
                price="",
                url="",
                time_sec=0,
                error="Playwright не установлен"
            )
        
        try:
            with self.sync_playwright() as p:
                # Запускаем браузер
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                    ]
                )
                
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='ru-RU',
                )
                
                page = context.new_page()
                
                # Применяем stealth
                if self.stealth_sync:
                    self.stealth_sync(page)
                
                # Переходим на страницу поиска
                search_url = f"https://ozon.kz/search/?text={quote(query)}&from_global=true"
                page.goto(search_url, wait_until='networkidle', timeout=30000)
                
                # Ждём загрузки контента
                page.wait_for_timeout(3000)
                
                # Получаем HTML
                html = page.content()
                
                browser.close()
                
                elapsed = time.time() - start_time
                
                # Парсим результаты
                return self._parse_html(html, query, search_url, elapsed)
                
        except Exception as e:
            elapsed = time.time() - start_time
            return SearchResult(
                method="Ozon Final",
                query=query,
                found=False,
                product_title="",
                price="",
                url="",
                time_sec=elapsed,
                error=str(e)[:50]
            )
    
    def _parse_html(self, html: str, query: str, search_url: str, elapsed: float) -> SearchResult:
        """
        Парсинг HTML с использованием найденных паттернов
        
        КЛЮЧЕВОЙ ПАТТЕРН:
        Цены с thin space: (\d[\d\s\u2009]*)\s*₸
        """
        
        # ═══ МЕТОД 1: Цены с thin space (\u2009) ═══
        # Паттерн: число с пробелами/thin space + ₸
        price_pattern = r'(\d[\d\s\u2009]*)\s*₸'
        price_matches = re.findall(price_pattern, html)
        
        prices = []
        for match in price_matches:
            # Убираем все пробелы (обычные и thin space)
            clean_price = re.sub(r'[\s\u2009]', '', match)
            if clean_price.isdigit():
                price_int = int(clean_price)
                # Фильтруем разумные цены (не слишком маленькие и большие)
                if 1000 < price_int < 50000000:
                    prices.append(price_int)
        
        # ═══ МЕТОД 2: Названия из ссылок на товары ═══
        # Паттерн: href="/product/название-товара-id/"
        product_links = re.findall(r'href="(/product/([^"]+))"', html)
        
        titles = []
        for link, slug in product_links:
            # Извлекаем название из slug
            # "lenovo-thinkpad-e16-g2-noutbuk-16-amd-ryzen-7-7735hs-2058745842"
            # Убираем ID в конце (обычно длинное число)
            parts = slug.rsplit('-', 1)
            if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) > 5:
                name_slug = parts[0]
            else:
                name_slug = slug
            
            # Преобразуем slug в читаемое название
            title = name_slug.replace('-', ' ').title()
            
            # Фильтруем короткие и нерелевантные
            if len(title) > 10:
                titles.append(title)
        
        # ═══ МЕТОД 3: Названия из JSON (backup) ═══
        if not titles:
            name_pattern = r'"name"\s*:\s*"([^"]{15,200})"'
            name_matches = re.findall(name_pattern, html)
            titles = [n for n in name_matches if 'ozon' not in n.lower() and 'http' not in n]
        
        # ═══ РЕЗУЛЬТАТ ═══
        if prices:
            title = titles[0] if titles else ""
            price = prices[0]
            
            # Находим URL первого товара
            product_url = ""
            if product_links:
                product_url = f"https://ozon.kz{product_links[0][0]}"
            
            return SearchResult(
                method="Ozon Final",
                query=query,
                found=True,
                product_title=title[:80],
                price=f"{price:,} ₸".replace(',', ' '),
                url=product_url or search_url,
                time_sec=elapsed
            )
        
        # Если цены не найдены
        return SearchResult(
            method="Ozon Final",
            query=query,
            found=bool(titles),
            product_title=titles[0][:80] if titles else "",
            price="",
            url=search_url,
            time_sec=elapsed,
            error="Цены не найдены" if titles else "Товары не найдены"
        )

# ═══════════════════════════════════════════════════════════════════
# ТЕСТЕР
# ═══════════════════════════════════════════════════════════════════

class OzonFinalTester:
    """Тестирование финального парсера"""
    
    def __init__(self):
        print("╔══════════════════════════════════════════════════════════════════╗")
        print("║  OZON.KZ - ФИНАЛЬНЫЙ ТЕСТ ПАРСЕРА                               ║")
        print("╚══════════════════════════════════════════════════════════════════╝")
        print()
        print("🔧 Инициализация:")
        
        self.parser = OzonFinalParser()
        self.results: List[SearchResult] = []
    
    def test_all(self, limit: int = 10):
        """Тестируем парсер на всех товарах"""
        
        products = TEST_PRODUCTS[:limit]
        
        print(f"\n📦 Тестовых товаров: {len(products)}")
        print()
        print("="*80)
        
        for product in products:
            print(f"\n🔍 [{product['id']}/{limit}] {product['category']}: {product['query']}")
            print("-"*60)
            
            result = self.parser.search(product['query'])
            self.results.append(result)
            
            if result.found and result.price:
                status = "✅"
                info = f"{result.price}"
            elif result.found:
                status = "⚠️"
                info = "Найден, без цены"
            else:
                status = "❌"
                info = result.error or "Не найден"
            
            print(f"   {status} {result.time_sec:.1f}s | {info}")
            if result.product_title:
                print(f"      📦 {result.product_title[:60]}...")
            
            time.sleep(2)  # Пауза между запросами
        
        self._print_summary()
    
    def _print_summary(self):
        """Итоговая статистика"""
        print("\n")
        print("="*80)
        print("📊 ИТОГОВАЯ СТАТИСТИКА")
        print("="*80)
        
        total = len(self.results)
        found = sum(1 for r in self.results if r.found)
        with_price = sum(1 for r in self.results if r.price)
        avg_time = sum(r.time_sec for r in self.results) / total if total > 0 else 0
        
        print(f"\n   Всего запросов:  {total}")
        print(f"   Найдено товаров: {found}/{total} ({found/total*100:.0f}%)")
        print(f"   С ценами:        {with_price}/{total} ({with_price/total*100:.0f}%)")
        print(f"   Среднее время:   {avg_time:.1f}s")
        
        print("\n" + "="*80)
        
        if with_price == total:
            print("🏆 УСПЕХ! Все цены извлечены!")
        elif with_price > 0:
            print(f"⚠️ Частичный успех: {with_price}/{total} цен")
        else:
            print("❌ Цены не извлечены")
        
        print("="*80)
        
        # Детальные результаты
        print("\n📋 ДЕТАЛЬНЫЕ РЕЗУЛЬТАТЫ:")
        print("-"*80)
        
        for r in self.results:
            status = "✅" if r.price else ("⚠️" if r.found else "❌")
            price = r.price or "—"
            title = r.product_title[:40] + "..." if len(r.product_title) > 40 else r.product_title or "—"
            print(f"   {status} {r.query[:25]:<25} | {price:<15} | {title}")

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print()
    print(f"🕐 Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    tester = OzonFinalTester()
    
    try:
        # Тестируем на всех 10 товарах
        tester.test_all(limit=10)
    except KeyboardInterrupt:
        print("\n\n⚠️ Остановлено пользователем")
    except Exception as e:
        print(f"\n\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print(f"🕐 Завершено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
