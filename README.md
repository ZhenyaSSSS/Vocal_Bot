# AI Vocal Bot для MusForums.ru

Интеллектуальный бот-помощник для анализа сообщений и предоставления рекомендаций на форуме MusForums.ru. Использует Google Gemini AI для анализа сообщений и ведения диалога с пользователями.

⚠️ **Важно:** Для работы бота в России требуется VPN, так как Google Gemini API недоступен на территории РФ.

## Основные компоненты системы

### 1. Графический интерфейс (BotGUI)

#### Вкладка "Управление"
- Управление работой бота (запуск/пауза/остановка)
- Настройки подключения к форуму (логин/пароль)
- Просмотр логов работы бота

#### Вкладка "Файлы"
- Просмотр и редактирование `add_info.txt`
- Просмотр и редактирование JSON файлов памяти
- Просмотр `thread_output.txt`

#### Вкладка "API ключи"
- Добавление/удаление ключей Google Gemini
- Управление списком активных ключей

### 2. Файловая система

#### add_info.txt
Файл с дополнительной информацией для работы бота

#### updated_memory.json
Текущая база знаний бота

#### new_memory.json
Временный файл с новыми данными для обновления памяти

#### old_memory.json
Предыдущая версия памяти бота (создается перед обновлением)

### 3. Логи
- `bot_gui.log` - основной лог работы GUI
- `parser.log` - лог работы парсера
- `memory_updates.log` - лог обновлений памяти

## Функциональность

### Мониторинг форума
- Отслеживание новых сообщений в указанных тредах
- Автоматическая обработка новых сообщений через Gemini AI
- Сохранение истории обработанных сообщений

### Безопасность
- Локальное хранение конфигурации в `bot_config.json`
- Система ротации API ключей
- Защита от дублирования сообщений
