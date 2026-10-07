# Job Matcher

Локальное приложение для macOS, которое помогает в личном поиске работы: собирает вакансии из общедоступной выдачи hh.ru и из писем LinkedIn Job Alerts, которые приходят на почту пользователя, оценивает их по профилю кандидата и присылает подходящие в Telegram.

## Правовая информация

- Проект создан для личного некоммерческого поиска работы: автор запускает его на своём компьютере. Это не сервис и не база данных вакансий: собранные данные хранятся локально и никуда не передаются.
- В репозитории нет данных, полученных с hh.ru или LinkedIn.
- Приложение открывает те же страницы, что и обычный браузер, с низкой частотой: по умолчанию раз в 30 минут, за запуск — 3 страницы поиска и страницы только новых вакансий (уже известные не открываются повторно). Оно не обходит капчу и другие защитные механизмы и не использует непубличные API.
- Письма LinkedIn читаются из почтового ящика самого пользователя только на чтение (по умолчанию; при `LINKEDIN_MARK_AS_READ=true` обработанные письма помечаются прочитанными).
- Проект не связан с HeadHunter, LinkedIn или Telegram и не одобрен ими. hh.ru, LinkedIn и Telegram — товарные знаки их правообладателей.
- Перед использованием ознакомьтесь с правилами hh.ru, LinkedIn, вашего почтового сервиса и Telegram. Пользователь сам отвечает за соблюдение этих правил и применимого законодательства.
- Программа предоставляется «как есть», без каких-либо гарантий (см. [LICENSE](LICENSE)).

## Источники

| Источник | Как получает данные | Статус |
|---|---|---|
| hh.ru (браузер) | Общедоступная выдача hh.ru в отдельном профиле Chromium через Playwright, без входа в аккаунт; капча только вручную | Работает локально |
| LinkedIn Job Alerts | Письма `jobalerts-noreply@linkedin.com` из почты по IMAP, только чтение (по умолчанию) | Работает на Mail.ru; Gmail описан, но на реальном ящике не проверялся |

## Как это работает

`monitor-once --headless --notify` по очереди опрашивает источники из `MONITOR_SOURCES` в [job_matcher/cli.py](job_matcher/cli.py). Каждая новая вакансия проходит общий пайплайн:

1. **SQLite** (`data/job_matcher.sqlite`): вакансия сохраняется один раз. Дубли отсекаются по паре `source + source_id` и по нормализованной ссылке.
2. **Оценка** по профилю: роль, задачи, английский, география, отрасль, уровень. Подробности в [job_matcher/scoring.py](job_matcher/scoring.py).
3. **Telegram**: hh.ru-вакансии отправляются, если нет причин для отказа и оценка не ниже порога. LinkedIn-вакансии отправляются все, потому что подписки уже отфильтрованы на стороне LinkedIn; оценка показывается для информации.

Если один источник упал из-за капчи, таймаута, почты или другой ошибки, `monitor-once` пишет ошибку в лог и переходит к следующему источнику. Если Telegram временно недоступен, отправка помечается как неуспешная и повторяется на следующих запусках до 5 попыток. Вакансии, сохранённые с `--no-notify`, повторно не отправляются.

## Установка

Нужны macOS (автозапуск через launchd), Python 3.9+ и доступ в интернет: `pip` скачивает пакеты с PyPI, а `playwright install chromium` — браузер Chromium (около 150 МБ).

