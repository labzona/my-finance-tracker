"""Автономний звіт зі збережених query 1–3: uv run python report.py."""

from __future__ import annotations

import calendar
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import beanquery
from beancount import loader
from beancount.core.data import Query

ROOT = Path(__file__).resolve().parent


def load_report() -> list[dict]:
    entries, errors, options = loader.load_file(str(ROOT / "main.beancount"))
    if errors:
        raise ValueError(f"Облік має помилки: {errors}")
    connection = beanquery.connect(
        "beancount:", entries=entries, errors=errors, options=options
    )
    results = {}
    for prefix in ("1 ", "2 ", "3 "):
        queries = [e for e in entries if isinstance(e, Query) and e.name.startswith(prefix)]
        if len(queries) != 1:
            raise ValueError(f"Очікується один збережений запит з префіксом {prefix!r}")
        cursor = connection.execute(queries[0].query_string)
        columns = [column.name for column in cursor.description]
        results[prefix] = [dict(zip(columns, row)) for row in cursor.fetchall()]

    months = {}
    for row in results["2 "]:
        key = (row["year"], row["month"])
        last = row["last_operation"]
        months[key] = {
            **row,
            "partial": last.day < calendar.monthrange(*key)[1],
            "categories": [],
        }
    for row in results["1 "]:
        months[(row["year"], row["month"])]["categories"].append(
            {"name": row["account"].split(":", 1)[1].replace("-", " "),
             "amount": row["expenses_pln"]}
        )
    for row in results["3 "]:
        months[(row["year"], row["month"])].update(row)

    for month in months.values():
        total = month["expenses_pln"]
        if sum(c["amount"] for c in month["categories"]) != total:
            raise ValueError("Суми категорій не збігаються з витратами")
        if month["fixed_pln"] + month["variable_pln"] != total:
            raise ValueError("Сума фіксованих і змінних не збігається з витратами")
        month["categories"].sort(key=lambda c: c["amount"], reverse=True)
    if not months:
        raise ValueError("Немає даних для звіту")
    return [months[key] for key in sorted(months)]


def json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Непідтримуваний тип: {type(value)}")


