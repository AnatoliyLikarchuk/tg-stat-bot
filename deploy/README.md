# Деплой на VPS

## 1. На VPS (один раз)

### Клонировать репозиторий
```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/AnatoliyLikarchuk/tg-stat-bot.git
cd tg-stat-bot
```

### Создать виртуальное окружение
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Создать .env файл
```bash
nano .env
```
Вставить:
```
TELEGRAM_BOT_TOKEN=твой_токен
GOOGLE_SHEETS_CREDENTIALS_FILE=credentials.json
GOOGLE_SHEETS_SPREADSHEET_NAME=Логистика Статистика
TIMEZONE=Europe/Kiev
```

### Скопировать credentials.json
Загрузить файл `credentials.json` на сервер в папку проекта.

### Настроить systemd сервис
```bash
# Отредактировать файл (заменить YOUR_USERNAME на своё имя пользователя)
nano deploy/tg-stat-bot.service

# Скопировать в systemd
sudo cp deploy/tg-stat-bot.service /etc/systemd/system/

# Включить и запустить
sudo systemctl daemon-reload
sudo systemctl enable tg-stat-bot
sudo systemctl start tg-stat-bot

# Проверить статус
sudo systemctl status tg-stat-bot
```

---

## 2. На GitHub (секреты)

Перейти в репозиторий → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Добавить 3 секрета:

| Имя | Значение |
|-----|----------|
| `VPS_HOST` | IP адрес или домен VPS |
| `VPS_USER` | Имя пользователя SSH |
| `VPS_SSH_KEY` | Приватный SSH ключ (содержимое файла `~/.ssh/id_rsa`) |

### Как получить SSH ключ

На локальном компьютере:
```bash
cat ~/.ssh/id_rsa
```

Скопировать **всё содержимое** (включая `-----BEGIN` и `-----END-----`).

---

## 3. Готово!

Теперь при каждом `git push` в `main`:
1. GitHub Actions подключится к VPS по SSH
2. Выполнит `git pull`
3. Обновит зависимости
4. Перезапустит бота

### Проверить логи бота на VPS
```bash
sudo journalctl -u tg-stat-bot -f
```
