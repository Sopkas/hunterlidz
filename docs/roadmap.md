# План Hunter v0

Backlog и критерии — из `13-tests-and-roadmap.md`. Каждая задача — отдельная ветка и PR, ветки стоят стеком друг на друге (часть 02 «Как мы работаем»).

| ID | Работа | Зона | Ветка | Статус |
| --- | --- | --- | --- | --- |
| H-01 | Каркас, конфигурация, роли, CI | A | `claude/H-01-project-foundation` | готово к ревью |
| H-02 | База, миграции, аудит, пользователи, режимы | A | `claude/H-02-storage-audit` | готово к ревью |
| H-03 | Импорт, нормализация, дедупликация, отсев | B | `claude/H-03-import-dedup` | готово к ревью |
| H-04 | Чтение источников, evidence, база знаний | B | `claude/H-04-sources-evidence` | готово к ревью |
| H-05 | LLM-адаптеры, бюджеты, jobs, research_v1 | B | `claude/H-05-llm-research` | готово к ревью |
| H-06 | Scoring и eligibility | B | `claude/H-06-scoring-eligibility` | готово к ревью |
| H-07 | Черновики, версии, approval | A/B | `claude/H-07-drafts-approval` | готово к ревью |
| H-08 | Telegram-панель | A | — | не в этом стеке |
| H-09 | Ручная отправка, ответы, suppression | A | `claude/H-09-send-replies-suppression` | план |
| H-10 | Follow-up, nurture, планировщик | A | `claude/H-10-followup-scheduler` | план |
| H-11 | Экспорт, метрики, backup / restore | A | `claude/H-11-export-metrics` | план |
| H-12 | Shadow и ручной пилот | — | — | после H-11 |
| H-13 | Адаптер отправки | — | — | после пилота |
| H-14 | Регулярный discovery | — | — | после пилота |

Интерфейс оператора в этом стеке — CLI. HTTP API `/v1` и Telegram-панель (H-08) подключаются к тем же сценариям `application` отдельными задачами.
