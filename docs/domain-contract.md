# Контракт доменных типов (S1-05, КОНТРАКТ)

Общие типы зон A и B. Код — `src/hunter/domain/catalog.py`, `entities.py`, `modes.py`; инварианты — `tests/test_domain_contract.py`. Источники: документ 04 (стадии, приоритет), документ 10 (сущности), документ 12 (режимы). Изменение этих типов — отдельным PR с пометкой КОНТРАКТ и Approve от обеих зон (пока в зонах по одному человеку, Approve за обе даёт тимлид — координатор зон).

## Общие правила

- Объект либо валиден, либо не создаётся: проверки в `__post_init__`, `frozen=True, slots=True`.
- Неизвестное — `None`, а не заглушка: пустая строка, `UNKNOWN` или 0 не используются.
- Момент времени — `datetime` с часовым поясом (UTC); наивный `datetime` отклоняется. Дата события — календарная `date` с точностью.
- Три разных понятия — три разных типа: **приоритет** исследования `Priority` (HOT / WARM / WATCH / SKIP, считается из score), **стадия** CRM `Stage` (NEW … NURTURE), **статус сообщения** (draft / approved / sent — появится в S3-01). Строка вместо перечисления не принимается.

## Типы

| Тип | Поля | Инварианты |
| --- | --- | --- |
| `Icp`, `Product`, `PRODUCT_FOR_ICP` | 3 ICP документа 03 → 3 оффера документа 02 | каждому ICP — один оффер по умолчанию; это не инвариант: `Lead` пару ICP / оффер не проверяет (документ 02) |
| `Stage` | NEW, RESEARCHED, APPROVED, CONTACTED, REPLIED, QUALIFIED, CALL, PROPOSAL, NEGOTIATION, WON, LOST, NURTURE | WON и LOST — закрытые (`CLOSED_STAGES`); переходы — отдельной задачей |
| `Priority`, `priority_for` | HOT, WARM, WATCH, SKIP | 80–100 / 60–79 / 40–59 / 0–39; вне 0–100 — ошибка |
| `RunMode` | shadow, manual, paused | какие действия разрешены в режиме — S1-08 |
| `Company` | id, name, version, normalized_domain, industry, size_min, size_max, location, merged_into_id | `id` не пустой; имя не пустое; домен — регистрируемый, ASCII/punycode, нижний регистр, без `www.` и точки в конце; приводит к нему `company_domain` (S1-09); size_min ≤ size_max, не отрицательные; нельзя слиться с собой |
| `Source` | id, canonical_url, source_type, fetch_status, fetched_at, content_hash, snapshot_ref, error_code | `id` не пустой; абсолютный http(s) URL; полученный источник хранит время и hash и не имеет кода ошибки; неудачный — обязательно с кодом ошибки |
| `Evidence` | id, company_id, source_id, claim, excerpt, observed_at, event_at, date_precision, verification_status, version | ID не пустые; утверждение и цитата не пустые; `event_at` пуст ровно тогда, когда точность `unknown`; при `month` — 1-е число, при `year` — 1 января |
| `Contact` | id, company_id, channel, normalized_address, version, name, role, source_id, verified_at | ID не пустые; адрес не пустой и без пробелов по краям; канал — `Channel` |
| `Lead` | id, company_id, campaign_id, stage, version, icp, product, priority, owner_id, next_action_at, loss_reason | ID не пустые; `stage` — только `Stage`; `loss_reason` есть ровно у LOST; `active` — стадия не WON / LOST |

## Отклонения от документа 10 и решения ревью

| # | Что | Почему |
| --- | --- | --- |
| 1 | `Source.error_code` — нет в таблице `sources` | FR-03: недоступность источника должна быть явной; код ошибки хранится рядом со статусом |
| 2 | `Evidence.event_at` — календарная `date`, а не момент RFC 3339 | Дата события известна с точностью до дня / месяца / года (`date_precision`); время суток не выдумываем. Период хранится первым днём: при `month` — 1-е число, при `year` — 1 января, чтобы один факт имел одну запись (документ 11). Консервативную границу свежести считает S2-08 из точности. Принято на ревью 16.10 |
| 3 | Значения `VerificationStatus`: unverified / verified / rejected | В документе 10 есть поле, но нет значений. Принято на ревью 16.10: `Evidence` создаётся только после того, как код нашёл `excerpt` в полученном источнике (S2-02, T-06), поэтому статус — решение человека, а не машинная проверка. `rejected` — конечное: факт не возвращается и не участвует ни в score, ни в черновике (S2-08) |
| 4 | Значения `Channel`: email, telegram, phone, web, other | В документе 10 нет перечня. Ответ через другие площадки — `other`. Принято на ревью 16.10 |
| 5 | Значения `SourceType`: website, conversation, manual | В документе 10 нет перечня. Объявлений в v0 нет (решение владельца 17.10): компания, найденная через объявление, импортируется как `website` или `manual` |
| 6 | `created_at`, `updated_at` не входят в доменные типы | Это метаданные хранения, их ведёт база (S1-07); `version` в типах есть — она нужна для `expected_version` |
| 7 | Формат UUID у ID не проверяется в домене | ID создаёт генератор, тип колонки проверяет база (S1-07); домен не зависит от способа генерации |
| 8 | `RunMode` без `assisted_send` (документ 12) | Адаптера отправки в v0 нет; значение добавляется вместе с ним (H-13) |
| 16 | `Evidence.version` (КОНТРАКТ, ревью #14) | Документ 10: у изменяемой сущности есть `version`. У факта после создания меняется только `verification_status` — решение оператора, и это идёт с `expected_version`. Утверждение и цитата не меняются: другой текст — новый факт. Колонку добавляет миграция `0002` (зона A). У `Source` версии нет, потому что **запись источника — снимок одного получения**: повторное получение страницы — новая запись, уникальность `(source_type, canonical_url, content_hash)`. Так `Evidence.source_id` указывает ровно на тот текст, где нашли цитату, и новое получение не подменяет его задним числом (T-06). Решение тимлида по ревью #16, вариант (а) |

## Отличия от ветки `claude/H-01-H-11-hunter-v0`

- Добавлен тип `Company` — в ветке его не было, компания жила только в хранилище.
- У `Source` нет `company_id`: по документу 10 компанию и источник связывает `Evidence`. `snapshot` (текст) заменён на `snapshot_ref` (ссылка на снимок), как в документе 10.
- У `Lead` нет `score_total`, `prescore_total`, `needs_review`: score хранится отдельной таблицей `scores` (документ 10). Добавлены `owner_id`, `next_action_at`.
- `Icp.UNKNOWN` убран: неизвестный ICP — `None`.
- У `Contact` нет `company_wide`: это вывод для scoring (S2-08), а не поле записи.
- `Operation` и `allows()` из `modes.py` перенесутся в S1-08 вместе со сценарием смены режима.