PAGE = r'''<!doctype html>
<html lang="uk">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Гроші Олі — місячний звіт</title>
<style>
:root{color-scheme:light;--ink:#16352d;--muted:#52665f;--green:#246b50;--soft:#e6efe9;--orange:#a4572e}
*{box-sizing:border-box}body{margin:0;background:#f5f3ed;color:var(--ink);font:16px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:1000px;margin:auto;padding:44px 24px}header{border-bottom:1px solid #ccd5cb;padding-bottom:26px;margin-bottom:28px}
.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:2px;color:var(--muted)}h1{font-size:clamp(30px,5vw,48px);line-height:1.1;margin:12px 0}
.intro{color:var(--muted);max-width:650px}.selector{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:24px}
select{font:inherit;padding:10px 14px;background:white;border:1px solid #a0b7a8;border-radius:10px;color:var(--ink);max-width:100%}
select:focus-visible{outline:3px solid #c89640}#coverage{font-size:14px;color:var(--muted)}.partial{color:#87461c!important}
section{background:#fff;border:1px solid #dce3d9;border-radius:18px;padding:26px;margin:20px 0;overflow:hidden}
.heading{display:flex;align-items:baseline;gap:12px}h2{font-size:21px;margin:0}.num{color:var(--muted);font-size:13px}.big{font-size:38px;font-weight:650;letter-spacing:-1px;margin-top:18px;font-variant-numeric:tabular-nums}
.caption{color:var(--muted);margin:0 0 22px}.chart{margin:22px 0}.row{display:grid;grid-template-columns:180px 1fr 112px;align-items:center;gap:12px;margin:12px 0;font-size:14px}.track{height:13px;background:#edf1ec;border-radius:8px;overflow:hidden}.fill{height:100%;background:var(--green);border-radius:8px}.amount{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.conclusion{border-top:1px solid #e5e9e2;padding-top:17px;margin:20px 0 0}.stats{display:flex;gap:24px;flex-wrap:wrap}.stats span{color:var(--muted);font-size:14px}.stats strong{display:block;color:var(--ink);font-size:19px;font-variant-numeric:tabular-nums}
.stack{display:flex;height:26px;background:var(--soft);border-radius:8px;overflow:hidden;margin:20px 0 10px}.stack div{height:100%}.legend{display:flex;gap:20px;flex-wrap:wrap;font-size:14px}.dot{display:inline-block;width:10px;height:10px;background:var(--green);border-radius:50%;margin-right:6px}.orange{background:var(--orange)}
.note,footer{color:var(--muted);font-size:13px}footer{margin-top:28px}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
@media(max-width:600px){main{padding:26px 14px}section{padding:20px 16px}.row{grid-template-columns:1fr 100px;gap:5px}.track{grid-column:1/-1;grid-row:auto;height:9px}.row .amount{grid-column:2;grid-row:1}.big{font-size:32px}h2{font-size:18px}}
@media print{body{background:white}main{padding:0}section{break-inside:avoid}select{border:0}}
</style>
<main>
<header><div class="eyebrow">Особисті фінанси · Оля</div><h1>Куди йдуть мої гроші?</h1>
<p class="intro">Три відповіді за календарний місяць. Усі суми в злотих; відкладене залишається на банківському рахунку.</p>
<div class="selector"><label for="month">Місяць</label><select id="month"></select><span id="coverage"></span></div></header>
<noscript>Увімкни JavaScript у браузері, щоб побачити звіт і перемикати місяці.</noscript>
<div id="report" aria-live="polite">
<section aria-labelledby="q1"><div class="heading"><span class="num">01</span><h2 id="q1">Скільки йде на кожну категорію?</h2></div>
<div class="big" id="total"></div><p class="caption">усі витрати за вибраний місяць</p><div id="categories" class="chart"></div><p id="category-note" class="conclusion"></p></section>
<section aria-labelledby="q2"><div class="heading"><span class="num">02</span><h2 id="q2">Яку частку доходу я відкладаю?</h2></div>
<div class="big" id="rate"></div><p class="caption">норма заощаджень: (дохід − витрати) / дохід</p>
<div class="stats"><span>Дохід<strong id="income"></strong></span><span>Витрати<strong id="spent"></strong></span><span>Відкладено<strong id="saved"></strong></span></div>
<div id="savings-chart" class="chart"></div><p id="savings-note" class="conclusion"></p></section>
<section aria-labelledby="q3"><div class="heading"><span class="num">03</span><h2 id="q3">Скільки витрат фіксовані?</h2></div>
<div class="big" id="fixed-share"></div><p class="caption">частка регулярних платежів у витратах</p>
<div id="fixed-chart"></div><div class="legend"><span><i class="dot"></i>Фіксовані: <b id="fixed"></b></span><span><i class="dot orange"></i>Змінні: <b id="variable"></b></span></div>
<p id="fixed-note" class="conclusion"></p><p class="note" id="uncategorized-note"></p></section>
</div><footer>Звіт збережено у файлі й працює без інтернету. Щоб оновити цифри після імпорту, згенеруй його знову.</footer>
</main>
<script id="data" type="application/json">__REPORT_DATA__</script>
<script>
'use strict';
const months=JSON.parse(document.getElementById('data').textContent);
const names=['Січень','Лютий','Березень','Квітень','Травень','Червень','Липень','Серпень','Вересень','Жовтень','Листопад','Грудень'];
const money=v=>new Intl.NumberFormat('uk-UA',{minimumFractionDigits:2,maximumFractionDigits:2}).format(v)+' zł';
const percent=v=>new Intl.NumberFormat('uk-UA',{maximumFractionDigits:2}).format(v)+'%';
const text=(id,value)=>document.getElementById(id).textContent=value;
const dateLabel=value=>value.split('-').reverse().join('.');
const selector=document.getElementById('month');
months.forEach((m,i)=>{const o=document.createElement('option');o.value=i;o.textContent=names[m.month-1]+' '+m.year+(m.partial?' · до '+dateLabel(m.last_operation):'');selector.append(o)});
function bars(container,items){
 container.replaceChildren();const max=Math.max(0,...items.map(i=>Math.abs(i.amount)));
 items.forEach(item=>{const row=document.createElement('div');row.className='row';
 const name=document.createElement('span');name.textContent=item.name;
 const track=document.createElement('div');track.className='track';track.setAttribute('aria-hidden','true');
 const fill=document.createElement('div');fill.className='fill';fill.style.width=(max?Math.abs(item.amount)/max*100:0)+'%';if(item.amount<0)fill.style.background='var(--orange)';track.append(fill);
 const amount=document.createElement('span');amount.className='amount';amount.textContent=money(item.amount);row.append(name,track,amount);container.append(row)});
}
function render(){
 const m=months[Number(selector.value)], partial=m.partial;
 const qualifier=partial?'За наявними даними до '+dateLabel(m.last_operation)+', ':'За місяць ';
 text('coverage',partial?'Неповний місяць · дані до '+dateLabel(m.last_operation):'Остання операція: '+dateLabel(m.last_operation));document.getElementById('coverage').className=partial?'partial':'';
 text('total',money(m.expenses_pln));bars(document.getElementById('categories'),m.categories);
 const top=m.categories[0];text('category-note',top?qualifier+'найбільша категорія — «'+top.name+'»: '+money(top.amount)+'.':'За цей місяць немає витрат.');
 text('rate',m.savings_percent===null?'Не визначено':percent(m.savings_percent));text('income',money(m.income_pln));text('spent',money(m.expenses_pln));text('saved',money(m.saved_pln));
 bars(document.getElementById('savings-chart'),[{name:'Дохід',amount:m.income_pln},{name:'Витрати',amount:m.expenses_pln},{name:'Відкладено',amount:m.saved_pln}]);
 text('savings-note',m.income_pln===0?'Без доходу норму заощаджень не визначаємо; різниця доходів і витрат — '+money(m.saved_pln)+'.':qualifier+(m.saved_pln<0?'витрати перевищили дохід на '+money(-m.saved_pln):'залишилося '+money(m.saved_pln)+' — '+percent(m.savings_percent)+' доходу')+(partial?'; це проміжний результат.':'.'));
 const share=m.total_pln?m.fixed_pln/m.total_pln*100:null;
 text('fixed-share',share===null?'Не визначено':percent(share));text('fixed',money(m.fixed_pln));text('variable',money(m.variable_pln));
 const fixedChart=document.getElementById('fixed-chart');fixedChart.replaceChildren();
 if(m.total_pln>0&&m.fixed_pln>=0&&m.variable_pln>=0){const stack=document.createElement('div');stack.className='stack';stack.setAttribute('aria-hidden','true');[share,100-share].forEach((value,i)=>{const segment=document.createElement('div');segment.style.width=value+'%';segment.style.background=i?'var(--orange)':'var(--green)';stack.append(segment)});fixedChart.append(stack)}
 else{bars(fixedChart,[{name:'Фіксовані',amount:m.fixed_pln},{name:'Змінні',amount:m.variable_pln}])}
 text('fixed-note',qualifier+'регулярні платежі становлять '+money(m.fixed_pln)+', решта витрат — '+money(m.variable_pln)+'.');
 const unknown=m.categories.find(c=>c.name==='Без Категорії');
 text('uncategorized-note','Фіксовані платежі теж можна переглянути або скасувати.'+(unknown?' Без категорії: '+money(unknown.amount)+'; ці операції поки враховані як змінні, тож розбивка потребує уточнення.':''));
}
selector.value=String(months.length-1);selector.addEventListener('change',render);render();
</script>
</html>'''


def main() -> None:
    months = load_report()
    # Не дозволяємо даним закрити script-тег або вставити HTML.
    payload = json.dumps(months, default=json_value, ensure_ascii=False, allow_nan=False)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    output = ROOT / "report.html"
    output.write_text(PAGE.replace("__REPORT_DATA__", payload), encoding="utf-8")
    print(f"Створено {output.name}: {len(months)} місяці, запити 1–3 виконано.")


if __name__ == "__main__":
    main()
