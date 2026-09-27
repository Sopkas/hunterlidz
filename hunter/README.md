# Hunter v0

Спецификация и бизнес-правила — в `../documents/` (номера «док. 04», «док. 11» в коде указывают туда).

Объявления с одной площадки (CSV/JSON, 100–200 шт.) → отсев мусора → score в коде (док. 04) → AI-карточки для лучших → ручные Approve / Edit / Skip → экспорт одобренных. Сообщения программа не отправляет: это делает оператор вручную.

## Установка

```
py -m pip install -r requirements.txt
copy .env.example .env           # и впишите ключи в .env
```

Ключи и выбор модели — в `hunter/.env` (он в `.gitignore`). Значения из `.env` важнее переменных окружения.

## Работа

```
py hunter.py import data\sample_listings.json          # created / matched / rejected + причины отсева
py hunter.py analyze --top 20 --min-score 60           # AI только для WARM/HOT, не больше 20 за запуск
py hunter.py review                                    # a — Approve, e — Edit, s — Skip, n — позже, q — выход
py hunter.py export approved.csv                       # одобренные карточки (UTF-8, открывается в Excel)
py hunter.py stats
py test_hunter.py                                      # проверки T-01, T-03–T-07, T-11
```

Опции: `--db` / `HUNTER_DB` (по умолчанию `hunter.db`), `import --platform имя --suppress запреты.txt --today YYYY-MM-DD`.

Модель: `HUNTER_LLM=gemini|claude`. Без неё используется Gemini, если задан `GEMINI_API_KEY` (бесплатный тариф Google AI Studio, модель `HUNTER_GEMINI_MODEL`, по умолчанию `gemini-flash-lite-latest`; у `gemini-flash-latest` бесплатно всего 20 запросов в день). Иначе — Claude (`ANTHROPIC_API_KEY`, модель `HUNTER_MODEL`, по умолчанию `claude-haiku-4-5`). На бесплатном тарифе Google может использовать запросы для обучения — реальные данные клиентов туда не отправлять.

## Формат входа

Колонки распознаются по синонимам: `id`, `title`, `description`, `company`, `url`, `budget` («400 000 ₽», «до 500к», «договорная»), `date` (`YYYY-MM-DD` или `DD.MM.YYYY`), `contact`, `contact_name`, `platform`. JSON — массив или `{"items": [...]}`.

## Что где

- **Отсев** (`junk_reason`): пусто, спам, запрет контакта, исполнитель вместо заказчика, нет задачи разработки / не наш профиль (SEO, SMM…), бюджет < 30 000 ₽, старше 180 дней, дубль текста.
- **Score** (`score`): ICP 25 + сигнал 30 + problem fit 20 + бюджет 15 + контакт 10. Дата неизвестна → сигнал ≤ 10. Problem fit 20 только с кейсом в статусе `verified`; сейчас таких нет (`CASE_CLAIMS`).
- **AI** (`analyze`): один вызов на лид, структурированный JSON. Код проверяет evidence_id, id кейсов, длину, шаблоны и кейс претензионщика. Ошибка или отказ → `ai_error`, повторный `analyze` подхватит лид.
- **Approve** привязан к версии и hash текста. Edit создаёт новую версию и отзывает прежнее одобрение. Всё пишется в `events`.
