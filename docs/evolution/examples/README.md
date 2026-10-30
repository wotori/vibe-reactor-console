# Примеры будущих контрактов

Эти файлы — часть спецификации, не готовый формат текущего CLI DEV/OS.

- `event.schema.json` — transport envelope и payload для `metric.sample`.
  Остальные topics требуют своих валидаторов при реализации.
- `demo-events.jsonl` — четыре демонстрационных нормализованных события.
  `recorded_offset_ms` задаёт порядок времени replay; source sequence — порядок
  внутри одного source session. Это фрагмент, а не готовая recording с manifest/checkpoints.
- `handoff.schema.json` — предлагаемый собственный формат завершённой передачи.
- `replay-event.example.json` — тот же demo sample при воспроизведении:
  `mode=replay`, обязательное исходное `origin_mode=demo`.
- `handoff.example.json` — синтетическое сообщение; нулевой hash и base являются
  placeholders, реального патча по этому ID нет. Семантический валидатор runtime
  дополнительно сверяет sender/recipient, task graph и настоящий content hash.
- `night-shift.preset.json` — неисполняемый reference preset. Он выбирает
  зарегистрированные будущие IDs; ядро текущей версии их ещё не загружает.

JSON Schema проверяет структуру и часть ограничений. Он не доказывает доступ
к файлу, корректность стоимости, истинность результата тестов или право
отправить запрос агенту. Это проверяют store/coordinator/artifact resolver.
