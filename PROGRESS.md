# Прогрес

## Зроблено
- Git: збережено наявну історію репозиторію, упорядковано `.gitignore`; `data/`, `transactions.beancount`, `report.html`, `.venv/` та `__pycache__/` виключено з комітів. Перевірено, що ці шляхи не відстежуються.
- Поточний стан обліку зі збереженими запитами перевірено: `uv run bean-check main.beancount` — без повідомлень.
- Документи: `PRD.md`, `IMPLEMENTATION_PLAN.md`, розділ «Проєкт» в `AGENTS.md`.
- Каркас обліку: `main.beancount` (рахунки, 4 500 PLN на 31.07.2026, `include`).
- Імпорт: `import.py` читає CSV з `data/` і `rules.csv`, перезаписує `transactions.beancount` у порядку банку, ставить `balance` на кінець кожного місяця.
- Перевірка: `uv run python import.py`, потім `uv run bean-check main.beancount` — без повідомлень. Залишок збігається з банком (15.10.2026: 17 127,34 PLN).
- Без категорії (правил ще немає): Rossmann (2), Play — abonament (2), Decathlon (2).

## Далі
Крок 2 плану: дописати в `rules.csv` Play, Rossmann і Decathlon, знову імпортувати й перевірити, що `Expenses:Без-Категорії` порожня.
