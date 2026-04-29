# ConspectAI.tech — STEM Knowledge Architect
Полностью контейнеризованный стек: PostgreSQL + FastAPI + (опционально) Caddy с автоматическим HTTPS.

---
# Main Link
1. [Google sheets](https://docs.google.com/spreadsheets/d/16s1q8gCboe1yT4y1dk7mcKwiI1nQ0zVRVCWBCWAC8Rw/edit?gid=59453621#gid=59453621)

---
# TO-DO
1. Настроить систему подписки

# Late
- Desmos API
- Anki card


## 🚀 Быстрый старт (локально)
1. [Скачать Docker](https://desktop.docker.com/mac/main/arm64/Docker.dmg?utm_source=docker&utm_medium=webreferral&utm_campaign=dd-smartbutton&utm_location=module)

```bash
# 2. Скопируйте конфиг и заполните три обязательных переменных
cp .env
# Откройте .env и заполните: GEMINI_API_KEY, SECRET_KEY, POSTGRES_PASSWORD
# Сгенерировать SECRET_KEY: openssl rand -hex 32

# 3. Соберите и запустите
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build  

# 4. Откройте в браузере
open http://localhost:8000
```

Логи приложения: `docker compose logs -f app`
Логи БД: `docker compose logs -f db`


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
.
├── Docker-compose.dev.yml
├── Docker-compose.yml
├── Readme.md
├── backend
│   ├── admin.py
│   ├── analytics.py
│   ├── auth.py
│   ├── billing.py
│   ├── db.py
│   ├── main.py
│   ├── promts.py
│   ├── rag.py
│   ├── rag_routes.py
│   ├── storage.py
│   └── templates
│       ├── 404.html
│       ├── 503.html
│       ├── admin.html
│       ├── contacts.html
│       ├── index.html
│       ├── landing.html
│       ├── offer.html
│       ├── pricing.html
│       └── privacy.html
├── conf
│   ├── Caddyfile
│   ├── Caddyfile.dev
│   ├── Dockerfile
│   └── requirements.txt
└── static
    ├── docs
    │   └── offer.pdf
    ├── error-bg
    │   ├── 1.jpg
    │   ├── 10.jpg
    │   ├── 2.jpg
    │   ├── 3.jpg
    │   ├── 4.jpg
    │   ├── 5.jpg
    │   ├── 6.jpg
    │   ├── 7.jpg
    │   ├── 8.jpg
    │   ├── 9.jpg
    │   ├── README.md
    │   └── manifest.json
    ├── favicon.svg
    ├── icon.svg
    └── og-image.svg
```