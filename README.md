# Hunter v0

Исследование B2B-лидов, scoring по правилам команды и черновики первого сообщения с согласованием человеком. Отправка в v0 — вручную.

Спецификация — документы 08–13 в базе знаний команды (`documents/`, в репозиторий не входит). Слои и решения — `docs/architecture.md`, план — `docs/roadmap.md`, журнал — `PROJECT_STATUS.md`, правила для агентов — `AGENTS.md`.

## Установка

Python ≥ 3.12.

```bash
python -m venv .venv
source .venv/bin/activate                     # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e .
cp .env.example .env                          # Windows: copy .env.example .env — и заполните
```

На Windows без venv используйте `py` вместо `python`.

## Проверки (то же, что в CI)

```bash
python -m pytest -q --durations=5
python -m ruff check src tests
python -m ruff format --check src tests
```

## Запуск

```bash
python -m hunter config                              # проверить конфигурацию
python -m hunter init                                # база + первый администратор = HUNTER_ACTOR
python -m hunter users add anna operator             # admin добавляет оператора
python -m hunter mode manual --reason "старт пилота" # admin меняет режим (shadow по умолчанию)
python -m hunter history <ID>                        # аудит объекта
```

Без `HUNTER_ACTOR` программа завершается с кодом 2 и понятным сообщением.
