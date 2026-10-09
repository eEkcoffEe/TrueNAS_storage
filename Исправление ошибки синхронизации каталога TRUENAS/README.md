
При этом проверки показывают, что сеть и Git в порядке:
- `ping github.com` — работает
- `curl -sI https://github.com/truenas/apps` — возвращает `HTTP/2 200`
- `git clone https://github.com/truenas/apps /tmp/test-apps` из Shell — успешно

## Причины

1. **`middlewared` использует собственный Git-стек** с дефолтными
   настройками, которые конфликтуют с HTTP/2 GitHub (обрывы
   `git clone` с `RPC failed` / `remote end hung up`).
2. **`/var/run` — это tmpfs**, ограниченный по размеру (обычно ~380 МБ).
   Даже успешный `git clone` падает с `No space left on device`.
3. **Корневая ФС (`/usr`, `/usr/local/bin`) смонтирована read-only.**
   Скрипты и обёртки туда писать нельзя — система защищена от изменений.
4. **IPv6 без маршрута** может вызывать таймауты подключения
   у middleware (см. трейсбек `TimeoutError` в `aiohttp`).

## Решение

Каталог клонируется в постоянное место на `boot-pool`, а в `/var/run`
создаётся символическая ссылка. Git настраивается на HTTP/1.1.
IPv6 отключается через Sysctl/Tunable. Синхронизация запускается
через `midclt call catalog.sync` (в обход GUI), а ежедневный Cron
поддерживает каталог в актуальном состоянии и восстанавливает
симлинк после перезагрузки.

### Шаги вручную (для справки)

```bash
# 1. Отключаем HTTP/2 для Git
git config --system http.version HTTP/1.1

# 2. Готовим постоянное место
mkdir -p /mnt/boot-pool/ix-apps-catalog

# 3. Клонируем каталог
git clone --depth=1 https://github.com/truenas/apps \
    /mnt/boot-pool/ix-apps-catalog/catalogs

# 4. Создаём симлинк
mkdir -p /var/run/middleware/ix-apps
rm -rf /var/run/middleware/ix-apps/catalogs
ln -s /mnt/boot-pool/ix-apps-catalog/catalogs \
      /var/run/middleware/ix-apps/catalogs

# 5. Отключаем IPv6 через Tunable (правильный API-метод)
midclt call tunable.create '{
  "type": "SYSCTL",
  "var": "net.ipv6.conf.all.disable_ipv6",
  "value": "1",
  "comment": "Disable IPv6 for TrueNAS update/catalog",
  "enabled": true
}'

# 6. Запускаем синхронизацию
midclt call catalog.sync
# Ответ — ID задания, например: 313

# 7. Проверяем статус
midclt call core.get_jobs '[["id", "=", 313]]'
# Ищите: "state": "SUCCESS", "description": "Synced 'TRUENAS' catalog"