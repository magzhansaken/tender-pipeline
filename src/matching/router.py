"""Производственный роутер: lot_name -> карточка. Использует TypeExtractor + маппинг
типа на карточку. Возвращает (card|None, product_type). None => универсальный промпт."""
import os, csv, json, re
def _re_sub(s): return re.sub(r'\(.*?\)','',s)
try:
    from .extractor import TypeExtractor, toks       # как пакет (matching.router)
except ImportError:
    from extractor import TypeExtractor, toks          # напрямую

class CardRouter:
    _DATA=os.path.join(os.path.dirname(__file__),'data')
    def __init__(self, pt_csv=None, cards_json=None):
        pt_csv=pt_csv or os.path.join(self._DATA,'product_types_full.csv')
        cards_json=cards_json or os.path.join(self._DATA,'cards.json')
        self.EX=TypeExtractor(pt_csv)
        self.cards=json.load(open(cards_json))
        pt={r['product_type']:int(r['lots']) for r in csv.DictReader(open(pt_csv,encoding='utf-8-sig'))}
        self.count=pt
        by_set={}; by_count={}
        for t,c in pt.items():
            by_set.setdefault(frozenset(toks(t)),[]).append(t); by_count.setdefault(c,[]).append(t)
        self.type2card={}
        for c in self.cards:
            ty=self._map(c,pt,by_set,by_count)
            if not ty: continue
            prev=self.type2card.get(ty)
            if prev is None:
                self.type2card[ty]=c
            else:                                    # коллизия: ближе по счётчику к типу
                tc=self.count.get(ty,0)
                if abs((c.get('lots') or 0)-tc) < abs((prev.get('lots') or 0)-tc):
                    self.type2card[ty]=c
    def _map(self,c,pt,by_set,by_count):
        exp=c.get('lots')
        clean=_re_sub(c['name'])
        parts=[p for p in clean.split('/')]
        cn=set(toks(parts[0]))
        name_exact_exists=any(frozenset(toks(p)) and frozenset(toks(p)) in by_set for p in parts)
        # A) имя И счётчик совпадают одновременно — сильнейший сигнал
        for p in parts:
            fs=frozenset(toks(p))
            if fs and fs in by_set:
                for t in by_set[fs]:
                    if self.count.get(t)==exp: return t
        # B) счётчик-матч (прощает ё/е и лемма-варианты) с пересечением по имени
        if exp in by_count:
            cands=by_count[exp]
            named=[t for t in cands if set(toks(t))&cn]
            if named: return max(named,key=lambda t:len(set(toks(t))&cn))
            if len(cands)==1 and not name_exact_exists: return cands[0]   # lenient
        # C) точный матч по имени (ближайший счётчик)
        for p in parts:
            fs=frozenset(toks(p))
            if fs and fs in by_set:
                return min(by_set[fs],key=lambda t:abs(self.count.get(t,0)-(exp or 0)))
        # D) одиночное головное слово
        for w in toks(clean):
            for t in pt:
                if frozenset(toks(t))=={w}: return t
        return None
    def route(self, lot_name, tech_spec=None, use_spec_fallback=False):
        """use_spec_fallback=False (по умолчанию): роутинг ТОЛЬКО по имени лота —
        чисто, OVER=0, покрытие ~84.2%. Включение спасает неинформативные имена
        по 1-й строке ТЗ (покрытие ~86%), НО даёт мисроуты (тара/компонент в ТЗ
        принимается за товар: 'Бумага'→ВТУЛКА). Включать только с умным гардом."""
        ty=self.EX.extract(lot_name)
        card=self.type2card.get(ty)
        if card is None and tech_spec and use_spec_fallback:
            ty2=self.EX.extract((tech_spec or '').split('\n')[0][:120])
            c2=self.type2card.get(ty2)
            if c2 is not None:
                return c2, ty2
        return card, ty

if __name__=='__main__':
    R=CardRouter()
    print("карточек с типом:", len(R.type2card))
    for n in ["Картриджи для HP CE285A","Лента бордюрная","Скотч упаковочный","Провод ПВС 3х2.5",
              "Электрод сварочный МР-3","Набор реагентов для анализа","Флеш-накопитель USB 32ГБ","Огурцы свежие"]:
        c,ty=R.route(n); print(f"  {n[:32]:<33} тип={str(ty)[:16]:<17} -> {c['name'] if c else 'FALLBACK'}")
