# CLAUDE.md — Инструкции для Claude Code

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

# Obsidian RAG — MCP Server

## ВАЖНО: перед началом работы

Прочитай этот файл полностью. Затем напиши подробный план действий и **жди подтверждения** перед тем как писать любой код.

---

## Что это за проект

MCP-сервер (Model Context Protocol) для Claude Code, который даёт Claude постоянный доступ к личной базе знаний из Obsidian vault через RAG (Retrieval-Augmented Generation).

**Проблема которую решаем:** каждый новый чат с Claude — чистый лист. Нужно заново объяснять контекст проектов, регламенты, предпочтения. Это решается единой RAG-памятью поверх всех ИИ-сессий.

**Как работает:**
1. Все `.md` файлы из Obsidian vault индексируются в ChromaDB (векторная БД, локально)
2. При запросе Claude сам решает вызвать `search_knowledge_base()` — получает релевантные чанки
3. File Watcher (watchdog) следит за изменениями vault и авто-переиндексирует файлы
4. Claude может писать новые заметки в vault через `create_note()` — они тоже индексируются автоматически

---

## Архитектура

```
Claude Code (VSCode)
    ↕ MCP protocol (stdio)
MCP Server (server.py)
    ↕ vector query / upsert
ChromaDB (локально, ./data/chromadb)
    ↑ индексация при старте + инкрементно
Indexer (indexer.py)
    ↑ fs events
File Watcher (watcher.py)
    ↑ наблюдает
Obsidian Vault (путь из .env)
```

---

## Структура проекта

```
obsidian-rag-mcp/
├── CLAUDE.md               # этот файл
├── .env.example            # шаблон конфига
├── .env                    # локальный конфиг (не в git)
├── .gitignore
├── requirements.txt
├── server.py               # MCP сервер — точка входа
├── indexer.py              # первичная индексация + инкрементная
├── watcher.py              # watchdog, следит за vault
├── embeddings.py           # обёртка над Ollama (nomic-embed-text)
├── config.py               # загрузка .env, все константы
└── data/
    └── chromadb/           # векторная БД (не в git)
```

---

## Конфигурация (.env)

```bash
# Путь до Obsidian vault — единственное что нужно настроить при первом запуске
# Windows: C:\Users\you\Documents\Obsidian\MyVault
# Mac:     ~/Documents/Obsidian/MyVault
OBSIDIAN_VAULT=

# Путь до ChromaDB (создаётся автоматически)
CHROMA_DB_PATH=./data/chromadb

# Ollama модель для эмбеддингов
EMBED_MODEL=nomic-embed-text

# Ollama base URL (обычно не менять)
OLLAMA_BASE_URL=http://localhost:11434

# Задержка debounce для watcher (мс)
WATCH_DEBOUNCE_MS=500

# Размер чанка при индексации (символы)
CHUNK_SIZE=1000
CHUNK_OVERLAP=200

# Количество чанков возвращаемых при поиске
TOP_K_RESULTS=5
```

---

## MCP Tools — что должно быть реализовано

### `search_knowledge_base(query: str) -> str`
Семантический поиск по vault. Возвращает топ-N релевантных чанков с метаданными (source path, заголовок, теги).

**Docstring для Claude:** "Search personal knowledge base from Obsidian vault. Use this to find context about specific projects, clients, workflow procedures, past decisions, personal preferences, team members, recurring errors, or any domain knowledge stored in notes. Call this before answering any question that might benefit from personal context."

### `create_note(title, content, project, tags, note_type) -> str`
Создаёт `.md` файл в vault с YAML frontmatter. Watcher подхватит его автоматически.

**Структура файла:**
```markdown
---
title: <title>
project: <project>
tags: [<tag1>, <tag2>]
type: <error|decision|research|note>
created: <ISO datetime>
---

<content>
```

**Путь сохранения:** `{VAULT}/Projects/{project}/{slugified-title}.md`

**Docstring для Claude:** "Create a new note in the Obsidian vault linked to a project. Use proactively when encountering an unusual error (record: what happened, why, how fixed), making an architectural decision, or discovering something worth remembering across sessions. Tags must be specific and searchable."

### `list_projects() -> str`
Возвращает список уникальных значений поля `project` из метаданных ChromaDB. Помогает Claude понять какие проекты существуют без поиска.

### `get_project_notes(project: str) -> str`
Возвращает все заметки конкретного проекта. Использовать когда нужен полный контекст проекта, а не точечный поиск.

---

## Технический стек

| Компонент | Библиотека | Версия |
|---|---|---|
| MCP протокол | `mcp` (Anthropic SDK) | latest |
| Векторная БД | `chromadb` | latest |
| Эмбеддинги | `ollama` + nomic-embed-text | latest |
| File watching | `watchdog` | latest |
| Env конфиг | `python-dotenv` | latest |
| Slugify | `python-slugify` | latest |

**Python:** 3.10+

---

## Кроссплатформенность

Код должен работать на **Windows 11** и **macOS** без изменений.

- Все пути — через `pathlib.Path`, никаких строковых слешей
- `watchdog` сам выбирает backend: `ReadDirectoryChangesW` (Windows) / `FSEvents` (Mac)
- Debounce 500ms решает проблему дублирующих событий на Windows
- `.env` принимает пути в любом формате (`C:\...`, `C:/...`, `~/...`)

---

## Логика индексации

### Первый запуск
```bash
python indexer.py --full-scan
```
Обходит весь vault, для каждого `.md`:
1. Парсит YAML frontmatter (теги, проект, тип, дата)
2. Чанкует текст по заголовкам H1/H2, затем по размеру (CHUNK_SIZE)
3. Генерирует эмбеддинги через Ollama
4. Сохраняет в ChromaDB с метаданными: `{source, title, project, tags, chunk_index}`

### Инкрементная (watcher)
При событии `on_modified` / `on_created`:
- Удаляет старые чанки этого файла из ChromaDB (по `source`)
- Переиндексирует файл заново (upsert)

При `on_deleted`:
- Удаляет все чанки файла из ChromaDB

---

## Подключение к Claude Code (VSCode)

После запуска сервера добавить в `.vscode/mcp.json` (или глобально в `~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "obsidian-rag": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "/абсолютный/путь/до/obsidian-rag-mcp",
      "env": {}
    }
  }
}
```

---

## Запуск

```bash
# 1. Клонировать и установить зависимости
pip install -r requirements.txt

# 2. Скопировать и заполнить конфиг
cp .env.example .env
# Открыть .env и указать OBSIDIAN_VAULT

# 3. Убедиться что Ollama запущена и модель скачана
ollama pull nomic-embed-text

# 4. Первичная индексация
python indexer.py --full-scan

# 5. Запуск MCP сервера (Claude Code запускает автоматически)
python server.py
```

---

## Что НЕ нужно делать

- Не использовать LangChain, LlamaIndex или другие тяжёлые фреймворки — только прямые вызовы библиотек
- Не хранить эмбеддинги в SQLite или JSON — только ChromaDB
- Не делать HTTP-сервер — только stdio транспорт (Claude Code требует именно его)
- Не индексировать файлы не-`.md` форматов
- Не трогать `data/chromadb/` руками — только через indexer

---

## Твой план действий

Перед написанием кода опиши план по каждому файлу:

1. Порядок создания файлов и почему именно такой
2. Ключевые решения в каждом модуле (структура данных, edge cases)
3. Что может пойти не так и как это обработать
4. Как проверить что всё работает (команды для теста)

Жди подтверждения плана перед тем как начинать писать код.