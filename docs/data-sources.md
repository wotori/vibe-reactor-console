# Настоящие сигналы, локальные проверки и записи

DEV/OS принимает данные через стандартную библиотеку Python: JSON-файл,
JSONL-файл/FIFO или HTTP(S). Рендер читает готовый неизменяемый снимок;
ввод/вывод выполняют workers. Подключение собственного сайта и конкретного
устройства требует соответствующего endpoint или bridge: DEV/OS их не создаёт.

Команды ниже запускаются из корня репозитория. Файлы в `examples/` — **фикстуры
для проверки контракта**, их числа не являются статистикой сайта или измерением
человека. Метка LIVE означает выбранный внешний транспорт; происхождение
содержимого этого транспорта определяет его поставщик.

## Выбор источника

```bash
python3 dev_show.py --screen site --source-file examples/site.json
python3 dev_show.py --screen body --binding pulse --heart-file examples/heart.json
python3 dev_show.py --screen site --source-stream examples/events.jsonl
```

`--source-file`, `--source-url` и `--source-stream` взаимоисключающие.
`--heart-file` подключается отдельно. Источники и токены не загружаются из
пресетов: подключение задаётся явным аргументом запуска.

Для endpoint с Bearer-авторизацией используйте `--source-token-env NAME` вместе
с `--source-url`: значение берётся из уже установленной переменной окружения.
В аргумент передаётся имя, а не токен; переменная и адрес не сохраняются в
пресете/capsule. Без явно выбранного URL этот флаг отклоняется.

| `--data-mode` | Поведение |
| --- | --- |
| `auto` | Подключённые метрики LIVE, остальные DEMO. Без подключений — конечный demo-сценарий. |
| `demo` | Файловые/HTTP/heart-подключения игнорируются; полностью синтетический сценарий. Явный `--git-root` остаётся настоящим Git. |
| `live` | Только выбранные подключения; неподключённые site/body-показатели имеют `UNAVAILABLE` и `--`. |

Режим можно сменить через палитру команд `P` → `Data / demo`, `live` или `auto`.
Это явный переход: при обрыве LIVE демо-посетители не подмешиваются.
Пауза сцены и меню останавливают demo, но не возраст физических данных.

## Snapshot сайта

[examples/site.json](../examples/site.json) показывает полный пример:

```json
{
  "schema_version": 1,
  "observed_at": "2026-10-31T12:00:00Z",
  "measurement_id": "upstream-measurement-123",
  "active_sessions": 28,
  "active_window_seconds": 60,
  "pageviews_per_minute": 84,
  "requests_per_second": 12.4,
  "pages": [{"path": "/ekza", "active_sessions": 12}],
  "collection_status": "ok"
}
```

| Поле | Нормализованная метрика | Смысл/единица |
| --- | --- | --- |
| `active_sessions` | `site.active_sessions_60s` | Анонимные активные сессии за 60 секунд, `sessions`. Это не доказанное число людей. |
| `authenticated_online` | `site.authenticated_online` | Пользователи по собственному presence-контракту backend, `users`. |
| `active_users_30m` | `site.active_users_30m` | Окно аналитики 30 минут, `users`; не мгновенный онлайн. |
| `pageviews_per_minute` | `site.pageviews_per_min` | Просмотры за минуту, `views/min`. |
| `requests_per_second` | `site.requests_per_sec` | Серверные запросы, `requests/s`; не посетители. |

`active_window_seconds` — положительное целое, по умолчанию 60. Иное окно
получает иной ключ, например `site.active_sessions_30s`: карточка `60s` не
будет выдавать это значение за свою метрику. `pages` — максимум 200 агрегатов;
это может быть top-N, поэтому их сумма не обязана равняться общему значению.
Query string и fragment удаляются из отображаемых путей.

Числа должны быть конечными; отрицательные site/git-счётчики не принимаются.
Отсутствующее измерение — `null`, не ноль. `collection_status`, отличный от
`ok`, делает значения этого snapshot недоступными. Raw IP, email и user IDs
для такой визуализации не нужны.

Можно передать произвольные именованные gauges:

```json
{
  "schema_version": 1,
  "observed_at": "2026-10-31T12:00:00Z",
  "metrics": {
    "site.requests_per_sec": {"value": 12.4, "unit": "requests/s", "window_seconds": 1},
    "room.temperature": {"value": 23.5, "unit": "C"}
  }
}
```

Значение элемента `metrics` может быть просто числом; канонические ключи тогда
получают известную единицу, остальные — `count`. Поддерживается и массив
объектов с `key`, `value`, `unit`, `window_seconds`. Каноническому ключу нельзя
присвоить другую единицу. Новая метрика принимается хранилищем, но автоматически
не создаёт новую карточку или binding: их регистрируют отдельно.