```bash
git clone https://github.com/agibalovaM/job_matcher.git
cd job_matcher
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

После этого заполните `.env`, создайте базу и проверьте тесты:

```bash
python -m job_matcher.cli init-db
python -m unittest
```

Зависимости: `playwright` для браузерного поиска hh.ru, `beautifulsoup4` для разбора писем LinkedIn, `pypdf` и `python-docx` для импорта резюме.

## Переменные окружения

| Имя | По умолчанию | Обязательна ли |
|---|---|---|
| `APP_DB_PATH` | `data/job_matcher.sqlite` | Нет |
| `APP_POLL_INTERVAL_SECONDS` | `1800` (30 минут) | Нет; меньше 300 не ставится |
| `APP_THRESHOLD` | `4` | Нет |
| `TELEGRAM_BOT_TOKEN` | пусто | Да, если нужны Telegram-уведомления |
| `TELEGRAM_CHAT_ID` | пусто | Да, если нужны Telegram-уведомления |
| `LINKEDIN_IMAP_HOST` | `imap.gmail.com` | Да, если используется LinkedIn IMAP |
| `LINKEDIN_IMAP_USER` | пусто | Да, если используется LinkedIn IMAP |
| `LINKEDIN_IMAP_PASSWORD` | пусто | Да, если используется LinkedIn IMAP |
| `LINKEDIN_IMAP_MAILBOX` | `INBOX` | Нет |
| `LINKEDIN_SENDER` | `jobalerts-noreply@linkedin.com` | Нет |
| `LINKEDIN_SINCE_DAYS` | `14` | Нет |
| `LINKEDIN_MARK_AS_READ` | `false` | Нет |
| `LINKEDIN_MBOX_PATH` | пусто | Нет; альтернативный локальный `.mbox` |

`.env` находится в `.gitignore`. Не коммитьте реальные токены, пароли, тексты писем, SQLite-базу, браузерный профиль и launchd-файлы с локальными путями.

## hh.ru через браузер

Поиск идёт по общедоступной выдаче hh.ru в отдельном профиле браузера `.browser-profiles/hh`. Входить в аккаунт hh.ru не нужно: приложение не спрашивает логин и пароль и не обходит капчу.

```bash
python -m job_matcher.cli hh-browser-once --query "Delivery Manager" --limit 5   # первый запуск, без уведомлений
python -m job_matcher.cli hh-browser-once --headless --notify
```

Первый запуск лучше делать без `--notify`: старые результаты сохранятся как уже виденные, и в Telegram не придёт пачка старых вакансий.

Если hh.ru показывает капчу, мониторинг hh пропускает запуск и один раз присылает в Telegram просьбу её пройти (до следующего успешного запуска). Тогда выполните `python -m job_matcher.cli hh-browser-login`: откроется окно браузера с тем же профилем, чтобы вручную пройти капчу.

## LinkedIn Job Alerts

Приложение читает письма LinkedIn из почты по IMAP: письма не удаляются, не перемещаются и по умолчанию не помечаются прочитанными (`BODY.PEEK`, папка открывается только на чтение). Из каждого письма берутся название, компания, локация, формат работы, ссылка `https://www.linkedin.com/jobs/view/<job_id>` и название подписки. Одна вакансия из нескольких подписок отправляется один раз по `job_id`.

Нужен отдельный пароль приложения, а не основной пароль от почты.

Mail.ru:

```dotenv
LINKEDIN_IMAP_HOST=imap.mail.ru
LINKEDIN_IMAP_USER=you@mail.ru
LINKEDIN_IMAP_PASSWORD=app-password
LINKEDIN_IMAP_MAILBOX=INBOX
```

Gmail:

```dotenv
LINKEDIN_IMAP_HOST=imap.gmail.com
LINKEDIN_IMAP_USER=you@gmail.com
LINKEDIN_IMAP_PASSWORD=app-password
```

Первый запуск без уведомлений:

```bash
python -m job_matcher.cli linkedin-once --no-notify
```

Текст LinkedIn-уведомления можно обработать вручную тем же парсером:

```bash
python -m job_matcher.cli ingest-alert linkedin /path/to/linkedin-alert.txt
```

## Профиль кандидата

В [job_matcher/models.py](job_matcher/models.py) лежит нейтральный пример профиля. Реальный профиль храните локально: в SQLite-базе, импортированном JSON или `.env`-зависимой настройке, которая не попадает в Git.

```bash
python -m job_matcher.cli import-resume /path/to/resume.pdf
python -m job_matcher.cli profile > profile.json
python -m job_matcher.cli import-profile-json profile.json
```

`import-resume` сохраняет только текст резюме; остальные поля задайте вручную в JSON. На оценку влияют:

| Поле | Как используется |
|---|---|
| `english_level` | A1–C2. Требование в вакансии на уровень выше — пробел, на два и больше — отказ |
| `experience_years` | Требование опыта на 1–2 года больше — «пограничная», больше — «пограничная» без балла |
| `location`, `work_permits`, `places` | Где подходит офис/гибрид. В `places` перечислите все написания, которые встречаются в вакансиях; русские — основой слова (`"лиссабон"` найдёт «Лиссабона»). Фраза «только из …» с этим местом не считается ограничением |
| `remote_regions` | Откуда ещё подходит удалёнка, например `["Europe", "EMEA", "CET"]`. «Worldwide», «из любой точки мира» подходят всегда |
| `industries_confirmed` | Отрасли, опыт в которых подтверждён (fintech, igaming и т.п.) |

`name`, `target_roles` и `skills` на оценку пока не влияют: целевые роли и задачи заданы списками `ROLE_TERMS` и `TASK_TERMS` в [job_matcher/scoring.py](job_matcher/scoring.py).

## CLI-команды

Основные команды:

