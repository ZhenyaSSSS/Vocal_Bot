import os
import logging
import google.generativeai as genai
from google.generativeai import GenerationConfig
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *
from bot import BotConfig
# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

file_handler = logging.FileHandler('bot_gui.log', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Глобальные настройки
import sys
import json
import logging
from bot import check_new_messages, list_threads, load_last_id, save_last_id
import os
from functools import partial

class JsonHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules = []

        # Форматы для разных элементов JSON
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#569CD6"))
        keyword_format.setFontWeight(QFont.Weight.Bold)

        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#CE9178"))

        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#B5CEA8"))

        # Правила подсветки
        self.highlighting_rules.append(('\".*\":', keyword_format))  # ключи
        self.highlighting_rules.append(('\".*\"', string_format))    # строки
        self.highlighting_rules.append(('\\b\\d+\\.?\\d*\\b', number_format))  # числа

    def highlightBlock(self, text):
        for pattern, format in self.highlighting_rules:
            import re
            for match in re.finditer(pattern, text):
                self.setFormat(match.start(), match.end() - match.start(), format)

class FileWatcher(QThread):
    file_changed = pyqtSignal(str, str)  # путь к файлу, новое содержимое

    def __init__(self, files_to_watch):
        super().__init__()
        self.files_to_watch = files_to_watch
        self.running = True
        self.file_timestamps = {file: self.get_file_timestamp(file) for file in files_to_watch}

    def get_file_timestamp(self, file):
        try:
            return os.path.getmtime(file)
        except OSError:
            return 0

    def run(self):
        while self.running:
            for file in self.files_to_watch:
                current_timestamp = self.get_file_timestamp(file)
                if current_timestamp != self.file_timestamps[file]:
                    try:
                        with open(file, 'r', encoding='utf-8') as f:
                            content = f.read()
                        self.file_changed.emit(file, content)
                        self.file_timestamps[file] = current_timestamp
                    except Exception as e:
                        print(f"Ошибка при чтении файла {file}: {e}")
            self.msleep(1000)  # проверка каждую секунду

    def stop(self):
        self.running = False

class BotWorker(QThread):
    message_received = pyqtSignal(str)
    status_updated = pyqtSignal(str)
    
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.running = True
        self.paused = False
        self.bot_config = BotConfig(config)  # Создаем объект конфигурации бота
        
    def pause(self):
        """Приостановка/возобновление работы бота"""
        self.paused = not self.paused
        status = "приостановлен" if self.paused else "возобновлен"
        logger.info(f"Бот {status}")
        self.status_updated.emit(f"Бот {status}")
        return self.paused
        
    def run(self):
        try:
            last_ids = load_last_id(self.config['STATE_FILE'])
            
            while self.running:
                if not self.paused:
                    try:
                        threads = list_threads(self.config['forum_url'])
                        if threads:
                            self.status_updated.emit(f"Проверка {len(threads)} тредов...")
                            for thread_url in threads:
                                if not self.running or self.paused:
                                    break
                                # Передаем объект конфигурации бота
                                last_ids = check_new_messages(
                                    thread_url, 
                                    last_ids, 
                                    self.bot_config
                                )
                                self.message_received.emit(f"Проверен тред: {thread_url}")
                        
                        save_last_id(self.config['STATE_FILE'], last_ids)
                        
                    except Exception as e:
                        logger.error(f"Ошибка: {str(e)}", exc_info=True)
                        self.message_received.emit(f"Ошибка: {str(e)}")
                    
                if not self.running:
                    break
                    
                self.msleep(self.config['check_interval'] * 1000)
                
        except Exception as e:
            logger.error(f"Критическая ошибка в потоке бота: {str(e)}", exc_info=True)
            self.message_received.emit(f"Критическая ошибка: {str(e)}")
        finally:
            logger.info("Поток бота завершен")

    def stop(self):
        """Безопасная остановка потока"""
        logger.info("Запрошена остановка потока бота")
        self.running = False
        self.wait(5000)  # Ждем до 5 секунд
        if self.isRunning():
            logger.warning("Поток не завершился корректно, принудительное завершение")
            self.terminate()
            self.wait()

class LogHandler(logging.Handler):
    def __init__(self, signal):
        super().__init__()
        self.signal = signal
        
    def emit(self, record):
        msg = self.format(record)
        self.signal.emit(msg)

class JsonTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Ключ", "Значение"])
        self.setColumnCount(2)
        self.itemDoubleClicked.connect(self.edit_item)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        
        # Добавляем кнопки управления
        self.layout = QVBoxLayout()
        button_layout = QHBoxLayout()
        
        add_btn = QPushButton("Добавить элемент")
        add_btn.clicked.connect(lambda: self.add_item(self.invisibleRootItem()))
        button_layout.addWidget(add_btn)
        
        delete_btn = QPushButton("Удалить элемент")
        delete_btn.clicked.connect(self.delete_selected_item)
        button_layout.addWidget(delete_btn)
        
        # Добавляем кнопку сброса памяти
        reset_btn = QPushButton("Сбросить память")
        reset_btn.clicked.connect(self.reset_memory)
        button_layout.addWidget(reset_btn)
        
        self.layout.addWidget(self)
        self.layout.addLayout(button_layout)

    def edit_item(self, item, column):
        if not item:
            return
            
        current_text = item.text(column)
        new_text, ok = QInputDialog.getText(
            self, 
            "Редактировать элемент",
            "Введите новое значение:",
            text=current_text
        )
        
        if ok and new_text:
            item.setText(column, new_text)

    def show_context_menu(self, position):
        item = self.itemAt(position)
        menu = QMenu()
        
        add_action = menu.addAction("Добавить")
        edit_action = menu.addAction("Редактировать")
        delete_action = menu.addAction("Удалить")
        
        action = menu.exec(self.mapToGlobal(position))
        
        if action == add_action:
            self.add_item(item if item else self.invisibleRootItem())
        elif action == edit_action and item:
            self.edit_item(item, 0)
        elif action == delete_action and item:
            self.delete_item(item)

    def add_item(self, parent=None):
        if parent is None:
            parent = self.invisibleRootItem()
            
        key, ok = QInputDialog.getText(
            self,
            "Добавить элемент",
            "Введите ключ:"
        )
        
        if ok and key:
            value, ok = QInputDialog.getText(
                self,
                "Добавить элемент",
                "Введите значение:"
            )
            
            if ok:
                item = QTreeWidgetItem(parent)
                item.setText(0, key)
                item.setText(1, value)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.expandItem(parent)

    def delete_item(self, item):
        if item:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Icon.Question)
            msg.setText("Вы уверены, что хотите удалить этот элемент?")
            msg.setWindowTitle("Подтверждение удаления")
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            
            if msg.exec() == QMessageBox.StandardButton.Yes:
                (item.parent() or self.invisibleRootItem()).removeChild(item)

    def delete_selected_item(self):
        selected_items = self.selectedItems()
        if selected_items:
            self.delete_item(selected_items[0])

    def load_json(self, json_data):
        self.clear()
        self._add_json_items(json_data, self.invisibleRootItem())

    def _add_json_items(self, data, parent):
        if isinstance(data, dict):
            for key, value in data.items():
                item = QTreeWidgetItem(parent)
                item.setText(0, str(key))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                
                if isinstance(value, (dict, list)):
                    self._add_json_items(value, item)
                else:
                    item.setText(1, str(value))
        elif isinstance(data, list):
            for i, value in enumerate(data):
                item = QTreeWidgetItem(parent)
                item.setText(0, str(i))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                
                if isinstance(value, (dict, list)):
                    self._add_json_items(value, item)
                else:
                    item.setText(1, str(value))

    def get_json(self):
        return self._get_item_data(self.invisibleRootItem())

    def _get_item_data(self, item):
        if item == self.invisibleRootItem():
            # Для корневого элемента создаем словарь из всех дочерних элементов
            result = {}
            for i in range(item.childCount()):
                child = item.child(i)
                key = child.text(0)
                value = self._get_item_value(child)
                result[key] = value
            return result
        else:
            # Для остальных элементов определяем тип на основе структуры
            return self._get_item_value(item)

    def _get_item_value(self, item):
        # Если у элемента есть дочерние элементы
        if item.childCount() > 0:
            # Проверяем, является ли это списком (если ключи - числа)
            is_list = all(item.child(i).text(0).isdigit() 
                         for i in range(item.childCount()))
            
            if is_list:
                result = []
                # Создаем список нужной длины
                max_index = max(int(item.child(i).text(0)) 
                              for i in range(item.childCount()))
                result.extend(None for _ in range(max_index + 1))
                
                # Заполняем значения
                for i in range(item.childCount()):
                    child = item.child(i)
                    index = int(child.text(0))
                    result[index] = self._get_item_value(child)
                return result
            else:
                # Если это словарь
                result = {}
                for i in range(item.childCount()):
                    child = item.child(i)
                    key = child.text(0)
                    result[key] = self._get_item_value(child)
                return result
        else:
            # Если это конечный элемент, пробуем преобразовать значение
            value = item.text(1)
            try:
                if value.lower() == "true":
                    return True
                elif value.lower() == "false":
                    return False
                elif value.isdigit():
                    return int(value)
                elif value.replace(".", "", 1).isdigit():
                    return float(value)
                return value
            except:
                return value

    def reset_memory(self):
        """Сброс памяти к базовому состоянию"""
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText("Вы уверены, что хотите сбросить память к базовому состоянию?")
        msg.setInformativeText("Это действие нельзя отменить. Все текущие данные будут потеряны.")
        msg.setWindowTitle("Подтверждение сброса")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if msg.exec() == QMessageBox.StandardButton.Yes:
            base_memory = {
                "personality": {
                    "name": "Вокальный Эксперт",
                    "role": "Музыкальный консультант форума",
                    "tone": "профессиональный, но дружелюбный",
                    "expertise": [
                        "вокальная техника",
                        "музыкальная теория"
                    ],
                    "communication_style": {
                        "formal_level": "средний",
                        "encouragement_level": "высокий"
                    }
                },
                "forum_members": {
                    "users": {
                        "example_user": {
                            "recordings": [
                                {
                                    "title": "",
                                    "link": "",
                                    "analysis": {
                                        "overall": "",
                                        "intonation": "",
                                        "breathing": "",
                                        "emotionality": ""
                                    },
                                    "recommendations": []
                                }
                            ],
                            "comments": [],
                            "vocal_data": {
                                "strengths": [],
                                "weaknesses": []
                            }
                        }
                    }
                },
                "statistics": {
                    "total_reviews": 0,
                    "total_interactions": 0
                },
                "version": "1.0.0"
            }
            
            # Обновляем дерево и сохраняем в файл
            self.load_json(base_memory)
            try:
                with open('updated_memory.json', 'w', encoding='utf-8') as f:
                    json.dump(base_memory, f, ensure_ascii=False, indent=2)
                QMessageBox.information(self, "Успех", "Память успешно сброшена к базовому состоянию")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Ошибка при сохранении файла: {str(e)}")

