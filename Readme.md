# Orion AI — STEM Knowledge Architect

Полностью контейнеризованный стек: PostgreSQL + FastAPI + (опционально) Caddy с автоматическим HTTPS.

---

# Late
Desmos API
Anki card


## 🚀 Быстрый старт (локально)

```bash
# 1. Скопируйте конфиг и заполните три обязательных переменных
cp .env
# Откройте .env и заполните: GEMINI_API_KEY, SECRET_KEY, POSTGRES_PASSWORD
# Сгенерировать SECRET_KEY: openssl rand -hex 32

# 2. Соберите и запустите
docker compose up -d --build

# 3. Откройте в браузере
open http://localhost:8000
```

Логи приложения: `docker compose logs -f app`
Логи БД: `docker compose logs -f db`

---

## 🌐 Деплой на сервер (продакшен с HTTPS)

### Что вам нужно
- VPS / dedicated сервер с публичным IP
- Домен, A-запись которого указывает на IP сервера
- Открытые порты `80` и `443`
- Установленный Docker + Docker Compose

### Шаги

```bash
# На сервере
git clone <your-repo> orion && cd orion

cp .env.example .env
nano .env
# Заполните: GEMINI_API_KEY, SECRET_KEY, POSTGRES_PASSWORD
# А также: DOMAIN, ACME_EMAIL

# Запуск с Caddy и автоматическим Let's Encrypt
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Caddy сам выпустит и продлит SSL-сертификат. Через 30 секунд сайт открыт по `https://your-domain.com`.

---

## 🔧 Полезные команды

```bash
# Перезапустить только приложение, не трогая БД
docker compose restart app

# Обновить код без потери данных
git pull
docker compose up -d --build app

# Подключиться к Postgres
docker compose exec db psql -U orion -d orion

# Бэкап БД
docker compose exec db pg_dump -U orion orion | gzip > backup-$(date +%F).sql.gz

# Восстановление
gunzip -c backup-2026-01-15.sql.gz | docker compose exec -T db psql -U orion orion

# Размер хранилища файлов (с дедупликацией)
docker compose exec app du -sh uploads/

# Полная остановка с сохранением данных
docker compose down

# ⚠️ Полное удаление вместе с данными (необратимо!)
docker compose down -v
```

---

## 📦 Архитектура

```
┌─────────────────────────────────────────────┐
│  Caddy (prod overlay)                       │
│  • TLS termination via Let's Encrypt        │
│  • gzip/zstd compression                    │
│  • Security headers                         │
│  Listens: 80, 443                           │
└──────────────┬──────────────────────────────┘
               │ proxy → app:8000
┌──────────────▼──────────────────────────────┐
│  app (FastAPI + uvicorn, 2 workers)         │
│  • REST API + JWT auth                      │
│  • Gemini integration                       │
│  • Background mindmap generation            │
│  Volume: uploads_data → /app/uploads        │
│         (content-addressed file storage)    │
└──────────────┬──────────────────────────────┘
               │ asyncpg
┌──────────────▼──────────────────────────────┐
│  db (PostgreSQL 16)                         │
│  • Users, chats, messages, files, mindmaps  │
│  Volume: postgres_data                      │
│  NOT exposed on host network                │
└─────────────────────────────────────────────┘
```

### Volumes (постоянные данные)

| Volume                  | Что хранит                                       |
|-------------------------|--------------------------------------------------|
| `orion_postgres_data`   | База данных PostgreSQL                           |
| `orion_uploads_data`    | Загруженные файлы (sha256-дедуп + gzip)          |
| `orion_caddy_data`      | SSL-сертификаты Let's Encrypt (только prod)      |
| `orion_caddy_config`    | Внутренний конфиг Caddy (только prod)            |

---

## 🛡️ Безопасность

В продакшене обязательно:
1. **Сильный `POSTGRES_PASSWORD`** — минимум 24 символа
2. **`SECRET_KEY` через `openssl rand -hex 32`** — без него JWT уязвим
3. **Файрвол**: открыты только `80` и `443` (порт 22 для SSH)
4. **БД не торчит наружу** — `db` доступен только из Docker-сети
5. **Регулярные бэкапы** — настройте cron для `pg_dump`

---

## 🩺 Troubleshooting

**Приложение не стартует, в логах `connection refused`**
→ Подождите 10–20 с — Postgres проходит healthcheck.
   `docker compose ps` должен показать `db ... healthy`.

**Caddy показывает `unable to obtain certificate`**
→ Проверьте, что A-запись `DOMAIN` указывает на ваш сервер
   и что порт 80 открыт (HTTP-01 challenge).

**Apple Silicon / ARM хост, ошибки сборки**
→ Все используемые пакеты имеют ARM64-wheels. Если зависнет на Pillow:
   `docker compose build --no-cache app`.

**Хочется уменьшить потребление памяти**
→ В `.env`: `UVICORN_WORKERS=1`.
   Добавьте лимиты в `docker-compose.yml` под service `app`:
   ```yaml
   deploy:
     resources:
       limits:
         memory: 512M
   ```

---

## 📁 Структура

```
./
├── backend/                 # Python код
│   ├── main.py
│   ├── db.py
│   ├── auth.py
│   ├── storage.py
│   ├── promts.py
│   └── templates/
│       ├── index.html
│       └── landing.html
└─── conf/                   # Docker конфиг
    ├── docker-compose.yml
    ├── Dockerfile
    ├── Caddyfile
    ├── .env
    ├── .dockerignore
    └── requirements.txt
```