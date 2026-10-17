# Hunter v0

Исследование B2B-компаний, scoring по правилам команды и черновики обращения с согласованием человеком. Отправка в v0 — вручную.

Спецификация — документы 08–13 в базе знаний команды (`documents/`, в репозиторий не входит). Слои — `docs/architecture.md`, план и соответствие задач — `docs/roadmap.md`, журнал — `PROJECT_STATUS.md`, правила для агентов — `AGENTS.md`.

Сейчас в `main` переносится по задачам код ветки `claude/H-01-H-11-hunter-v0` (план — `docs/roadmap.md`). Готовы каркас, проверка конфигурации на старте, `hunter config` и `hunter whoami` — учётная запись ОС, от имени которой действует Hunter (переменные окружения на неё не влияют).

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

Без `HUNTER_DATABASE_URL` команда завершается с кодом 2 и понятным сообщением до любых сетевых вызовов. Пароль из адреса базы на экран не выводится. Пока значение только проверяется: таблицы и миграции появятся в S1-07.

## База

PostgreSQL для разработки и тестов поднимается в Docker (нужен Docker Desktop или Docker Engine с Compose). Команда одна для всех ОС:

```bash
docker compose up -d --wait     # база hunter на 127.0.0.1:5432, готова, когда команда вернулась
```

В `.env`: `HUNTER_DATABASE_URL=postgresql://hunter:hunter@localhost:5432/hunter`. Порт открыт только на этой машине. Пароль известен всем — это база для разработки, не для прода.

Остановить — `docker compose down`, удалить вместе с данными и начать с нуля — `docker compose down -v`. Почему PostgreSQL, SQLAlchemy Core и Alembic — `docs/adr/0003-postgresql-sqlalchemy-alembic.md`.

## Проверки (то же, что в CI)

```bash
python -m pytest -q --durations=5
python -m ruff check src tests
python -m ruff format --check src tests
```

Тесты не ходят в сеть: любое соединение дальше loopback роняет тест, в том числе соединение PostgreSQL (`tests/conftest.py`). У каждого теста таймаут 10 с.

Тесты хранилища берут базу из `HUNTER_TEST_DATABASE_URL`. Без переменной они пропускаются, и в итоге pytest видно `skipped`. С базой из compose:

```bash
HUNTER_TEST_DATABASE_URL=postgresql://hunter:hunter@localhost:5432/hunter python -m pytest -q          # macOS / Linux
$env:HUNTER_TEST_DATABASE_URL="postgresql://hunter:hunter@localhost:5432/hunter"; python -m pytest -q  # PowerShell
```

В CI на Linux база поднимается service-контейнером, и там пропуск считается ошибкой. На Windows-джобе тесты хранилища пропускаются: GitHub запускает service-контейнеры только на Linux (ADR-0003).

## Коды выхода

`0` — успех, `1` — операция отклонена (код ошибки из документа 10), `2` — ошибка конфигурации.
