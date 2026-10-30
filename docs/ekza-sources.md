# Исходники Ekza Space / OMOBA

Режим `ekza` читает только перечисленные ниже файлы относительно `--ekza-root`.
Он не сканирует репозитории рекурсивно. При выборе фрагмента или нажатии `R`
файл перечитывается; `LOCAL` в интерфейсе означает код из текущего checkout.
Отступы с табуляцией разворачиваются, управляющие символы экранируются.

Встроенные `SNAPSHOT` — точные выдержки из этих файлов от **2026-10-30**.
Они сохраняют исходные номера строк и работают без папки `ekza`.
Пути и якоря заданы в `devos/ekza.py`; ограниченное чтение и fallback —
в `devos/sources.py`. SHA-256 в таблице относится к тексту фрагмента с LF,
без завершающего перевода строки. На экране также показывается реальный
отпечаток отображаемого фрагмента после нормализации для терминала.

| Файл | Строки снимка | SHA-256 (8) | Содержание |
| --- | --- | --- | --- |
| `ekza-bevy-sdk/src/assets.rs` | 113–160 | `bc6ce5c7` | Canonical asset identity + exact rendition |
| `ekza-bevy-sdk/src/store.rs` | 266–319 | `e2d04979` | Verify bytes, validate profile, install atomically |
| `omoba-bevy/server/src/passport_admission.rs` | 270–319 | `efc9e846` | OMOBA server owns admission; clients cannot grant it |
| `core/src/features/ekza/useVerifiedAvatar.ts` | 6–38 | `d33d0c6e` | Space peers verify server-approved avatars |
| `ekza-stellar-sdk/src/catalog/index.ts` | 154–186 | `46cac313` | Web SDK: pinned references + verified VRM download |
| `omoba-bevy/client/src/ekza_account.rs` | 67–111 | `e9130507` | Project-scoped account session + stale response guard |
| `core/src/realtime/protocol.ts` | 3–58 | `d847198d` | Space realtime: presence, rooms, movement and avatars |
| `solana-ekza-space/programs/ekza-space/src/instructions/mint_space.rs` | 128–175 | `6c58503c` | Optional Solana layer: numbered Space NFT mint |

Контекст взят из `ekza/ARCHITECTURE.md`,
`ekza/doc/docs/core-concepts/ecosystem-map.md` и
`ekza/doc/docs/developers/game-integration.md`,
`ekza/doc/docs/developers/typed-assets.md`.
Точный project/profile Mirror сверялся с
`ekza-mirror/EkzaMirror/AvatarBrowser/StudioClient.swift`.

`devos/protocol.py` описывает общий контракт и отдельный каталог потребителей.
Проектные идентификаторы: `ekza-space`, `ekza-mirror`, `omoba`.
Анимация переключается независимо от исходника; в `EKZA ASSET FLOW`
пакет проходит шесть стадий жизненного цикла. Числа/хэши в этом рассказе —
демонстрационные, с пометкой `DEMO`.

Главный маршрут: Studio → Registry → утверждённая версия → SDK с проверкой
размера/хэша → Space или OMOBA. Сервер OMOBA независимо решает допуск.
Связь аккаунта даёт библиотеку конкретной игры, а не право надеть любой ассет.
Solana остаётся дополнительным слоем владения; бесплатные Studio-ассеты
не требуют кошелька.

Показанные логи конвейера — симуляция архитектуры, а не результат запросов
к Registry, запуска игры или проверки текущего состояния серверов.
