import os
import logging
import google.generativeai as genai
from google.generativeai import GenerationConfig
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

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
USE_GENAI = True
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
        self.genai_model = None
        
    def pause(self):
        """Приостановка/возобновление работы бота"""
        self.paused = not self.paused
        status = "приостановлен" if self.paused else "возобновлен"
        logger.info(f"Бот {status}")
        self.status_updated.emit(f"Бот {status}")
        return self.paused
        
    def run(self):
        try:
            # Инициализируем API с ключами из конфигурации
            global API_KEYS, current_key_index
            API_KEYS = self.config['API_KEYS']
            current_key_index = 0
            
            if USE_GENAI and API_KEYS:
                os.environ['API_KEY'] = API_KEYS[current_key_index]
                genai.configure(api_key=os.environ["API_KEY"])
                generation_config = GenerationConfig(temperature=0.15)
                self.genai_model = genai.GenerativeModel("gemini-1.5-pro-002")
                logger.debug("Инициализирован Google Generative AI клиент.")
            
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
                                # Создаем конфигурацию форума
                                forum_config = {
                                    'forum_url': self.config['forum_url'],
                                    'username': self.config['username'],
                                    'password': self.config['password']
                                }
                                # Передаем конфигурацию форума
                                last_ids = check_new_messages(
                                    thread_url, 
                                    last_ids, 
                                    self.genai_model,
                                    forum_config
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

class BotGUI(QMainWindow):
    log_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Vocal Bot Control Panel")
        self.setMinimumSize(1200, 800)
        
        # Загрузка конфигурации из файла
        self.config_file = "bot_config.json"
        self.load_config()
        
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
        forum_layout.addWidget(self.forum_url_edit)
        settings_layout.addLayout(forum_layout)
        
        # Учетные данные
        creds_layout = QHBoxLayout()
        creds_layout.addWidget(QLabel("Логин:"))
        self.username_edit = QLineEdit(self.config['username'])
        creds_layout.addWidget(self.username_edit)
        creds_layout.addWidget(QLabel("Пароль:"))
        self.password_edit = QLineEdit(self.config['password'])
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        creds_layout.addWidget(self.password_edit)
        settings_layout.addLayout(creds_layout)
        
        # Интервал проверки
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel("Интервал проверки (сек):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(self.config['check_interval'])
        interval_layout.addWidget(self.interval_spin)
        settings_layout.addLayout(interval_layout)
        
        # Добавляем настройку лимита сообщений после интервала проверки
        message_limit_layout = QHBoxLayout()
        message_limit_layout.addWidget(QLabel("Лимит сообщений:"))
        self.message_limit_spin = QSpinBox()
        self.message_limit_spin.setRange(1, 1000)
        self.message_limit_spin.setValue(self.config['MESSAGE_LIMIT'])
        message_limit_layout.addWidget(self.message_limit_spin)
        settings_layout.addLayout(message_limit_layout)
        
        settings_group.setLayout(settings_layout)
        control_layout.addWidget(settings_group)
        
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
        
        # Лог
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        control_layout.addWidget(self.log_text)
        
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
        self.log_text.append(message)
        
    def start_bot(self):
        if not self.bot_worker:
            # Проверка наличия API ключей
            if not self.config['API_KEYS']:
                QMessageBox.critical(
                    self,
                    "Ошибка",
                    "Необходимо добавить хотя бы один API ключ перед запуском бота.\n"
                    "Перейдите на вкладку 'API ключи' и добавьте ключ."
                )
                return
            
            self.update_config_from_ui()
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
        """Обновление конфигурации из UI и сохранение в файл"""
        self.config['forum_url'] = self.forum_url_edit.text()
        self.config['username'] = self.username_edit.text()
        self.config['password'] = self.password_edit.text()
        self.config['check_interval'] = self.interval_spin.value()
        self.config['MESSAGE_LIMIT'] = self.message_limit_spin.value()
        self.save_config()

    def closeEvent(self, event):
        """Обработка закрытия окна"""
        if self.bot_worker:
            self.stop_bot()
        if self.file_watcher:
            self.file_watcher.stop()
            self.file_watcher.wait()
        self.save_config()  # Сохраняем конфигурацию при закрытии
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
            'API_KEYS': []
        }
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        except FileNotFoundError:
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

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = BotGUI()
    window.show()
    sys.exit(app.exec())
