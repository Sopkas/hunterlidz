# ADR-0003. SQLite, CLI вместо Telegram, структура каталогов

**Статус:** предложено — требуется решение координатора зоны A и PM.
**Дата:** 2026-09-27.

## 1. SQLite вместо PostgreSQL

doc 09 допускает любую реляционную СУБД с транзакциями, внешними ключами и уникальными ограничениями («PostgreSQL-подобные возможности»). Взят stdlib `sqlite3`: без новых зависимостей и сервера, с `BEGIN IMMEDIATE`, внешними ключами, частичными уникальными индексами и CHECK. Ограничение — один писатель; для пилота (doc 08: до 1 000 компаний, 2 worker) достаточно. Переход на PostgreSQL затрагивает только `infrastructure/sqlite`. Миграции — нумерованные SQL-файлы с таблицей `schema_migrations`.

## 2. CLI вместо Telegram-панели в v0

doc 12 задаёт Telegram-панель как интерфейс оператора (H-08). В этом стеке интерфейс — CLI, панель не сделана. Все сценарии (Approve / Edit / Skip, отправка, ответы) находятся в `application` и не зависят от CLI, поэтому панель добавляется слоем `presentation/telegram` без изменения логики. Решение о сроке H-08 — за PM.

## 3. Структура каталогов

Онбординг (стр. 6) *предлагает* `src/api, src/domain, src/application, src/adapters, migrations/, tests/, docs/`. Правила «Как мы работаем» (часть 03) и пример ArtemProd используют `domain / application / infrastructure / presentation / runtime`. Принято второе:

| Онбординг | Реализация |
| --- | --- |
| `src/domain` | `src/hunter/domain` |
| `src/application` | `src/hunter/application` |
| `src/adapters` | `src/hunter/infrastructure` (+ интерфейсы в `application/ports.py`) |
| `src/api` | `src/hunter/presentation/cli` (HTTP API `/v1` не сделан) |
| `migrations/` | `src/hunter/infrastructure/sqlite/migrations/` (устанавливаются вместе с пакетом) |
| `tests/`, `docs/` | `tests/`, `docs/` |

Если команда предпочитает раскладку онбординга — это переименование каталогов без изменения кода.
