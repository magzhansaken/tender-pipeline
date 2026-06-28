"""
ollama_debug.py — сырой вывод ответов Олламы, чтобы понять причину 0 результатов и 404.
Ставит только requests. Нужен OLLAMA_API_KEY.
    python ollama_debug.py
"""
import os
import requests

KEY = os.getenv("OLLAMA_API_KEY")
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def call(path, payload):
    r = requests.post(f"https://ollama.com/api/{path}", headers=H, json=payload, timeout=45)
    return r.status_code, r.text


print("OLLAMA_API_KEY есть:", bool(KEY), "| длина:", len(KEY or ""))

print("\n=== A) web_search простой запрос 'ноутбук Lenovo цена' ===")
try:
    st, body = call("web_search", {"query": "ноутбук Lenovo цена"})
    print("HTTP", st)
    print(body[:2500])
except Exception as e:
    print("ошибка:", str(e)[:200])

print("\n\n=== B) web_fetch КОНТРОЛЬ (ollama.com — должно работать) ===")
try:
    st, body = call("web_fetch", {"url": "https://ollama.com"})
    print("HTTP", st)
    print(body[:900])
except Exception as e:
    print("ошибка:", str(e)[:200])

print("\n\n=== C) web_fetch ozon.kz (главная) ===")
try:
    st, body = call("web_fetch", {"url": "https://www.ozon.kz"})
    print("HTTP", st)
    print(body[:1200])
except Exception as e:
    print("ошибка:", str(e)[:200])
