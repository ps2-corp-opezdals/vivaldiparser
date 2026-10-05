# Source-vivaldi — обезличенная выгрузка профиля Vivaldi

Данные для модулей парсера из ТЗ: `VivaldiHistory` (ПВ-2), `VivaldiDownloads` (ПВ-3),
`VivaldiBookmarks` (ПВ-4), `VivaldiCookies` (ПВ-5), `VivaldiCache` (ПВ-6).

## Что здесь

```
vivaldi_dump/vivaldi/
├── Local State              # профиль браузера, версия
├── Preferences / Secure Preferences
└── Default/
    ├── History              # sqlite: urls, visits, downloads  (ПВ-2, ПВ-3)
    ├── Cookies              # sqlite: cookies                  (ПВ-5)
    ├── Bookmarks            # json                            (ПВ-4)
    ├── Web Data             # sqlite: keywords, autofill
    ├── Favicons, Top Sites, Shortcuts
    └── Cache/
        └── Cache_Data/      # Simple Cache, ~4800 файлов      (ПВ-6)
```

Путь до каталога данных: `Source-vivaldi/vivaldi_dump/vivaldi/Default/`
(в ТЗ он назван `SourceVivaldi/Default/`).

## Что обезличено

Профиль настоящий, но личные данные удалены, чтобы выгрузку можно было держать
в публичном репозитории.

**Удалено целиком:**

- `Login Data`, `Login Data For Account` — email и хэши паролей
- 42 авторизационные куки из `Cookies` (`SID`, `HSID`, `SSID`, `APISID`, `SAPISID`,
  `SIDCC`, `NID`, `__Secure-1PSID`, `__Secure-3PSID`, `__Host-next-auth.csrf-token`,
  `aws-waf-token`, `ACCOUNT_CHOOSER`, `PHPSESSID`, `JSESSIONID`)
- каталоги с токенами и вне зоны ТЗ: `Local Storage`, `Session Storage`,
  `IndexedDB`, `blob_storage`, `WebStorage`, `Service Worker`, `Sync Data`,
  `Local App Settings`, `File System`, `Extension*`, `AdBlockRules`,
  `Code Cache`, `GPUCache`, `Dawn*Cache`, `Vivaldi*Icons`

**Заменено на заглушки:**

| Где | Было | Стало |
|---|---|---|
| `Cookies` | реальные хосты и значения | `siteN.invalid`, значения пустые |
| `History.urls` | реальные URL и заголовки | `https://site.invalid/...` |
| `History.downloads` | `<user>/Downloads/<файл>` | `/home/user/Downloads/file.bin` |
| `Bookmarks` | 23 URL-закладки | `https://site.invalid/` |
| `Preferences`, `Local State` | `gaia_cookie` с email и Google ID | удалено |
| `Preferences`, `Local State` | имя профиля `Работа` | `Profile` |
| `Web Data.autofill` | адрес электронной почты | `user` / `value` |
| `Favicons`, `Top Sites`, `Shortcuts` | реальные URL | `https://site.invalid/...` |
| leveldb `LOG`, `WidevineCdm` | абсолютный путь к профилю | `/home/user/...` |

## Что сохранено

Структура и объём данных реальные, чтобы парсеры тестировались на настоящем
формате, а не на синтетике:

- `History`: 133 URL, 229 посещений, 7 загрузок — с реальными
  `chrome_time`-таймстампами, `visit_count`, цепочками переходов
- `Cookies`: 156 строк с реальными `expires_utc`, `is_secure`, `is_httponly`,
  `samesite`, `source_scheme` — значения обнулены, но схема и тайминги настоящие
- `Cache/Cache_Data`: ~4800 файлов Simple Cache не тронут, поэтому
  `VivaldiCache` можно доводить по критерию «≥50% записей»

## Проверка

```bash
# личных данных нет (подставь свой email/логин, если проверяешь свою копию)
grep -rlai "<email>\|<login>" Source-vivaldi/

# структура на месте
python3 -c "import sqlite3; c=sqlite3.connect('Source-vivaldi/vivaldi_dump/vivaldi/Default/History'); print(c.execute('select count(*) from urls').fetchone())"
```

## Оговорка

`vivaldi_dump/vivaldi/.gitignore` содержит `*` — Vivaldi добавляет его, чтобы профиль
не уехал в репозиторий случайно. Файлы добавлены принудительно (`git add -f`),
перечисление исключений — в `.gitignore` репозитория.

Кеш (`Cache/Cache_Data`) проверен на токены: 24 совпадения `access_token` —
публичные Mapbox-ключи из JS-бандла ChatGPT, `Bearer` — шаблонные строки в JS.
Персональных секретов там нет.
