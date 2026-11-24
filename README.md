# Евро — Ёлки: YML для Яндекс Товаров

Отдельный публичный репозиторий только для товарного XML-фида, без ABC Connect.

Раз в три часа GitHub Actions:
1. Скачивает стандартный YML Tilda и sitemap-store.xml.
2. Selects the exact tree height and all distinct accessory/bundle editions.
3. Проверяет адреса, цены, изображения, типы и число товаров.
4. При успехе публикует XML. При ошибке оставляет последний опубликованный файл.

Публичный адрес после активации Pages: https://savelygpt-lang.github.io/euro-elki-yandex-feed/catalog.xml

Важно: ссылка ещё не зарегистрирована в Яндекс Товарах. Для подключения может потребоваться размещение фида на подтверждённом домене euro-elki.ru. Стандартный YML Tilda содержит 294 предложения с несовпадением названий размеров и цен.

Локальная проверка:

    python3 -m unittest tests.test_yandex_yml -v
    python3 scripts/build_yandex_yml.py --output site/catalog.xml

Fail-closed: validates 44+ canonical pages, URL paths, exact variant IDs, prices and unique offers. Supports root /tproduct/ and section /catalog/.../tproduct/ paths. The current catalog yields 54 offers (33 trees, 12 accessories, 9 bundles). Transient upstream errors are retried up to four times; the last successful deployment is retained on failure.

Численные условные остатки не передаются. Все товары доступны к заказу по подтверждению владельца. Scheduled workflow может быть отключён GitHub после 60 дней без активности; ежемесячное служебное изменение уменьшает этот риск, но не гарантирует полного отсутствия отключений.
