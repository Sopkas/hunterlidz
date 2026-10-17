# Контракт сообщений и согласования (S3-01, КОНТРАКТ)

Согласование относится к точной версии текста и точному адресату. Код — `src/hunter/domain/messages.py`, инварианты — `tests/test_messages_contract.py`. Источники: документ 10 (`messages`, `message_versions`, `approvals`, «Состояния сообщения», «Пример согласования»), документ 12 (`APPROVAL_TTL_HOURS=24`), сценарий T-11.

Изменение этих типов — отдельным PR с пометкой КОНТРАКТ и Approve от обеих зон. Хранение и неизменяемость в базе — S3-02 (зона A), черновик — S3-03, Approve / Edit / Skip — S3-04.

## Типы

| Тип | Поля | Инварианты |
| --- | --- | --- |
| `Recipient` | contact_id, channel, normalized_address | Снимок адресата в момент версии. `channel` — `Channel` из S1-05, адрес нормализован (S1-13) |
| `MessageVersion` | message_id, version, body, recipient, payload_hash, evidence_ids, case_claim_ids, created_by, created_at | Неизменяема. Текст не пустой. `payload_hash` обязан совпадать с содержимым. ID без повторов. `case_claim_ids` — ID утверждений реестра вида `auto_crm.experience:v1` (S2-03) |
| `Approval` | id, message_id, message_version, payload_hash, actor_id, approved_at, expires_at, revoked_at | `expires_at` позже `approved_at`, отзыв не раньше одобрения |
| `Message` | id, lead_id, kind, sequence_step, status, current_version, approved_version, sent_at | `approved_version` есть ровно у approved и sent и равна текущей версии. `sent_at` есть ровно у sent. Первое сообщение — шаг 0, follow-up — шаг 1 и дальше |

## Хеш согласования — `payload_hash`

sha256 канонического JSON (ключи по алфавиту, без пробелов, UTF-8) из `message_id`, `version`, `contact_id`, `channel`, `normalized_address` и точных байтов `body`, как в документе 10. Любое изменение текста — даже пробел или ё/е — или адресата даёт другой хеш.

`MessageVersion.create` считает хеш сам. `Approval.grant` берёт хеш из сохранённой версии: хеш, присланный клиентом, ничего не доказывает.

## Действительность согласования — `Approval.valid_for(version, recipient, now)`

Согласование действует, пока:
- оно не отозвано;
- `now` раньше `expires_at` (24 часа, `APPROVAL_TTL`);
- совпадают сообщение, номер версии и хеш;
- адресат сейчас — тот же, что в снимке версии: `recipient` — контакт, как он хранится в момент проверки.

Поэтому:
- **правка текста** — новая версия, и прежнее согласование к ней не подходит (T-11);
- **смена адресата при том же тексте** — тоже новая версия и тоже недействительное согласование, как требует документ 10;
- **правка контакта после согласования** — версия не меняется, но текущий адресат уже другой, и согласование недействительно. Документ 10: «Изменение контакта аннулирует связанные approval, даже если текст не изменился».

Ответ лида, запрет контакта и права того, кто отправляет, проверяет сценарий перед отправкой (S3-04): это не свойство самого согласования.

## Статусы — `check_transition`

| Из | Можно в |
| --- | --- |
| draft | draft (новая версия), pending_review, cancelled |
| pending_review | approved, rejected, draft (правка), cancelled |
| approved | sent, draft (правка отзывает согласование), cancelled |
| sent, rejected, cancelled | никуда |

Отправленное нельзя «отменить» сменой статуса (документ 10). Ответ или запрет переводит неотправленное в `cancelled`. Запрещённый переход — `InvalidTransitionError`, код ошибки `INVALID_TRANSITION`.

## Отклонения от документа 10 и решения для ревью

| # | Что | Почему |
| --- | --- | --- |
| 1 | Нет статусов адаптера отправки: queued, sending, failed, send_unknown | В v0 адаптера нет, как и `assisted_send` в `RunMode` (S1-05). Появятся вместе с адаптером (H-13) |
| 2 | В `Message` нет `contact_id`, `channel`, `body`, `body_hash` | Текущий текст и адресат лежат в текущей версии (`current_version`). Две копии разошлись бы, а согласование всё равно сверяется с версией |
| 3 | `sequence_step`: 0 — первое сообщение, follow-up от 1, ответ — любой шаг | В документе 10 правила нет. Так шаги цепочки follow-up (4, 9, 25 дней, документ 12) нумеруются с 1 |
| 4 | `case_claim_ids` — `claim_key:vN` | В `case_claims` документа 10 нет `claim_key`. Отклонение записывается в S1-07 (решение по S2-03, ревью #7) |
| 5 | В версии нет `review_reasons` и `blockers` старой ветки | Это результат проверки текста (S3-03), а не часть снимка. Проверка запускается заново на каждую версию |
| 7 | `expected_version` в API документа 10 — это `Message.current_version` | Отдельной версии строки у `Message` нет. Смена статуса без нового текста защищена блокировкой строки (`SELECT … FOR UPDATE`, S3-02) и таблицей переходов: второе «Отправлено» упирается в `sent → sent` (T-12) |
| 6 | Срок согласования — константа `APPROVAL_TTL` | Настройка `APPROVAL_TTL_HOURS` из документа 12 читается конфигурацией S3-04 |

## Отличия от ветки `claude/H-01-H-11-hunter-v0` (`174c737`)

- `Recipient.channel` — `Channel`, а не строка.
- Проверка «помощник описан как готовый» убрана: её заменяет `check_case_text` из S2-03.
- `Approval.grant` и `MessageVersion.create` считают хеш сами. Добавлены инварианты `Message` и таблица переходов.
