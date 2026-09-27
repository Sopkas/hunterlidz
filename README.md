# Hunter v0

Исследование B2B-лидов из объявлений площадок, scoring по правилам команды и черновики обращения с согласованием человеком. Отправка в v0 — вручную.

Спецификация — документы 08–13 в базе знаний команды (`documents/`, в репозиторий не входит). Слои и решения — `docs/architecture.md`, контракт AI — `docs/research-contract.md`, эксплуатация — `docs/operations.md`, план — `docs/roadmap.md`, журнал — `PROJECT_STATUS.md`, правила для агентов — `AGENTS.md`.

## Установка

Python ≥ 3.12.

```bash
python -m venv .venv
source .venv/bin/activate                     # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e .
cp .env.example .env                          # Windows: copy .env.example .env — и заполните
```

На Windows без venv используйте `py` вместо `python`. В `.env` обязательно `HUNTER_ACTOR`; для AI — `ANTHROPIC_API_KEY`, тариф модели за 1M токенов, `COST_CURRENCY`, `RUN_COST_CAP`, `DAILY_COST_CAP`. Без них — код 2 до любых сетевых вызовов. Документы команды — в `documents/` (или путь в `HUNTER_KNOWLEDGE_DIR`).

## Проверки (то же, что в CI)

```bash
python -m pytest -q --durations=5
python -m ruff check src tests
python -m ruff format --check src tests
```

## Рабочий цикл

```bash
# 0. Один раз
python -m hunter init                                # база + первый администратор = HUNTER_ACTOR
python -m hunter users add anna operator             # admin добавляет оператора
python -m hunter claims load data/case_claims.json   # admin: реестр кейсов (doc 01)

# 1. Импорт и отсев
python -m hunter import data/sample_listings.json --campaign pilot-1 --platform fl-demo
python -m hunter leads --limit 20                    # очередь по предварительному score

# 2. AI-исследование лучших и score по doc 04
python -m hunter research --limit 20
python -m hunter lead <ID>                           # карточка: факты, гипотеза, что продаём и почему, score
python -m hunter queue                               # готовые к сообщению (score ≥ 80 и проверки)
python -m hunter allow-contact <ID> --reason "…"     # исключение оператора для WARM

# 3. Черновики и согласование
python -m hunter draft                               # черновики для очереди (или draft <ID>)
python -m hunter review                              # a Approve · e Edit · s Skip · c карточка · n позже · q выход

# 4. Ручная отправка (режим manual) и ответы
python -m hunter mode manual --reason "старт пилота"
python -m hunter sent <MSG> --version N --sent-at 2026-09-27T12:30+03:00
python -m hunter reply <LEAD> --file ответ.txt --received-at 2026-09-28T09:10+03:00
python -m hunter replies
python -m hunter reply-review <ID> --class later --action nurture --return-at 2026-11-02T10:00+03:00
python -m hunter suppress --lead <LEAD> --reason "просили не писать"

# 5. Повторные касания и планировщик
python -m hunter tick                                # раз в минуту через cron / планировщик Windows
python -m hunter tasks --due
python -m hunter followup <TASK>                     # черновик D4 / D9 / D25 → review → sent

# 6. Отчёты и копии
python -m hunter metrics
python -m hunter export exports/leads.csv            # --with-contacts — с адресами
python -m hunter backup
python -m hunter history <ID>                        # аудит любого объекта
```

Approve — согласование точной версии текста и адресата на 24 часа, а не отправка. `sent` регистрируют только после реальной отправки в своём канале.

`data/sample_listings.json` — синтетические объявления для проверки (контакты вымышленные).
