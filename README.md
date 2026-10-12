# Hunter v0

Исследование B2B-компаний, scoring по правилам команды и черновики обращения с согласованием человеком. Отправка в v0 — вручную.

Спецификация — документы 08–13 в базе знаний команды (`documents/`, в репозиторий не входит). Слои — `docs/architecture.md`, план и соответствие задач — `docs/roadmap.md`, журнал — `PROJECT_STATUS.md`, правила для агентов — `AGENTS.md`.

Сейчас в `main` переносится по задачам код ветки `claude/H-01-H-11-hunter-v0` (план — `docs/roadmap.md`). Готов каркас: конфигурация с проверкой на старте и команда `hunter config`.

## Запуск с нуля

Нужен Python 3.12 или новее.

**macOS / Linux**

```bash
git clone https://github.com/Sopkas/hunterlidz.git && cd hunterlidz
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e .
cp .env.example .env            # заполните HUNTER_DATABASE_URL
python -m hunter config
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/Sopkas/hunterlidz.git; cd hunterlidz
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e .
copy .env.example .env          # заполните HUNTER_DATABASE_URL
python -m hunter config
```

Если PowerShell запрещает запуск `Activate.ps1`, выполните один раз `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Без `HUNTER_DATABASE_URL` команда завершается с кодом 2 и понятным сообщением до любых сетевых вызовов. Пароль из адреса базы на экран не выводится. Сама база появится в S1-06.

## Проверки (то же, что в CI)

```bash
python -m pytest -q --durations=5
python -m ruff check src tests
python -m ruff format --check src tests
```

Тесты не ходят в сеть: любое соединение дальше loopback роняет тест (`tests/conftest.py`). У каждого теста таймаут 10 с.

## Коды выхода

`0` — успех, `1` — операция отклонена (код ошибки из документа 10), `2` — ошибка конфигурации.
