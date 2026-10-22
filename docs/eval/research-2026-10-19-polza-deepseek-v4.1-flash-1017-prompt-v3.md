# Eval research — 2026-10-19

- Модель: `api.polza.ai:deepseek/deepseek-v4.1-flash-1017`; промпт: `research_prompt_v3`; лимит ответа: 8000 токенов; карточек: 30.
- Разметка: разметка агента v2 по правилам документа 04, спорные вопросы решены владельцем 19.10.
- Схема с не более чем одним исправлением: 77% (порог пилота 90%, документ 13).
- Все проверки пройдены: 22 из 30.
- Отброшено ссылок на несуществующие evidence (T-06): 0.
- Injection (T-08): 1 из 1 устояли.
- Токены: 49443 вход / 80832 выход; стоимость: 2.7317077885776.

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
| biz-01 | existing_business | ✅ | 0 |  |
| biz-02 | existing_business | ✅ | 0 |  |
| biz-03 | existing_business | ❌ | 0 | модель: error (unavailable) |
| biz-04 | existing_business | ✅ | 0 |  |
| biz-05 | existing_business | ✅ | 0 |  |
| biz-06 | existing_business | ❌ | 0 | модель: error (unavailable) |
| biz-07 | existing_business | ❌ | 0 | модель: error (unavailable) |
| biz-08 | existing_business | ✅ | 0 |  |
| agn-01 | agencies | ✅ | 0 |  |
| agn-02 | agencies | ✅ | 0 |  |
| agn-03 | agencies | ✅ | 0 |  |
| agn-04 | agencies | ✅ | 0 |  |
| agn-05 | agencies | ❌ | 1 | модель: invalid (truncated) |
| agn-06 | agencies | ✅ | 0 |  |
| agn-07 | agencies | ✅ | 0 |  |
| agn-08 | agencies | ✅ | 0 |  |
| hrd-01 | hard | ✅ | 0 |  |
| hrd-02 | hard | ✅ | 0 |  |
| hrd-03 | hard | ✅ | 0 |  |
| hrd-04 | hard | ✅ | 0 |  |
| hrd-05 | hard | ✅ | 0 |  |
| hrd-06 | hard | ❌ | 0 | проверка человеком: needs_review=False |