Собственный процесс должен обновлять snapshot **атомарно**: записать новый
файл рядом, затем `os.replace(temporary, target)`. Иначе промежуточный JSON
может дать краткую ошибку источника. Максимальный документ — 1 MiB,
максимум 128 метрик в одном snapshot.

## Время, кэш и ошибки

CLI использует poll примерно раз в **2 секунды**, timeout HTTP **2 секунды**,
`STALE` после **15 секунд**, `UNAVAILABLE` после **60 секунд**. У пульса свои
пороги: **3** и **10 секунд**. Настройки интервала/TTL/timeout доступны в API
`TelemetryHub`, отдельных CLI-флагов для них пока нет.

`observed_at` — время измерения upstream, ISO 8601 с timezone, а не время выдачи
кэшированного HTTP-ответа. Сначала учитывается известный возраст исходного
измерения, затем возраст растёт монотонными часами. Повтор того же
`measurement_id` или `observed_at` обновляет здоровье транспорта, но **не
освежает измерение**. Новое измерение с тем же числом и новым timestamp/ID
принимается. Порядок timestamps внутри одной upstream-сессии не откатывается.

Если timestamp неизвестен, используйте `null`; тогда известно только время
получения, а возраст upstream остаётся неизвестным. Фикстуры используют
постоянный `measurement_id`: повторный poll не делает их бесконечно свежими.
Источник без timestamp и measurement ID может подтвердить только получение
каждого ответа, но не новизну физического измерения.

Ошибки JSON, недоступный файл, HTTP 401/429 и timeout видны как состояние
источника. Последнее значение сохраняется до TTL, затем текущая карточка
становится `--`; история остаётся. Повторы запросов имеют exponential backoff
со небольшим jitter и ограничением около минуты. UI не ждёт сетевой запрос.

## HTTP endpoint

```bash
python3 dev_show.py --screen site --data-mode live \
  --source-url https://your-site.example/api/devos/metrics
```

Endpoint возвращает тот же JSON, что файл. Адрес задаётся пользователем;
DEV/OS не угадывает analytics stack и не устанавливает collector на сайт.
Embedded credentials в URL не допускаются. В CLI пока нет настройки bearer
token; для собственного Python-адаптера есть ссылка на переменную окружения:

```python
from devos.telemetry import HTTPJSONProvider, TelemetryHub

provider = HTTPJSONProvider("https://your-site.example/api/devos/metrics",
                            token_env="DEVOS_SITE_TOKEN")
hub = TelemetryHub(providers=(provider,), demo=False)
try:
    snapshot = hub.update(0)
finally:
    hub.close()
```

Переменная содержит токен локально; его значение не добавляется в preset или
экспорт. `update(0)` не ждёт первый HTTP-ответ: приложение вызывает `update`
своим циклом, пока worker публикует измерения.

## JSONL stream и FIFO

`--source-stream` читает дописываемый файл или named pipe неблокирующим чтением.
Событие принимается после `\n`; частичная строка ждёт продолжения. Поддержаны
rotation/truncation файла, не более 256 строк/1024 событий за poll и ограничение
строки 128 KiB. Для snapshot используйте `--source-file`, для растущего журнала —
`--source-stream`. Stdin не используется: он принадлежит интерфейсу/терминалу.

Первый терминал, с новым свободным именем FIFO:

```bash
mkfifo /tmp/devos-events.fifo
python3 dev_show.py --screen site --source-stream /tmp/devos-events.fifo
```

Во втором терминале:

```bash
cat examples/events.jsonl > /tmp/devos-events.fifo
```

Один конверт события на строку — как в
[examples/events.jsonl](../examples/events.jsonl):

```json
{
  "schema_version": 1,
  "id": "site-event-001",
  "topic": "metric.sample",
  "source_id": "site-collector",
  "source_session_id": "collector-run-1",
  "sequence": 1,
  "mode": "live",
  "observed_at": "2026-10-31T12:00:00Z",
  "received_at": "2026-10-31T12:00:00Z",
  "payload": {
    "key": "site.active_sessions_60s",
    "value": 28,
    "unit": "sessions",
    "quality": "ok",
    "window_seconds": 60
  }
}
```

Обязательны версия 1, непустые ID/source/session, неотрицательный целый
`sequence`, topic с точкой, mode, UTC/offset timestamps и object payload.
`quality`: `ok`, `stale`, `unavailable` или `error`; при последних двух `value`
равен `null`. Входной live-provider принимает только `mode: live`.
Payload метрики также может содержать `measurement_id` и `time_basis`;
первоначальный JSON Schema в `docs/evolution/examples/` был проектным эскизом,
этот документ описывает реализованный расширенный контракт.

