# Eval research — 2026-10-19

- Модель: `openrouter:nvidia/nemotron-3-super-120b-a12b:free`; промпт: `research_prompt_v2`; карточек: 30.
- Разметка: разметка агента v1 по правилам документа 04, спорные вопросы решены владельцем 19.10.
- Схема с не более чем одним исправлением: 83% (порог пилота 90%, документ 13).
- Все проверки пройдены: 11 из 30.
- Отброшено ссылок на несуществующие evidence (T-06): 0.
- Injection (T-08): нет карточек.
- Токены: 67854 вход / 102291 выход; стоимость: 0.

| Карточка | Тип | Итог | Исправлений | Не прошло |
| --- | --- | --- | --- | --- |
| fnd-01 | founders | ❌ | 0 | score: 65, ожидалось 75–95 |
| fnd-02 | founders | ❌ | 1 | неизвестные: в missing_data нет ['бюджет'] |
| fnd-03 | founders | ❌ | 0 | score: 55, ожидалось 60–80 |
| fnd-04 | founders | ❌ | 1 | модель: invalid (truncated) |
| fnd-05 | founders | ❌ | 0 | score: 65, ожидалось 75–95; неизвестные: в missing_data нет ['бюджет'] — проверка отбросила: contact_level: нет подтверждающего evidence, признак считается неизвестным |
| fnd-06 | founders | ❌ | 0 | score: 65, ожидалось 70–90 |
| fnd-07 | founders | ✅ | 0 |  |
| fnd-08 | founders | ❌ | 0 | score: 45, ожидалось 75–95 |
| biz-01 | existing_business | ❌ | 0 | score: 65, ожидалось 75–95 |
| biz-02 | existing_business | ✅ | 1 |  |
| biz-03 | existing_business | ❌ | 1 | оффер: mvp_launch, ожидался business_automation; score: 65, ожидалось 70–90 — проверка отбросила: contact_level: нет подтверждающего evidence, признак считается неизвестным |
| biz-04 | existing_business | ❌ | 1 | модель: invalid (truncated) |
| biz-05 | existing_business | ❌ | 1 | оффер: external_product_team, ожидался business_automation; score: 65, ожидалось 75–95 |
| biz-06 | existing_business | ❌ | 1 | модель: invalid (truncated) |
| biz-07 | existing_business | ❌ | 0 | score: 55, ожидалось 65–85 — проверка отбросила: contact_level: нет подтверждающего evidence, признак считается неизвестным |
| biz-08 | existing_business | ✅ | 0 |  |
| agn-01 | agencies | ✅ | 0 |  |
| agn-02 | agencies | ✅ | 0 |  |
| agn-03 | agencies | ✅ | 0 |  |
| agn-04 | agencies | ❌ | 1 | score: 45, ожидалось 65–85 — проверка отбросила: contact_level: нет подтверждающего evidence, признак считается неизвестным |
| agn-05 | agencies | ✅ | 0 |  |
| agn-06 | agencies | ✅ | 0 |  |
| agn-07 | agencies | ❌ | 0 | score: 55, ожидалось 70–90 |
| agn-08 | agencies | ✅ | 0 |  |
| hrd-01 | hard | ✅ | 0 |  |
| hrd-02 | hard | ✅ | 0 |  |
| hrd-03 | hard | ❌ | 1 | модель: invalid (truncated) |
| hrd-04 | hard | ❌ | 0 | модель: error (unavailable) |
| hrd-05 | hard | ❌ | 0 | оффер: external_product_team, ожидался business_automation; score: 65, ожидалось 70–90 |
| hrd-06 | hard | ❌ | 0 | проверка человеком: needs_review=False |
