# Eval research — 2026-10-19

- Модель: `api.polza.ai:openai/gpt-6-luna`; промпт: `research_prompt_v3`; лимит ответа: 8000 токенов; карточек: 30.
- Разметка: разметка агента v2 по правилам документа 04, спорные вопросы решены владельцем 19.10.
- Схема с не более чем одним исправлением: 80% (порог пилота 90%, документ 13).
- Все проверки пройдены: 19 из 30.
- Отброшено ссылок на несуществующие evidence (T-06): 0.
- Injection (T-08): 1 из 1 устояли.
- Токены: 57737 вход / 48223 выход; стоимость: 1.78341425260.

| Карточка | Тип | Итог | Исправлений | Не прошло |
| --- | --- | --- | --- | --- |
| fnd-01 | founders | ✅ | 0 |  |
| fnd-02 | founders | ✅ | 0 |  |
| fnd-03 | founders | ❌ | 0 | модель: error (unavailable) |
| fnd-04 | founders | ❌ | 0 | модель: error (unavailable) |
| fnd-05 | founders | ❌ | 0 | модель: error (unavailable) |
| fnd-06 | founders | ❌ | 0 | score: 45, ожидалось 55–75 (signal 10 (в разметке 30)) — проверка отбросила: signals[0]: событие в будущем (2026-11-01) не считается произошедшим |
| fnd-07 | founders | ✅ | 0 |  |
| fnd-08 | founders | ✅ | 0 |  |
| biz-01 | existing_business | ❌ | 0 | модель: error (unavailable) |
| biz-02 | existing_business | ✅ | 0 |  |
| biz-03 | existing_business | ✅ | 0 |  |
| biz-04 | existing_business | ❌ | 0 | модель: error (unavailable) |
| biz-05 | existing_business | ❌ | 0 | модель: error (unavailable) |
| biz-06 | existing_business | ✅ | 0 |  |
| biz-07 | existing_business | ✅ | 0 |  |
| biz-08 | existing_business | ✅ | 0 |  |
| agn-01 | agencies | ✅ | 0 |  |
| agn-02 | agencies | ✅ | 0 |  |
| agn-03 | agencies | ✅ | 0 |  |
| agn-04 | agencies | ✅ | 0 |  |
| agn-05 | agencies | ❌ | 0 | оффер: None, ожидался external_product_team; score: 55, ожидалось 70–90 (icp_fit 0 (в разметке 25)) — проверка отбросила: задача или масштаб подтверждены без совпадения сегмента |
| agn-06 | agencies | ✅ | 0 |  |
| agn-07 | agencies | ❌ | 0 | score: 65, ожидалось 70–90 (problem_fit 10 (в разметке 20), budget 0 (в разметке 5)) |
| agn-08 | agencies | ✅ | 0 |  |
| hrd-01 | hard | ✅ | 0 |  |
| hrd-02 | hard | ✅ | 0 |  |
| hrd-03 | hard | ✅ | 0 |  |
| hrd-04 | hard | ✅ | 0 |  |
| hrd-05 | hard | ❌ | 0 | score: 55, ожидалось 70–90 (icp_fit 0 (в разметке 20), problem_fit 15 (в разметке 20)) — проверка отбросила: задача или масштаб подтверждены без совпадения сегмента |
| hrd-06 | hard | ❌ | 0 | проверка человеком: needs_review=False |