| Команда | Назначение |
|---|---|
| `python -m job_matcher.cli init-db` | Создать/обновить SQLite-схему |
| `python -m job_matcher.cli monitor-once --headless` | Один проход по всем настроенным источникам без Telegram |
| `python -m job_matcher.cli monitor-once --headless --notify` | Один проход с Telegram-уведомлениями |
| `python -m job_matcher.cli hh-browser-login [--query TEXT]` | Открыть окно браузера, чтобы вручную пройти капчу, если она появилась |
| `python -m job_matcher.cli hh-browser-once [--query TEXT] [--limit N] [--pages N] [--headless] [--notify]` | Один браузерный проход по hh.ru |
| `python -m job_matcher.cli linkedin-once [--no-notify] [--notify]` | Один проход по LinkedIn Job Alerts |
| `python -m job_matcher.cli ingest-alert linkedin PATH [--no-notify]` | Разобрать сохранённый текст LinkedIn alert |
| `python -m job_matcher.cli status` | Показать путь к базе, паузу, порог и статус источников |
| `python -m job_matcher.cli telegram-test` | Отправить тестовое Telegram-сообщение |
| `python -m job_matcher.cli telegram-chat-id` | Показать доступные Telegram chat id |
| `python -m job_matcher.cli install-launchd [--pages N] [--limit N]` | Установить локальный launchd job для `monitor-once` (см. ниже) |

Служебные команды:

| Команда | Назначение |
|---|---|
| `python -m job_matcher.cli import-resume PATH` | Импортировать профиль из PDF/DOCX/TXT/MD |
| `python -m job_matcher.cli profile` | Вывести текущий профиль JSON |
| `python -m job_matcher.cli import-profile-json PATH` | Загрузить отредактированный профиль JSON |
| `python -m job_matcher.cli test-vacancy [--no-notify]` | Прогнать синтетическую вакансию через общий пайплайн |
| `python -m job_matcher.cli hh-browser-notify-test [--query TEXT] [--limit N] [--headless]` | Отправить тестовое уведомление по реальной hh.ru-вакансии, если найдётся подходящая |
| `python -m job_matcher.cli linkedin-notify-test` | Отправить тестовое уведомление по LinkedIn-вакансии, если найдётся подходящая |

`install-launchd` пишет файл `~/Library/LaunchAgents/local.marina-job-search.hh-browser.plist` с абсолютными путями к проекту и загружает его через `launchctl`, после чего `monitor-once --headless --notify` запускается раз в `APP_POLL_INTERVAL_SECONDS` секунд (по умолчанию 30 минут, не чаще раза в 5 минут). За запуск открываются 3 страницы поиска hh.ru (по одной на запрос) и страницы только новых вакансий, до 10 на запрос. Чтобы поменять интервал, сначала выгрузите старую задачу (`launchctl unload …`), потом снова выполните `install-launchd`. Отключить: `launchctl unload ~/Library/LaunchAgents/local.marina-job-search.hh-browser.plist`.

Логи launchd по умолчанию пишутся в `data/logs/hh-browser.out.log` и `data/logs/hh-browser.err.log`.

## Что не работает или не проверено

- `LINKEDIN_MBOX_PATH` на реальном `.mbox`-экспорте не проверялся.
- Gmail не проверялся на реальном ящике.
- `linkedin-notify-test` и `hh-browser-notify-test` на реальных данных могут отправлять уведомления, поэтому запускайте их только осознанно.
- Поля профиля `target_roles` и `skills` не используются в оценке (см. «Профиль кандидата»).

## В планах

- **Команды Telegram-бота**: `/pause` (пауза), `/resume` (возобновить), `/threshold N` (порог), `/latest` (последние вакансии), `/why` (почему отобрана) и разбор пересланной боту ссылки LinkedIn. Код лежит в [job_matcher/telegram_bot.py](job_matcher/telegram_bot.py), но ни к одной команде не подключён: его нужно вызывать периодически, например из `monitor-once`. Пока паузу можно включить вручную: `sqlite3 data/job_matcher.sqlite "UPDATE state SET value='true' WHERE key='paused'"`.

## Как добавить источник

1. Создайте модуль `job_matcher/sources_<name>.py`, который возвращает `list[Vacancy]`.
2. В [job_matcher/cli.py](job_matcher/cli.py) добавьте функцию `monitor_<name>(app, settings, args)`.
3. Добавьте строку `("<name>", monitor_<name>)` в `MONITOR_SOURCES`.
4. Настройки и секреты добавьте в `Settings`, `.env.example` и этот README.
5. Если источник должен отправляться без порога, добавьте правило в `should_notify`.
6. Первый запуск делайте без `--notify`, чтобы старые вакансии не пришли пачкой.

## Тесты

```bash
source .venv/bin/activate
python -m unittest
```

Все тесты в репозитории работают на синтетических данных. Тесты на своих реальных письмах и вакансиях кладите в `tests/test_local_*.py`, а письма — в `tests/fixtures/`: и то и другое в `.gitignore`, `python -m unittest` подхватит их локально.
