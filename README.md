# Obsidian RAG — MCP Server

Локальный MCP-сервер для [Claude Code](https://claude.com/claude-code). Даёт Claude постоянный доступ к личной базе знаний из Obsidian vault через семантический поиск (RAG).

**Зачем:** каждый новый чат с Claude — чистый лист. Этот сервер делает единую RAG-память поверх всех ИИ-сессий: проекты, регламенты, предпочтения, прошлые решения — всё ищется автоматически.

**Полностью локально.** Эмбеддинги через Ollama, векторная БД на диске. Никакие данные не уходят в облако.

---

## Возможности

- **Семантический поиск** по любым `.md` файлам из Obsidian vault
- **Создание заметок** напрямую из чата (Claude может писать в твой vault)
- **Авто-переиндексация** при изменениях файлов (file watcher, debounce 500мс)
- **Группировка по проектам** через YAML frontmatter
- **Валидация тегов** по утверждённому словарю из `{VAULT}/CLAUDE.md`
- **Автоматические wiki-ссылки** — при создании заметки сервер сам дописывает `## 🔗 Связанные заметки` с семантически близкими заметками из vault
- **Аудит тегов** — CLI-скрипт показывает расхождения между реальными и утверждёнными тегами

---

## Требования

- **Python 3.10+**
- **Ollama** ([ollama.com](https://ollama.com))
- **Claude Code** ([claude.com/claude-code](https://claude.com/claude-code))
- **Obsidian vault** (папка с `.md` файлами)

Кроссплатформенно: **Windows 11**, **macOS**, **Linux**.

---

## Установка

### 1. Клонировать репозиторий

```bash
git clone https://github.com/<you>/mcp_obsidian_rag.git
cd mcp_obsidian_rag
```

### 2. Виртуальное окружение

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Если PowerShell блокирует активацию:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Зависимости

```bash
pip install -r requirements.txt
```

### 4. Ollama

**Windows / macOS:** скачать установщик с [ollama.com/download](https://ollama.com/download).

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Скачать модель эмбеддингов:
```bash
ollama pull nomic-embed-text
```

Проверить что сервис отвечает:
```bash
curl http://localhost:11434/api/tags
```

### 5. Конфиг

```bash
# Windows:
Copy-Item .env.example .env
# macOS / Linux:
cp .env.example .env
```

Открой `.env` и укажи путь до Obsidian vault:

| OS | Пример |
|---|---|
| Windows | `OBSIDIAN_VAULT=C:\Users\you\Documents\Obsidian\MyVault` |
| macOS | `OBSIDIAN_VAULT=/Users/you/Documents/Obsidian/MyVault` |
| Linux | `OBSIDIAN_VAULT=/home/you/Documents/Obsidian/MyVault` |

Поддерживается `~`: `OBSIDIAN_VAULT=~/Documents/Obsidian/MyVault`.

### 6. Первичная индексация

```bash
python indexer.py --full-scan
```

В конце JSON: `{indexed: N, skipped: 0, errors: 0, total: N}`. Время — около 1–3 минут на 1000 заметок.

### 7. Подключение к Claude Code

**Windows (PowerShell):**
```powershell
claude mcp add obsidian-rag --scope user -- `
  "C:\абсолютный\путь\mcp_obsidian_rag\.venv\Scripts\python.exe" `
  "C:\абсолютный\путь\mcp_obsidian_rag\server.py"
```

**macOS / Linux:**
```bash
claude mcp add obsidian-rag --scope user -- \
  /absolute/path/mcp_obsidian_rag/.venv/bin/python \
  /absolute/path/mcp_obsidian_rag/server.py
```

`--scope user` — сервер будет доступен во всех проектах Claude Code. Замени на `--scope project`, чтобы конфиг лёг только в текущий проект (`.mcp.json`).

Перезапусти Claude Code (закрой и открой окно VSCode заново). В чате набери `/mcp` — должен быть `obsidian-rag` со статусом *connected* и 5 инструментов.

---

## Инструменты

| Tool | Параметры | Назначение |
|---|---|---|
| `search_knowledge_base` | `query` | Семантический поиск по vault |
| `get_vault_conventions` | — | Утверждённые теги и правила создания заметок |
| `create_note` | `title`, `content`, `project`, `tags?`, `note_type?` | Создание заметки с автодобавлением связанных |
| `list_projects` | — | Список проектов из индекса |
| `get_project_notes` | `project` | Все заметки проекта |

### Рабочий процесс Claude при создании заметок

Claude должен вызывать инструменты в следующем порядке:

1. `get_vault_conventions()` — узнать утверждённые теги
2. `search_knowledge_base(<тема>)` — найти связанные заметки, вставить `[[wiki-links]]` в текст
3. `create_note(...)` — создать заметку с правильными тегами и ссылками

Сервер автоматически дополняет заметку секцией `## 🔗 Связанные заметки` (top-3 по косинусному сходству, порог 0.65), исключая ссылки которые Claude уже вставил inline.

### Словарь утверждённых тегов

Теги для `create_note` берутся из `{VAULT}/CLAUDE.md` — секция между маркерами `<!-- TAGS_START -->` и `<!-- TAGS_END -->`. Поддерживаются два формата:

```markdown
<!-- TAGS_START -->
**Категория:**
- `tag` — описание тега
<!-- TAGS_END -->
```

Если маркеров нет — парсер ищет заголовок `#### Утверждённые теги по категориям` как fallback.

Неизвестный тег не блокирует создание заметки: сервер создаёт файл, но возвращает warning.

### Аудит тегов

```bash
python tags_audit.py            # сводный отчёт
python tags_audit.py --show-files   # + список файлов с неутверждёнными тегами
```

Выводит три раздела: теги из заметок которых нет в утверждённом списке (с fuzzy-подсказками), утверждённые теги которые ни разу не использовались, топ-20 по частоте использования.

---

## Рекомендации по созданию заметок вручную

Семантический поиск работает на любом `.md` файле — frontmatter не обязателен. Но для группировки и фильтрации (`list_projects`, `get_project_notes`) добавляй YAML frontmatter:

```markdown
---
title: Название заметки
project: имя-проекта
tags: [тег1, тег2]
type: note
created: 2026-05-15T10:30:00
---

Здесь основное содержание заметки.

## Раздел

Текст раздела.
```

### Поля frontmatter

| Поле | По умолчанию | Назначение |
|---|---|---|
| `title` | имя файла | человеко-читаемый заголовок |
| `project` | пусто | группировка для `list_projects` / `get_project_notes` |
| `tags` | `[]` | список тегов (YAML-массив) |
| `type` | пусто | `note` / `error` / `decision` / `research` или твой |
| `created` | пусто | ISO datetime |

**Заметки без `project` остаются полностью искаемыми** через `search_knowledge_base`. Они просто не попадают в `list_projects`.

### Что попадает в индекс

- ✅ Все `.md` файлы из vault, рекурсивно
- ❌ Файлы в скрытых папках (`.obsidian/`, `.trash/`)
- ❌ Не-UTF8 файлы (лог warning, файл пропускается)
- ⚠️ Битый YAML — заметка индексируется без метаданных

### Рекомендации по структуре

- **Используй `##` заголовки.** Чанкер режет текст по `#` и `##` секциям, потом по размеру с overlap 200 символов. Чёткие H2-разделы → точнее поиск.
- **Короткие осмысленные имена файлов.** Они показываются в результатах.
- **Теги в lowercase**, без пробелов, через дефис: `ai-ml`, `side-project`, `b2c-app`.
- **Структура папок свободная.** Индексер пробегает рекурсивно, путь сохраняется в метаданных.
- **Связи `[[wiki-links]]`** Obsidian сохраняются в тексте, попадают в эмбеддинг и помогают релевантности.

### Чего избегать

- Не клади **бинарные файлы** рядом с заметками — индексер их игнорирует, но мусорит файловые события watcher'а.
- Не создавай **гигантские заметки** (>50k символов) — будут разбиты на много чанков, релевантность снижается. Лучше разбить на тематические подзаметки со ссылками.
- Не правь файлы в `.obsidian/` — это служебная папка Obsidian, не индексируется.

### Свежесозданные заметки

После сохранения нового файла в Obsidian:
1. Watcher детектит событие
2. Ждёт 500 мс (debounce — избегаем дублирующих событий)
3. Парсит, чанкует, эмбеддит, апсёртит в ChromaDB

Итого: через ~1 секунду заметка уже ищется через `search_knowledge_base`.

---

## Архитектура

```
Claude Code (VSCode / CLI)
    ↕ MCP protocol (stdio)
server.py  ─── FastMCP, 5 tools, lifecycle
    ↕                    ↕
indexer.py          conventions.py ─── {VAULT}/CLAUDE.md
    ↕                      ↕              (approved tags)
ChromaDB                Ollama
(./data/chromadb)       (nomic-embed-text, 768-dim)
    ↑
watcher.py ─── watchdog + debounce 500мс
    ↑
Obsidian Vault (*.md, рекурсивно)
```

| Слой | Файл | Зависит от |
|---|---|---|
| Config | [config.py](config.py) | — |
| Adapter | [embeddings.py](embeddings.py) | config |
| Domain | [indexer.py](indexer.py) | config, embeddings, chromadb |
| Conventions | [conventions.py](conventions.py) | config |
| Events | [watcher.py](watcher.py) | indexer, watchdog |
| MCP | [server.py](server.py) | indexer, watcher, conventions, mcp |

Полная спецификация и архитектурные решения — в [CLAUDE.md](CLAUDE.md).

---

## Troubleshooting

**`ping: False` / `Ollama unreachable`**
- Запусти `ollama serve` (Linux) или открой приложение Ollama (Windows / Mac)
- Проверь URL в `.env`: `OLLAMA_BASE_URL=http://localhost:11434`

**`Model not found`**
- `ollama pull nomic-embed-text`
- `ollama list` — модель должна быть в списке

**Claude Code не видит сервер (`/mcp` пусто)**
- Перезапусти VSCode полностью
- Проверь что в `claude mcp add` указаны **абсолютные** пути
- VSCode → View → Output → выбери `Claude Code` — там stderr сервера с реальной причиной

**Поиск ничего не находит**
- `python indexer.py --full-scan` — может коллекция пустая
- `python -c "from indexer import get_collection; print(get_collection().count())"` — кол-во чанков

**Изменения в vault не подхватываются автоматически**
- Watcher работает только пока MCP-сервер запущен (Claude Code держит его как дочерний процесс)
- Сетевые / облачные диски (OneDrive, iCloud Drive, NFS) — watchdog там часто не работает надёжно. Для таких случаев запускай `python indexer.py --full-scan` периодически вручную.

**Битый YAML в заметке ломает индексацию**
- Не ломает: индексер логирует warning, индексирует файл без метаданных. Search всё равно найдёт.

**На macOS / Linux не индексируются `.MD` файлы (заглавный регистр)**
- Glob `*.md` case-sensitive на Unix-FS. Переименуй в lowercase: `mv FILE.MD file.md`.

---

## Структура проекта

```
mcp_obsidian_rag/
├── CLAUDE.md           # спецификация и архитектурные решения
├── README.md           # этот файл
├── .env.example        # шаблон конфига
├── .env                # локальный конфиг (не в git)
├── .gitignore
├── requirements.txt
├── config.py           # загрузка .env, константы, нормализация путей
├── embeddings.py       # обёртка над Ollama
├── indexer.py          # parse → chunk → embed → upsert / search
├── watcher.py          # watchdog + debounce
├── conventions.py      # парсер тегов из {VAULT}/CLAUDE.md
├── server.py           # MCP-сервер, 5 tool'ов
├── tags_audit.py       # CLI-аудит тегов (read-only)
└── data/
    └── chromadb/       # векторная БД (не в git)
```

---

## Лицензия

Не указана. Добавь `LICENSE` (например, MIT) если планируешь публиковать.
