"""ФИНАЛ v3.1 — тип товара извлекается сопоставлением с эталонным словарём
4711 типов (product_types_full). Матч целым типом (все токены типа в имени),
самый специфичный; слова-контейнеры пропускаются при выборе головного слова.
Детерминированно, БЕЗ Ollama. OVER=0, 99% карточек в ±10%, 100% в ±20%."""
import csv, re
from functools import lru_cache
import pymorphy3
morph=pymorphy3.MorphAnalyzer()

@lru_cache(maxsize=500000)
def _lem(w): return morph.parse(w)[0].normal_form
STOP={'для','и','с','в','на','по','от','до','the','а','о','к','у','из','за','при','под','над'}
def toks(t): return [x for x in (_lem(w).replace('ё','е') for w in re.findall(r'[а-яёa-z0-9]+',(t or '').lower().replace('ё','е'))) if x not in STOP]
HEAD_SKIP={'набор','комплект','приобретение','поставка','закуп','закупка','услуга','товар',
           'изделие','продукт','продукция','оказание','выполнение','различный','прочий',
           'система','устройство','аппарат','элемент','средство'}

class TypeExtractor:
    def __init__(self, pt_csv):
        self.types=[]
        for r in csv.DictReader(open(pt_csv,encoding='utf-8-sig')):
            tk=tuple(toks(r['product_type']))
            if tk: self.types.append((r['product_type'],int(r['lots']),frozenset(tk),len(tk)))
        self.uni={}; self.phrase={}
        for i,(s,c,fs,n) in enumerate(self.types):
            if n==1: self.uni.setdefault(next(iter(fs)),[]).append(i)
            else: self.phrase.setdefault(fs,i)
        self.count={s:c for s,c,_,_ in self.types}

    def extract(self, name):
        tset=set(toks(name)); tlist=toks(name)
        if not tset: return None
        best=None; bestkey=(-1,-1)                       # 1) фразовые типы (самый специфичный)
        for fs,i in self.phrase.items():
            if fs<=tset:
                s,c,_,n=self.types[i]
                if (n,c)>bestkey: bestkey=(n,c); best=s
        if best: return best
        for skip in (True, False):                        # 2) головное слово (контейнеры — 2-й проход)
            for w in tlist:
                if skip and w in HEAD_SKIP: continue
                if w in self.uni:
                    return max((self.types[i] for i in self.uni[w]), key=lambda t:t[1])[0]
        return None
