# Архитектура Hunter v0

Реализация документа `09-hunter-architecture.md` и правил «Как мы работаем», часть 03. Спецификация — в папке знаний (`HUNTER_KNOWLEDGE_DIR`, по умолчанию `documents/`, в репозиторий не входит).

## Слои

```
src/hunter/
  domain/          сущности, правила, расчёты (scoring, стадии, approval, follow-up) — без сети, БД, AI и часов
  application/     сценарии (импорт, research, черновики, согласование, отправка, ответы, экспорт)
                   и интерфейсы адаптеров (ports.py)
  infrastructure/  SQLite, LLM-клиенты, чтение источников, файлы, база знаний — реализуют ports
  presentation/    CLI оператора — показывает результат, не считает
  runtime/         конфигурация, связывание зависимостей, запуск
```

Зависимости идут в одну сторону: `presentation → application → domain`. `infrastructure` реализует интерфейсы `application` и использует типы `domain`. `runtime` собирает всё вместе и не содержит бизнес-правил.

## Принципы

- **Сценарий — единственный координатор.** Адаптеры не вызывают друг друга. AI получает готовые данные и не пересчитывает score.
- **Score считает код** (`domain/scoring.py`, `RULES_VERSION`). Модель только предлагает признаки со ссылками на evidence.
- **Время и идентификаторы — параметрами.** Домен получает `now`/`today` явно; `datetime` всегда в UTC, календарная дата — в деловом поясе (`HUNTER_BUSINESS_UTC_OFFSET`).
- **Интерфейсы — только на реальных границах**: хранилище, LLM, чтение источников, база знаний, часы.
- **Неизвестно ≠ ноль.** Неизвестное — `None` и причина в списке пропусков.
- **Версии — явно**: `RULES_VERSION`, `PRESCORE_VERSION`, `PROMPT_VERSION`, `schema_version` в payload.

## Карта модулей

| Слой | Модули |
| --- | --- |
| domain | `catalog` (ICP, продукты, стадии, приоритет, деньги), `listings`, `contacts`, `screening`, `prescore`, `entities`, `cases`, `research`, `budget`, `scoring`, `messages`, `drafts`, `replies`, `stages`, `followup`, `metrics`, `roles`, `modes`, `users` |
| application | `access`, `import_listings`, `sources`, `case_claims`, `research`, `llm_calls`, `prompts`, `scoring`, `messaging`, `lead_stage`, `outreach`, `followups`, `reporting`, `ports` |
| infrastructure | `sqlite/*` (миграции `0001`–`0007`, репозитории по таблицам, backup), `llm/gemini`, `llm/claude`, `fetcher`, `knowledge`, `listing_files`, `system` |
| presentation | `cli/*` — по модулю на группу команд |
| runtime | `config`, `container`, `main` |

## Режимы работы

`shadow` (research и черновики, отправка отключена), `manual` (ручная отправка с подтверждением), `paused` (только чтение, ответы и запреты). `assisted_send` в v0 не реализован: `dispatch` возвращает `ADAPTER_DISABLED`. Режим переключает администратор, переключение записывается в аудит.

## Коды выхода CLI

`0` — успех, `1` — операция отклонена (`HunterError` с кодом из doc 10), `2` — ошибка конфигурации (до любых сетевых вызовов).
