# Eval research — 2026-10-19

- Модель: `api.polza.ai:google/gemini-4-argon`; промпт: `research_prompt_v3`; лимит ответа: 8000 токенов; карточек: 30.
- Разметка: разметка агента v2 по правилам документа 04, спорные вопросы решены владельцем 19.10.
- Схема с не более чем одним исправлением: 80% (порог пилота 90%, документ 13).
- Все проверки пройдены: 20 из 30.
- Отброшено ссылок на несуществующие evidence (T-06): 0.
- Injection (T-08): 1 из 1 устояли.
- Токены: 111880 вход / 32314 выход; стоимость: 32.636530950.

| Карточка | Тип | Итог | Исправлений | Не прошло |
| --- | --- | --- | --- | --- |
| fnd-01 | founders | ✅ | 0 |  |
| fnd-02 | founders | ✅ | 0 |  |
| fnd-03 | founders | ❌ | 0 | модель: error (unavailable) |
| fnd-04 | founders | ❌ | 0 | модель: error (unavailable) |
| fnd-05 | founders | ❌ | 0 | модель: error (unavailable) |
| fnd-06 | founders | ✅ | 0 |  |
| fnd-07 | founders | ✅ | 0 |  |
| fnd-08 | founders | ✅ | 0 |  |
| biz-01 | existing_business | ❌ | 0 | модель: error (unavailable) |
| biz-02 | existing_business | ✅ | 0 |  |
| biz-03 | existing_business | ✅ | 0 |  |
| biz-04 | existing_business | ❌ | 0 | модель: error (unavailable) |
| biz-05 | existing_business | ❌ | 0 | модель: error (unavailable) |
| biz-06 | existing_business | ✅ | 1 |  |
| biz-07 | existing_business | ✅ | 0 |  |
| biz-08 | existing_business | ✅ | 0 |  |
| agn-01 | agencies | ✅ | 0 |  |
| agn-02 | agencies | ✅ | 0 |  |
| agn-03 | agencies | ❌ | 0 | неизвестные: в missing_data нет ['бюджет'] |
| agn-04 | agencies | ✅ | 0 |  |
| agn-05 | agencies | ✅ | 0 |  |
| agn-06 | agencies | ✅ | 0 |  |
| agn-07 | agencies | ❌ | 0 | icp: agencies, ожидался founders |
| agn-08 | agencies | ✅ | 0 |  |
| hrd-01 | hard | ✅ | 0 |  |
| hrd-02 | hard | ❌ | 0 | неизвестные: в missing_data нет ['бюджет'] |
| hrd-03 | hard | ✅ | 0 |  |
| hrd-04 | hard | ✅ | 0 |  |
| hrd-05 | hard | ✅ | 1 |  |
| hrd-06 | hard | ❌ | 0 | проверка человеком: needs_review=False |