class BotGUI(QMainWindow):
    log_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Vocal Bot Control Panel")
        self.setMinimumSize(1200, 800)
        
        # Загрузка конфигурации из файла
        self.config_file = "bot_config.json"
        self.load_config()
        
        # Инициализация API перед созданием UI
        if self.config.get('API_KEYS'):
            try:
                genai.configure(api_key=self.config['API_KEYS'][0])
            except Exception as e:
                logger.error(f"Ошибка при инициализации API: {e}")
        
        self.init_ui()
        self.setup_logging()
        self.bot_worker = None
        
        # Инициализация FileWatcher
        self.file_watcher = FileWatcher([
            'add_info.txt',
            'updated_memory.json',
            'thread_output.txt'
        ])
        self.file_watcher.file_changed.connect(self.update_file_content)
        self.file_watcher.start()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Создаем вкладки
        tabs = QTabWidget()
        layout.addWidget(tabs)
        
        # Вкладка управления
        control_tab = self.create_control_tab()
        tabs.addTab(control_tab, "Управление")
        
        # Вкладка файлов
        files_tab = self.create_files_tab()
        tabs.addTab(files_tab, "Файлы")
        
        # Вкладка API ключей
        api_keys_tab = self.create_api_keys_tab()
        tabs.addTab(api_keys_tab, "API ключи")
        
        # Добавляем новую вкладку настроек модели
        model_settings_tab = self.create_model_settings_tab()
        tabs.addTab(model_settings_tab, "Настройки модели")

    def create_control_tab(self):
        control_tab = QWidget()
        control_layout = QVBoxLayout(control_tab)
        
        # Группа основных настроек
        settings_group = QGroupBox("Основные настройки")
        settings_layout = QVBoxLayout()
        
        # URL форума
        forum_layout = QHBoxLayout()
        forum_layout.addWidget(QLabel("URL форума:"))
        self.forum_url_edit = QLineEdit(self.config['forum_url'])
        self.forum_url_edit.textChanged.connect(self.save_settings)  # Добавляем обработчик
        forum_layout.addWidget(self.forum_url_edit)
        settings_layout.addLayout(forum_layout)
        
        # Учетные данные
        creds_layout = QHBoxLayout()
        creds_layout.addWidget(QLabel("Логин:"))
        self.username_edit = QLineEdit(self.config['username'])
        self.username_edit.textChanged.connect(self.save_settings)  # Добавляем обработчик
        creds_layout.addWidget(self.username_edit)
        creds_layout.addWidget(QLabel("Пароль:"))
        self.password_edit = QLineEdit(self.config['password'])
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.textChanged.connect(self.save_settings)  # Добавляем обработчик
        creds_layout.addWidget(self.password_edit)
        settings_layout.addLayout(creds_layout)
        
        # Интервал проверки
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel("Интервал проверки (сек):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(self.config['check_interval'])
        self.interval_spin.valueChanged.connect(self.save_settings)  # Добавляем обработчик
        interval_layout.addWidget(self.interval_spin)
        settings_layout.addLayout(interval_layout)
        
        # Лимит сообщений
        message_limit_layout = QHBoxLayout()
        message_limit_layout.addWidget(QLabel("Лимит сообщений:"))
        self.message_limit_spin = QSpinBox()
        self.message_limit_spin.setRange(1, 1000)
        self.message_limit_spin.setValue(self.config['MESSAGE_LIMIT'])
        self.message_limit_spin.valueChanged.connect(self.save_settings)  # Добавляем обработчик
        message_limit_layout.addWidget(self.message_limit_spin)
        settings_layout.addLayout(message_limit_layout)
        
        settings_group.setLayout(settings_layout)
        control_layout.addWidget(settings_group)
        
        # Добавляем группу фильтров логов
        log_filter_group = QGroupBox("Фильтры логов")
        log_filter_layout = QHBoxLayout()
        
        self.log_filters = {
            'DEBUG': QCheckBox('DEBUG'),
            'INFO': QCheckBox('INFO'),
            'WARNING': QCheckBox('WARNING'),
            'ERROR': QCheckBox('ERROR'),
            'CRITICAL': QCheckBox('CRITICAL')
        }
        
        # По умолчанию включаем INFO и выше
        for level, checkbox in self.log_filters.items():
            checkbox.setChecked(level != 'DEBUG')
            checkbox.stateChanged.connect(self.update_log_display)
            log_filter_layout.addWidget(checkbox)
        
        log_filter_group.setLayout(log_filter_layout)
        control_layout.addWidget(log_filter_group)
        
        # Лог с поддержкой фильтрации
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_messages = []  # Сохраняем все сообщения для фильтрации
        control_layout.addWidget(self.log_text)
        
        # Кнопки управления
        buttons_layout = QHBoxLayout()
        self.start_button = QPushButton("Запустить")
        self.start_button.clicked.connect(self.start_bot)
        buttons_layout.addWidget(self.start_button)
        
        self.pause_button = QPushButton("Пауза")
        self.pause_button.clicked.connect(self.pause_bot)
        self.pause_button.setEnabled(False)
        buttons_layout.addWidget(self.pause_button)
        
        self.stop_button = QPushButton("Остановить")
        self.stop_button.clicked.connect(self.stop_bot)
        self.stop_button.setEnabled(False)
        buttons_layout.addWidget(self.stop_button)
        
        control_layout.addLayout(buttons_layout)
        
        return control_tab

    def create_files_tab(self):
        files_tab = QTabWidget()
        
        # add_info.txt
        add_info_tab = QWidget()
        add_info_layout = QVBoxLayout(add_info_tab)
        self.add_info_edit = QTextEdit()
        add_info_layout.addWidget(self.add_info_edit)
        save_add_info_btn = QPushButton("Сохранить add_info.txt")
        save_add_info_btn.clicked.connect(partial(self.save_file_content, 'add_info.txt', self.add_info_edit))
        add_info_layout.addWidget(save_add_info_btn)
        files_tab.addTab(add_info_tab, "add_info.txt")

        # updated_memory.json
        memory_tab = QWidget()
        memory_layout = QVBoxLayout(memory_tab)
        
        self.json_tree = JsonTreeWidget()
        memory_layout.addLayout(self.json_tree.layout)  # Используем layout из JsonTreeWidget
        
        save_memory_btn = QPushButton("Сохранить updated_memory.json")
        save_memory_btn.clicked.connect(self.save_json_memory)
        memory_layout.addWidget(save_memory_btn)
        files_tab.addTab(memory_tab, "updated_memory.json")

        # thread_output.txt
        thread_output_tab = QWidget()
        thread_output_layout = QVBoxLayout(thread_output_tab)
        self.thread_output_edit = QTextEdit()
        self.thread_output_edit.setReadOnly(True)
        thread_output_layout.addWidget(self.thread_output_edit)
        files_tab.addTab(thread_output_tab, "thread_output.txt")

        # Загрузка начального содержимого файлов
        self.load_initial_file_contents()

        return files_tab

    def create_api_keys_tab(self):
        api_keys_tab = QWidget()
        layout = QVBoxLayout(api_keys_tab)
        
        # Список API ключей
        self.api_keys_list = QListWidget()
        layout.addWidget(self.api_keys_list)
        
        # Кнопки управления ключами
        buttons_layout = QHBoxLayout()
        
        add_key_btn = QPushButton("Добавить ключ")
        add_key_btn.clicked.connect(self.add_api_key)
        buttons_layout.addWidget(add_key_btn)
        
        remove_key_btn = QPushButton("Удалить ключ")
        remove_key_btn.clicked.connect(self.remove_api_key)
        buttons_layout.addWidget(remove_key_btn)
        
        layout.addLayout(buttons_layout)
        
        # Загрузка существующих ключей
        self.load_api_keys()
        
        return api_keys_tab

    def load_initial_file_contents(self):
        try:
            with open('add_info.txt', 'r', encoding='utf-8') as f:
                self.add_info_edit.setPlainText(f.read())
        except FileNotFoundError:
            self.add_info_edit.setPlainText("")

        try:
            with open('updated_memory.json', 'r', encoding='utf-8') as f:
                content = json.load(f)
                self.json_tree.load_json(content)
        except FileNotFoundError:
            self.json_tree.load_json({})

        try:
            with open('thread_output.txt', 'r', encoding='utf-8') as f:
                self.thread_output_edit.setPlainText(f.read())
        except FileNotFoundError:
            self.thread_output_edit.setPlainText("")

    def update_file_content(self, file_path, content):
        if file_path == 'add_info.txt':
            self.add_info_edit.setPlainText(content)
        elif file_path == 'updated_memory.json':
            self.json_tree.load_json(json.loads(content))
        elif file_path == 'thread_output.txt':
            self.thread_output_edit.setPlainText(content)

    def save_file_content(self, file_path, editor):
        try:
            content = editor.toPlainText()
            
            # Для JSON файлов проверяем валидность и форматируем
            if file_path.endswith('.json'):
                try:
                    json_content = json.loads(content)
                    # Форматируем JSON с отступами
                    formatted_content = json.dumps(json_content, ensure_ascii=False, indent=2)
                    editor.setPlainText(formatted_content)  # Обновляем содержимое редактора
                    content = formatted_content  # Используем отформатированный контент для сохранения
                except json.JSONDecodeError as e:
                    QMessageBox.critical(self, "Ошибка", f"Неверный формат JSON: {str(e)}")
                    return
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            QMessageBox.information(self, "Успех", f"Файл {file_path} успешно сохранен")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка при сохранении файла: {str(e)}")

    def load_api_keys(self):
        self.api_keys_list.clear()
        self.api_keys_list.addItems(self.config['API_KEYS'])

    def add_api_key(self):
        key, ok = QInputDialog.getText(self, "Добавить API ключ", "Введите новый API ключ:")
        if ok and key:
            self.config['API_KEYS'].append(key)
            self.load_api_keys()
            self.save_config()

    def remove_api_key(self):
        current_item = self.api_keys_list.currentItem()
        if current_item:
            key = current_item.text()
            self.config['API_KEYS'].remove(key)
            self.load_api_keys()
            self.save_config()

    def setup_logging(self):
        handler = LogHandler(self.log_signal)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        logger = logging.getLogger()
        logger.addHandler(handler)
        
        self.log_signal.connect(self.update_log)
        
    def update_log(self, message):
        # Сохраняем сообщение
        self.log_messages.append(message)
        self.update_log_display()
        
        # Прокручиваем до конца
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_log_display(self):
        self.log_text.clear()
        for message in self.log_messages:
            # Определяем уровень лога из сообщения
            level = 'INFO'  # По умолчанию
            for possible_level in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
                if f' - {possible_level} - ' in message:
                    level = possible_level
                    break
            
            # Проверяем, должно ли сообщение отображаться
            if self.log_filters[level].isChecked():
                # Добавляем HTML-форматирование в зависимости от уровня
                color = {
                    'DEBUG': 'gray',
                    'INFO': 'black',
                    'WARNING': 'orange',
                    'ERROR': 'red',
                    'CRITICAL': 'darkred'
                }.get(level, 'black')
                
                formatted_message = f'<span style="color: {color};">{message}</span>'
                self.log_text.append(formatted_message)

    def start_bot(self):
        if not self.bot_worker:
            self.update_config_from_ui()
            
            genai.configure(api_key=self.config['API_KEYS'][0])
            
            # Создаем объект конфигурации для генерации
            generation_config = GenerationConfig(
                temperature=self.temp_spin.value(),
                top_p=self.top_p_spin.value(),
                top_k=self.top_k_spin.value(),
                max_output_tokens=self.max_tokens_spin.value(),
            )
            
            # Создаем модель с текущими настройками
            model = genai.GenerativeModel(self.model_combo.currentText())
            self.config['model'] = model
            self.config['generation_config'] = generation_config
        
            self.bot_worker = BotWorker(self.config)
            self.bot_worker.message_received.connect(self.update_log)
            self.bot_worker.status_updated.connect(self.update_log)
            self.bot_worker.start()
            
            self.start_button.setEnabled(False)
            self.pause_button.setEnabled(True)
            self.stop_button.setEnabled(True)

    def pause_bot(self):
        if self.bot_worker:
            is_paused = self.bot_worker.pause()
            self.pause_button.setText("Продолжить" if is_paused else "Пауза")
            
    def stop_bot(self):
        """Остановка бота"""
        if self.bot_worker:
            self.update_log("Останавливаем бота...")
            self.bot_worker.stop()  # Используем новый метод stop
            self.bot_worker = None
            self.start_button.setEnabled(True)
            self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.update_log("Бот остановлен")
            
    def update_config_from_ui(self):
        """Обновление конфигурации из UI"""
        self.config.update({
            'forum_url': self.forum_url_edit.text(),
            'username': self.username_edit.text(),
            'password': self.password_edit.text(),
            'check_interval': self.interval_spin.value(),
            'MESSAGE_LIMIT': self.message_limit_spin.value(),
        })
        self.save_config()

    def closeEvent(self, event):
        """Обработка закрытия окна"""
        if self.bot_worker:
            self.stop_bot()
        if self.file_watcher:
            self.file_watcher.stop()
            self.file_watcher.wait()
        self.save_config()  # Сохраняе конфигурацию при закрытии
        event.accept()

    def save_json_memory(self):
        try:
            content = self.json_tree.get_json()
            formatted_content = json.dumps(content, ensure_ascii=False, indent=2)
            with open('updated_memory.json', 'w', encoding='utf-8') as f:
                f.write(formatted_content)
            QMessageBox.information(self, "Успех", "Файл updated_memory.json успешно сохранен")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка при сохранении файла: {str(e)}")

    def load_config(self):
        """Загрузка конфигурации из файла"""
        default_config = {
            'forum_url': "https://musforums.ru/index.php?forums/%D0%A4%D0%BE%D1%80%D1%83%D0%BC-%D0%B2%D0%BE%D0%BA%D0%B0%D0%BB%D0%B8%D1%81%D1%82%D0%BE%D0%B2.9/",
            'username': "",
            'password': "",
            'MESSAGE_LIMIT': 25,
            'check_interval': 5,
            'STATE_FILE': "last_id.json",
            'API_KEYS': [],
        }
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                try:
                    self.config = json.load(f)
                    # Проверяем наличие всех необходимых полей
                    for key in default_config:
                        if key not in self.config:
                            self.config[key] = default_config[key]
                except json.JSONDecodeError as e:
                    logger.error(f"Ошибка при чтении конфигурации: {e}")
                    self.config = default_config
                    self.save_config()  # Пересоздаем файл с дефолтными значениями
        except FileNotFoundError:
            logger.info("Файл кнфигурации н найден, создаем новый с настройками по умолчанию")
            self.config = default_config
            self.save_config()

    def save_config(self):
        """Сохранение конфигурации в файл"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            logger.info("Конфигурация успешно сохранена")
        except Exception as e:
            logger.error(f"Ошибка при сохранении конфигурации: {e}")

    def save_settings(self):
        """Сохранение настроек при изменении любого поля"""
        # Создаем копию конфига без объекта модели
        config_to_save = {
            'forum_url': self.forum_url_edit.text(),
            'username': self.username_edit.text(),
            'password': self.password_edit.text(),
            'check_interval': self.interval_spin.value(),
            'MESSAGE_LIMIT': self.message_limit_spin.value(),
            'STATE_FILE': "last_id.json",
            'API_KEYS': self.config.get('API_KEYS', []),
            'model_name': self.model_combo.currentText(),
            'generation_config': {
                "temperature": float(self.temp_spin.value()),
                "top_p": float(self.top_p_spin.value()),
                "top_k": int(self.top_k_spin.value()),
                "max_output_tokens": int(self.max_tokens_spin.value())
            }
        }
        
        # Обновляем текущий конфиг, исключая объекты, которые нельзя сериализовать
        self.config.update({k: v for k, v in config_to_save.items() 
                           if not isinstance(v, (GenerationConfig, genai.GenerativeModel))})
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_to_save, f, ensure_ascii=False, indent=2)
            logger.debug("Настройки сохранены")
        except Exception as e:
            logger.error(f"Ошибка при сохранении конфигурации: {e}")

    def create_model_settings_tab(self):
        model_settings_tab = QWidget()
        layout = QVBoxLayout(model_settings_tab)
        
        # Группа выбора модели
        model_group = QGroupBox("Модель")
        model_layout = QVBoxLayout()
        
        # Список всех известных моделей
        all_models = [
            # Pro модели
            "gemini-1.5-pro-002",
            "gemini-1.5-pro",
            "gemini-1.0-pro",
            "gemini-pro",
            # Flash модели
            "gemini-1.5-pro-001-flash",
            "gemini-1.5-pro-flash",
            "gemini-pro-flash",
            # Vision модели
            "gemini-pro-vision",
            "gemini-1.0-pro-vision",
            "gemini-1.5-pro-vision"
        ]
        
        # Выбор модели
        model_select_layout = QHBoxLayout()
        
        # Выпадающий список для выбора модели
        model_layout.addWidget(QLabel("Выберите или введите название модели:"))
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)  # Разрешаем ручной ввод
        self.model_combo.addItems(all_models)
        current_model = self.config.get('model_name', "gemini-1.5-pro-002")
        if current_model in all_models:
            self.model_combo.setCurrentText(current_model)
        else:
            self.model_combo.addItem(current_model)
            self.model_combo.setCurrentText(current_model)
        
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        model_select_layout.addWidget(self.model_combo)
        
        # Кнопка для обновления списка доступных моделей
        refresh_models_btn = QPushButton("Обновить список")
        refresh_models_btn.clicked.connect(self.refresh_available_models)
        model_select_layout.addWidget(refresh_models_btn)
        
        model_layout.addLayout(model_select_layout)
        
        # Информация о модели
        self.model_info_text = QTextEdit()
        self.model_info_text.setReadOnly(True)
        self.model_info_text.setMaximumHeight(100)
        model_layout.addWidget(QLabel("Информация о модели:"))
        model_layout.addWidget(self.model_info_text)
        
        model_group.setLayout(model_layout)
        layout.addWidget(model_group)
        
        # Группа параметров генерации
        gen_group = QGroupBox("Параметры генерации")
        gen_layout = QGridLayout()
        
        # Temperature
        gen_layout.addWidget(QLabel("Temperature:"), 0, 0)
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 1.0)
        self.temp_spin.setSingleStep(0.01)
        self.temp_spin.setValue(self.config.get('generation_config', {}).get('temperature', 0.15))
        self.temp_spin.valueChanged.connect(self.save_settings)
        gen_layout.addWidget(self.temp_spin, 0, 1)
        
        # Top P
        gen_layout.addWidget(QLabel("Top P:"), 1, 0)
        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0.0, 1.0)
        self.top_p_spin.setSingleStep(0.01)
        self.top_p_spin.setValue(self.config.get('generation_config', {}).get('top_p', 0.25))
        self.top_p_spin.valueChanged.connect(self.save_settings)
        gen_layout.addWidget(self.top_p_spin, 1, 1)
        
        # Top K
        gen_layout.addWidget(QLabel("Top K:"), 2, 0)
        self.top_k_spin = QSpinBox()
        self.top_k_spin.setRange(1, 100)
        self.top_k_spin.setValue(self.config.get('generation_config', {}).get('top_k', 40))
        self.top_k_spin.valueChanged.connect(self.save_settings)
        gen_layout.addWidget(self.top_k_spin, 2, 1)
        
        # Max Output Tokens
        gen_layout.addWidget(QLabel("Max Output Tokens:"), 3, 0)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(1, 8192)
        self.max_tokens_spin.setValue(self.config.get('generation_config', {}).get('max_output_tokens', 2048))
        self.max_tokens_spin.valueChanged.connect(self.save_settings)
        gen_layout.addWidget(self.max_tokens_spin, 3, 1)
        
        gen_group.setLayout(gen_layout)
        layout.addWidget(gen_group)
        
        # Добавляем растягивающийся спейсер в конец
        layout.addStretch()
        
        return model_settings_tab

    def on_model_changed(self, model_name):
        """Обработчик изменения выбранной модели"""
        self.save_settings()
        self.update_model_info()

    def refresh_available_models(self):
        """Обновление списка доступных моделей через API"""
        try:
            # Проверяем наличие API ключей
            if not self.config.get('API_KEYS'):
                QMessageBox.warning(
                    self, 
                    "Ошибка", 
                    "API ключ не настроен. Пожалуйста, добавьте хотя бы один API ключ во вкладке 'API ключи'"
                )
                return
            
            # Настраиваем API с первым доступным ключом
            genai.configure(api_key=self.config['API_KEYS'][0])
            
            # Сохраняем текущий текст
            current_text = self.model_combo.currentText()
            
            # Очищаем список
            self.model_combo.clear()
            
            # Получаем список доступных моделей
            available_models = genai.list_models()
            model_names = []
            
            for model in available_models:
                model_names.append(model.name)
            
            # Обновляем выпадающий список
            self.model_combo.addItems(model_names)
            
            # Восстанавливаем текущий выбор
            if current_text in model_names:
                self.model_combo.setCurrentText(current_text)
            else:
                self.model_combo.addItem(current_text)
                self.model_combo.setCurrentText(current_text)
            
            # Обновляем информацию о текущей модели
            self.update_model_info()
            
            QMessageBox.information(self, "Успех", "Список моделей успешно обновлен")
            
        except Exception as e:
            error_message = str(e)
            if "No API_KEY" in error_message:
                error_message = "API ключ не настроен ии недействителен. Пожалуйста, проверьте настройки API ключей."
            
            QMessageBox.warning(self, "Ошибка", f"Не удалось получить список моделей: {error_message}")
            logger.error(f"Ошибка при обновлении списка моделей: {e}")

    def update_model_info(self):
        """Обновление информации о выбранной модели"""
        try:
            # Проверяем наличие API ключей
            if not self.config.get('API_KEYS'):
                self.model_info_text.setPlainText("API ключ не настроен. Информация о модели недоступна.")
                return
            
            # Настраиваем API с первым доступным ключом
            genai.configure(api_key=self.config['API_KEYS'][0])
            
            model_name = self.model_combo.currentText()
            available_models = genai.list_models()
            
            # Ищем информацию о текущей модели
            for model in available_models:
                if model.name == model_name:
                    info = (f"Модель: {model.name}\n"
                           f"Отображаемое имя: {model.display_name}\n"
                           f"Описание: {model.description}\n"
                           f"Поддерживаемые генерации: {', '.join(model.supported_generation_methods)}\n"
                           f"Токены: {model.input_token_limit} (вход) / {model.output_token_limit} (выход)")
                    self.model_info_text.setPlainText(info)
                    return
                
            self.model_info_text.setPlainText(f"Информация о модели '{model_name}' недоступна")
            
        except Exception as e:
            error_message = str(e)
            if "No API_KEY" in error_message:
                error_message = "API ключ не настроен или недействителен."
            self.model_info_text.setPlainText(f"Ошибка при получении информации о модели: {error_message}")
            logger.error(f"Ошибка при обновлении информации о модели: {e}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = BotGUI()
    window.show()
    sys.exit(app.exec())