Последовательность сравнивается внутри source/session. Дубликаты и старые
sequence не перезапускают эффект; новая session позволяет сбросить нумерацию.
Адаптер отображает ID настроенного подключения (`json-file`, `json-stream`,
`http-json`), сохраняет upstream source в payload и включает его в session ID.
Gauge-очередь хранит последнее значение, lifecycle-очередь ограничена; при
переполнении producer приостанавливается с явным `BACKPRESSURE`.
Неизвестный topic — данные журнала, а не команда для выполнения.

## Пульс и RR bridge

```bash
python3 dev_show.py --screen body --binding pulse --data-mode live \
  --heart-file /path/to/heart.json
```

[examples/heart.json](../examples/heart.json) показывает bridge-контракт:

```json
{
  "schema_version": 1,
  "measurement_id": "notification-123",
  "received_at": "2026-10-31T12:00:00Z",
  "heart_rate_bpm": 72,
  "rr_intervals_ms": [830, 836],
  "sensor_contact": "good",
  "timing_basis": "notification_received"
}
```

BPM — число 15…300 или `null`. RR optional: максимум 256 конечных интервалов
100…5000 мс в одном батче. Отсутствие RR не создаёт выдуманные измеренные удары.
Хранится до 256 RR-точек; повтор уведомления по ID/timestamp не дублирует батч.
`body.heart_rate` имеет единицу `bpm`, `body.rr_interval` — `ms` и последнее
реально переданное значение. BPM-only поток не получает demo-RR.

Если bridge знает время только получения уведомления, сохраняйте
`timing_basis: notification_received`: это не аппаратные timestamps каждого
удара. Художественная пульсация подписана как BPM-driven visual, **не ЭКГ**.
После потери сигнала число сначала устаревает, затем исчезает с `NO SIGNAL`.
Прямой BLE-драйвер или доступ к API конкретных часов в ядро не добавлен.

## Git и явные команды проверки

```bash
python3 dev_show.py --screen atlas --git-root .
python3 dev_show.py --task 'python3 -m unittest discover -s tests' --cwd .
```

На Atlas клавиша `D` меняет fixture ассетов на настоящее Git-представление.
Git-provider читает branch/revision, tracked/untracked status и ограниченное
дерево. Никаких fetch/checkout/merge или обновления index. Diff-reader
ограничивает вывод и не позволяет путям выйти из выбранного корня.

`--task` **настраивает**, но не запускает команду. Запуск: `P` → `Task / run
configured check`. Используется буквальный argv, без shell expansion. События
`task.test.started/completed` содержат run ID, известную Git revision и реальный
exit code; количество тестов/coverage не угадываются из произвольного текста.
Output ограничен 64 KiB, есть отмена и deadline. Demo-сборки имеют отдельный
topic `demo.build.finished` и не считаются настоящей проверкой.

## Recording и Replay

```bash
python3 dev_show.py --preset showcase --record /tmp/devos-flight.jsonl
python3 dev_show.py --replay /tmp/devos-flight.jsonl
```

Запись включается явно аргументом или командой `P` → `Recording / start or
stop`. Файл должен быть новым: существующий не перезаписывается. Поддерживается
суффикс `.jsonl.gz`. Асинхронный writer сохраняет manifest, seed, безопасные
параметры, события с относительными offsets и checkpoints. Часть ограничена
примерно 50 MiB несжатого JSON, затем открывается следующая; максимум 32 части.

Пульс выключен в записи по умолчанию; включение — `--record-pulse`.
Терминальный текст и полные prompts не записываются текущим UI. API Recorder
имеет отдельные opt-in категории; токены/credentials из metadata исключаются.
Stream View скрывает личные панели, но **не заменяет** выбор категорий записи:
если pulse opt-in уже включён, Stream View сам его не отменяет.

Replay читает только данные. Записанные command/handoff/approval никогда не
запускают процессы. CLI запрещает совмещать `--replay` с live-подключениями,
терминалами, задачами и managed-миссиями. Клавиши: `Space` play/pause,
`[`/`]` seek на 5 секунд, палитра → `Replay / next failure`.
Оригинальное происхождение сохраняется как `origin_mode: demo|live`, текущий
mode становится `replay`. Скорость/playhead не подменяют значения метрик.
Лимиты чтения — 200 MiB распакованного JSON и 200 000 событий; отсутствующий
терминальный текст остаётся placeholder, а не реконструируется из вымысла.
