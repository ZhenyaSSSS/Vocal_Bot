import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import time
import logging
import re
import json
import os
from http.client import RemoteDisconnected
import traceback
import random
from logging.handlers import RotatingFileHandler
import difflib
import openai
import google.generativeai as genai
from google.generativeai import GenerationConfig
import urllib.parse
from selenium import webdriver
from selenium_stealth import stealth
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from forum_poster import ForumPoster
from memory_updater import update_memory
import glob
import sys

# Фильтры для логирования
class WarningErrorFilter(logging.Filter):
    def filter(self, record):
        return record.levelno >= logging.WARNING

class ImportantMessageFilter(logging.Filter):
    def filter(self, record):
        return record.levelno >= logging.WARNING or (record.levelno == logging.INFO and "важное" in record.msg.lower())

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
console_handler.addFilter(ImportantMessageFilter())

file_handler = RotatingFileHandler('parser.log', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

memory_file_handler = RotatingFileHandler('memory_updates.log', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
memory_file_handler.setLevel(logging.INFO)
memory_file_handler.setFormatter(formatter)
memory_file_handler.addFilter(logging.Filter('memory'))

logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.addHandler(memory_file_handler)

class BotConfig:
    """Класс для хранения конфигурации бота"""
    def __init__(self, config_dict):
        self.forum_url = config_dict.get('forum_url', '')
        self.username = config_dict.get('username', '')
        self.password = config_dict.get('password', '')
        self.message_limit = config_dict.get('MESSAGE_LIMIT', 25)
        self.check_interval = config_dict.get('check_interval', 5)
        self.state_file = config_dict.get('STATE_FILE', 'last_id.json')
        self.api_keys = config_dict.get('API_KEYS', [])
        self.current_key_index = 0
        
        # Инициализация Gemini
        if self.api_keys:
            genai.configure(api_key=self.api_keys[0])
            self.generation_config = GenerationConfig(
                temperature=0.15,
                top_p=0.25,
                top_k=40,
                max_output_tokens=2048,
            )
            self.model = genai.GenerativeModel("gemini-1.5-pro-002")
        else:
            self.generation_config = None
            self.model = None
            logger.warning("Не найдены API ключи для инициализации модели Gemini")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
}
def switch_api_key(bot_config, content=None):
    """Переключение на следующий API ключ"""
    bot_config.current_key_index = (bot_config.current_key_index + 1) % len(bot_config.api_keys)
    new_key = bot_config.api_keys[bot_config.current_key_index]
    
    try:
        genai.configure(api_key=new_key)
        bot_config.model = genai.GenerativeModel("gemini-1.5-pro-002")
        
        if content and isinstance(content, list):
            new_content = []
            for part in content:
                try:
                    if hasattr(part, 'display_name'):
                        original_path = f"./{part.display_name}"
                        if os.path.exists(original_path):
                            new_file = safe_upload_file(original_path)
                            if new_file:
                                new_content.append(new_file)
                    else:
                        new_content.append(part)
                except AttributeError:
                    new_content.append(part)
            return True, new_content
            
        logger.info(f"Успешно переключились на API ключ {bot_config.current_key_index + 1}")
        return True, content
        
    except Exception as e:
        logger.error(f"Ошибка при переключении на ключ {bot_config.current_key_index + 1}: {e}")
        return False, content

def create_session():
    """Создание сессии с настроенными повторными попытками"""
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    logger.debug("Создана новая сессия с настроенными повторными попытками.")
    return session

session = create_session()

def load_sent_messages(sent_messages_path="sent_messages.json"):
    """Загрузка списка отправленных сообщений"""
    if os.path.exists(sent_messages_path):
        with open(sent_messages_path, 'r', encoding='utf-8') as f:
            try:
                sent_messages = json.load(f)
                logger.debug("Список отправленных сообщений загружен.")
                return sent_messages
            except json.JSONDecodeError:
                logger.warning("Файл отправленных сообщений поврежден. Инициализируется пустым списком.")
                return []
    logger.debug("Файл отправленных сообщений не найден. Инициализируется пустым списком.")
    return []

def save_sent_messages(sent_messages, sent_messages_path="sent_messages.json"):
    """Сохранение списка отправленных сообщений"""
    try:
        with open(sent_messages_path, 'w', encoding='utf-8') as f:
            json.dump(sent_messages, f, ensure_ascii=False, indent=4)
        logger.debug("Список отправленных сообщений сохранен.")
    except Exception as e:
        logger.error(f"Ошибка при сохранении отправленных сообщений: {e}")
        logger.error(traceback.format_exc())

def is_message_valid(message):
    """Проверка корректности сообщения"""
    if not message:
        logger.warning("Важное: Сообщение пустое.")
        return False
    if len(message) > 1000000:
        logger.warning("Важное: Сообщение слишком длинное.")
        return False
    return True

def is_duplicate(message, sent_messages, threshold=0.8):
    """Проверка сообщения на дубликат"""
    for sent in sent_messages:
        similarity = difflib.SequenceMatcher(None, message, sent).ratio()
        if similarity >= threshold:
            logger.debug(f"Сообщение является дубликатом с похожестью {similarity:.2f}.")
            return True
    return False

def read_memory(memory_file_path=".\\updated_memory.json"):
    """Чтение содержимого файла памяти"""
    logger.debug(f"Попытка чтения файла памяти: {memory_file_path}")
    try:
        with open(memory_file_path, 'r', encoding='utf-8') as mem_file:
            memory_content = mem_file.read()
            logger.debug("Содержимое файла памяти успешно прочитано.")
            return memory_content
    except FileNotFoundError:
        logger.error(f"Файл памяти не найден: {memory_file_path}")
        return "Файл памяти не найден."
    except Exception as e:
        logger.error(f"Ошибка при чтении файла памяти {memory_file_path}: {e}")
        logger.error(traceback.format_exc())
        return "Ошибка при чтении файла памяти."

def load_last_id(file_path):
    """Загрузка последнего идентификатора сообщения"""
    logger.debug(f"Попытка загрузить состояние из файла: {file_path}")
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            try:
                data = json.load(file)
                if isinstance(data, dict):
                    logger.debug("Состояние успешно загружено.")
                    return data
                else:
                    logger.warning("Файл состояния имеет неверный формат. Инициализируется пустым состоянием.")
                    return {}
            except json.JSONDecodeError:
                logger.warning("Файл состояния повреждён. Инициализируется без последних ID.")
                return {}
    logger.debug("Файл состояния не найден. Инициализация пустого состояния.")
    return {}

def save_last_id(file_path, last_id):
    """Сохранение последнего идентификатора сообщений"""
    logger.debug(f"Сохранение состояния в файл: {file_path}")
    try:
        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump(last_id, file, ensure_ascii=False, indent=4)
        logger.debug("Состояние успешно сохранено.")
    except Exception as e:
        logger.error(f"Ошибка при сохранении состояния в файл {file_path}: {e}")
        logger.error(traceback.format_exc())

def extract_post_id(post_content):
    """Извлечение ID сообщения из HTML"""
    match = re.search(r'id="post-(\d+)"', post_content)
    if match:
        post_id = match.group(1)
        logger.debug(f"Извлечён ID сообщения: {post_id}")
        return post_id
    logger.debug("ID сообщения не найден.")
    return None

def extract_thread_id(thread_url):
    """Извлечение ID треда из URL"""
    match = re.search(r'\.(\d+)/', thread_url)
    if match:
        thread_id = match.group(1)
        logger.debug(f"Извлечён ID треда: {thread_id}")
        return thread_id
    logger.debug("ID треда не найден в URL.")
    return None

def list_threads(forum_url):
    """Получение списка URL всех тредов на странице форума"""
    logger.info(f"Запрос списка тредов с форума: {forum_url}")
    try:
        response = session.get(forum_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        logger.debug(f"Получен ответ от форума: {forum_url}")
    except (requests.exceptions.SSLError, RemoteDisconnected) as e:
        logger.error(f"SSL ошибка или разрыв соединения при запросе к форуму {forum_url}: {e}")
        logger.error(traceback.format_exc())
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при запросе к форуму {forum_url}: {e}")
        logger.error(traceback.format_exc())
        return []
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    threads = []
    thread_elements = soup.find_all('div', class_=re.compile(r'structItem--thread'))
    logger.debug(f"Найдено {len(thread_elements)} элементов тредов на странице.")
    for thread in thread_elements:
        title_div = thread.find('div', class_='structItem-title')
        if title_div:
            a_tag = title_div.find('a', href=True)
            if a_tag:
                thread_url = requests.compat.urljoin(forum_url, a_tag['href'])
                threads.append(thread_url)
                logger.debug(f"Добавлен URL треда: {thread_url}")
    
    if not threads:
        logger.warning("Не удалось найти ни одного треда на странице форума.")
    
    return threads

def extract_audio_links(content):
    """Извлечение аудиоссылок из HTML"""
    soup = BeautifulSoup(content, 'html.parser')
    audio_links = []
    
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href']
        if 'vocaroo.com' in href or 'voca.ro' in href or 'smule.com' in href:
            audio_links.append(href)
    
    return audio_links

def download_smule_audio(url, output_filename, max_retries=3, base_delay=5):
    """Скачивание аудио со Smule через selenium"""
    for attempt in range(max_retries):
        try:
            options = Options()
            options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Получаем путь к папке с исполняемым файлом
            if getattr(sys, 'frozen', False):
                application_path = os.path.dirname(sys.executable)
            else:
                application_path = os.path.dirname(os.path.abspath(__file__))
                
            # Путь к файлам selenium_stealth
            stealth_path = os.path.join(application_path, 'selenium_stealth')
            
            logger.info(f"Попытка {attempt + 1}/{max_retries} скачать аудио со Smule: {url}")
            driver = webdriver.Chrome(options=options)
            
            try:
                stealth(driver,
                    languages=["ru-RU", "ru"],
                    vendor="Google Inc.",
                    platform="Win32",
                    webgl_vendor="Intel Inc.",
                    renderer="Intel Iris OpenGL Engine",
                    fix_hairline=True,
                    js_path=stealth_path  # Используем локальный путь
                )
                
                try:
                    # Обработка VK редиректа
                    if 'vk.com/away.php' in url:
                        parsed = urllib.parse.urlparse(url)
                        query_params = urllib.parse.parse_qs(parsed.query)
                        url = query_params.get('to', [None])[0]
                        if url:
                            url = urllib.parse.unquote(url)
                        else:
                            raise ValueError("Не удалось извлечь URL из VK редиректа")

                    logger.info(f"Загрузка страницы Smule: {url}")
                    driver.get(url)
                    time.sleep(5)
                    
                    content = driver.page_source
                    audio_url = None
                    
                    # Поск ссылки на аудио
                    try:
                        audio_url = content.split('twitter:player:stream" content="')[1].split('">')[0].replace('amp;', '')
                        logger.info(f"Найден URL аудио через meta тег: {audio_url}")
                    except:
                        logger.info("Поиск аудио через meta тег не удался, пробуем другие способы...")
                    
                    if not audio_url:
                        match = re.search(r'"m4a":"([^"]+)"', content)
                        if match:
                            audio_url = match.group(1).replace('\\/', '/')
                            logger.info(f"Найден URL аудио через JSON: {audio_url}")
                    
                    if not audio_url:
                        try:
                            audio_element = WebDriverWait(driver, 10).until(
                                EC.presence_of_element_located((By.TAG_NAME, "audio"))
                            )
                            audio_url = audio_element.get_attribute('src')
                            logger.info(f"Найден URL аудио через audio тег: {audio_url}")
                        except:
                            logger.info("Поиск аудио через audio тег не удался")

                    if not audio_url:
                        raise ValueError("Не удалось найти ссылку на аудио")

                    # Скачивание файла
                    cookies = driver.get_cookies()
                    s = requests.Session()
                    for cookie in cookies:
                        s.cookies.set(cookie['name'], cookie['value'])
                    
                    download_headers = HEADERS.copy()
                    download_headers['Referer'] = url
                    s.headers.update(download_headers)
                    
                    audio_response = s.get(audio_url, timeout=30)
                    audio_response.raise_for_status()
                    
                    with open(output_filename, 'wb') as file:
                        file.write(audio_response.content)
                    
                    logger.info(f"Аудио со Smule успешно сохранено: {output_filename}")
                    return True
                    
                finally:
                    driver.quit()
            except Exception as e:
                logger.error(f"Ошибка при попытке {attempt + 1} скачивания аудио со Smule {url}: {str(e)}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.info(f"Ожидание {delay} секунд перед следующей попыткой...")
                    time.sleep(delay)
                else:
                    logger.error(f"Все попытки скачать аудио со Smule не удались: {url}")
                    return False
        except Exception as e:
            logger.error(f"Ошибка при попытке {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(base_delay)
            continue

def download_audio(url, filename):
    """Скачивание аудио по URL"""
    if 'smule.com' in url:
        return download_smule_audio(url, filename)
    
    # Обработка Vocaroo ссылок
    if url.startswith("https://voca.ro/"):
        audio_id = url.split("/")[-1]
        url = f"https://media1.vocaroo.com/mp3/{audio_id}"
    elif url.startswith("https://vocaroo.com/"):
        audio_id = url.split("/")[-1]
        url = f"https://media1.vocaroo.com/mp3/{audio_id}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
        'Referer': 'https://vocaroo.com/'
    }
    try:
        response = session.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        with open(filename, 'wb') as file:
            file.write(response.content)
        logger.info(f"Аудио успешно скачано: {filename}")
        print(f"Аудио успешно скачано: {filename}", url)
        return True
    except Exception as e:
        logger.error(f"Ошибка при скачиании аудио {url}: {e}")
        return False

def extract_image_links(content, base_url="https://musforums.ru"):
    """Извлечение ссылок на изображения из HTML"""
    soup = BeautifulSoup(content, 'html.parser')
    image_links = []
    
    supported_formats = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
    
    for img_tag in soup.find_all('img', src=True):
        src = img_tag['src']
        if (not img_tag.get('class') or 'smilie' not in img_tag.get('class')) and \
           any(src.lower().endswith(fmt) for fmt in supported_formats):
            if src.startswith('/'):
                src = f"{base_url}{src}"
            image_links.append(src)
    
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].lower()
        if any(href.endswith(fmt) for fmt in supported_formats):
            if href.startswith('/'):
                href = f"{base_url}{href}"
            image_links.append(href)
    
    return image_links
def parse_thread(url, bot_config):
    """
    Получает ссылку на тред и возвращает данные в формате словаря:
    {
        'title': str,
        'creator': str,
        'messages': list of dicts with keys 'id', 'author', 'content',
        'unique_audio_links': list of str,
        'unique_image_links': list of str
    }
    """
    logger.info(f"Парсинг треда: {url}")
    messages = []
    page = 1
    
    while True:
        page_url = f"{url}page-{page}" if page > 1 else url
        logger.debug(f"Обработка страницы {page}: {page_url}")
        
        try:
            response = session.get(page_url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            logger.debug(f"Получен ответ от страницы {page}: {page_url}")
        except (requests.exceptions.SSLError, RemoteDisconnected) as e:
            logger.error(f"SSL ошибка или разрыв соединения при запросе к странице {page_url}: {e}")
            logger.error(traceback.format_exc())
            break
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе к странице {page_url}: {e}")
            logger.error(traceback.format_exc())
            break

        soup = BeautifulSoup(response.content, 'html.parser')

        if page == 1:
            # звлечение заголовка темы
            title_tag = soup.find('h1', class_='p-title-value')
            title = title_tag.text.strip() if title_tag else "Без заголовка"
            logger.debug(f"Заголовок треда: {title}")

            # Извлечение создателя темы
            creator_tag = soup.find('a', class_='username')
            creator = creator_tag.text.strip() if creator_tag else "Неизвестный"
            logger.debug(f"Создатель треда: {creator}")

        # Извлечение сообщений
        posts = soup.find_all('div', class_='message-inner')
        logger.debug(f"Найдено {len(posts)} сообщений на странице {page}.")
        
        if not posts:
            logger.debug(f"Сообщения не найдены на странице {page}. Завершение парсинга.")
            break

        for post in posts:
            user_tag = post.find('a', class_='username')
            user = user_tag.text.strip() if user_tag else "Неизвестный пользователь"
            
            message_content = post.find('div', class_='message-content')
            # Сохраняем ссылки отдельно, не добавляя их в текст
            audio_links = extract_audio_links(str(message_content))
            image_links = extract_image_links(str(message_content))
            
            if message_content:
                # Удаляем ненужные элементы
                for unwanted in message_content.find_all('div', class_='bbCodeBlock-expandLink'):
                    unwanted.extract()
                
                # Обрабатываем смайлики
                for img in message_content.find_all('img', class_='smilie'):
                    alt_text = img.get('alt', '')
                    if alt_text:
                        img.replace_with(alt_text)
                
                # Обрабатываем цитаты
                quotes = message_content.find_all('blockquote', class_='bbCodeBlock--quote')
                for quote in quotes:
                    # Получаем автора цитаты
                    title_div = quote.find('div', class_='bbCodeBlock-title')
                    author = title_div.get_text(strip=True) if title_div else None
                    
                    # Получаем содержимое цитаты
                    content_div = quote.find('div', class_='bbCodeBlock-expandContent')
                    if not content_div:
                        content_div = quote.find('div', class_='bbCodeBlock-content')
                    
                    if content_div:
                        # Рекурсивно обрабатываем смайлики внутри цитаты
                        for img in content_div.find_all('img', class_='smilie'):
                            alt_text = img.get('alt', '')
                            if alt_text:
                                img.replace_with(alt_text)
                        
                        content_text = content_div.get_text(strip=True)
                        
                        if author:
                            quote.replace_with(f'[QUOTE={author}]{content_text}[/QUOTE]')
                        else:
                            quote.replace_with(f'[QUOTE]{content_text}[/QUOTE]')
                
                # Обрабатываем спойлеры
                spoilers = message_content.find_all('div', class_='bbCodeSpoiler')
                for spoiler in spoilers:
                    title_span = spoiler.find('span', class_='bbCodeSpoiler-button-title')
                    spoiler_title = title_span.get_text(strip=True) if title_span else ''
                    content = spoiler.find('div', class_='bbCodeSpoiler-content')
                    if content:
                        content_text = content.get_text(strip=True)
                        if spoiler_title:
                            spoiler.replace_with(f'[SPOILER={spoiler_title}]{content_text}[/SPOILER]')
                        else:
                            spoiler.replace_with(f'[SPOILER]{content_text}[/SPOILER]')
                

                message = message_content.get_text(separator=' ', strip=True)
                
                # Добавляем информацию об аудио и изображениях в текст сообщения
                if audio_links:
                    message += "\n[Аудио от " + user + ": " + ", ".join(audio_links) + "]"
                if image_links:
                    message += "\n[Изображения от " + user + ": " + ", ".join(image_links) + "]"
            else:
                message = "Нет содержания."

            # Извлечение ID сообщения
            post_id = extract_post_id(str(post))

            messages.append({
                'id': post_id,
                'author': user,
                'content': message,
                'audio_links': audio_links,  # Сохраняем ссылки в структуре сообщения
                'image_links': image_links
            })
            logger.debug(f"Добавлено сообщение от {user} с ID {post_id}")

        # Проверяем наличие следующей страницы
        next_page = soup.find('a', class_='pageNav-jump--next')
        if not next_page:
            logger.debug(f"Следующая страница не найдена. Завершение парсинга на странице {page}.")
            break

        page += 1

    logger.info(f"Тред '{title}' успешно спарсен. Всего страниц: {page}")
    
    # Обрезаем сообщения до message_limit из конфигурации
    messages = messages[-bot_config.message_limit:]
    
    # Собираем ссылки только из отфильтрованных сообщений
    unique_audio_links = set()
    unique_image_links = set()
    
    for message in messages:
        unique_audio_links.update(message.get('audio_links', []))
        unique_image_links.update(message.get('image_links', []))

    logger.info(f"Найдено {len(unique_audio_links)} уникальных аудио и {len(unique_image_links)} уникальных изображений")

    return {
        'title': title,
        'creator': creator,
        'messages': messages,
        'unique_audio_links': list(unique_audio_links),
        'unique_image_links': list(unique_image_links)
    }

def write_thread_to_file(thread_data, file_path="thread_output.txt", memory_file_path=".\\updated_memory.json"):
    """
    Записывает данные треда в указанный файл в формате Markdown, добавляя содержимое памяти в начале.
    
    Формат файла:
    # Memory:
    (содержимое файла updated_memory.json)
    
    # Title:
    **Заголовок треда**
    
    # Creator:
    *Создатель треда*
    
    # Messages:
    **Автор:** Сообщение
    ...
    """
    logger.info(f"Запись треда '{thread_data['title']}' в файл: {file_path}")
    
    # Чтение содержимого памяти
    memory_content = read_memory(memory_file_path)
    
    try:
        with open(file_path, 'w', encoding='utf-8') as file:
            # Запись секции Memory
            file.write("# Memory:\n")
            file.write(f"{memory_content}\n\n")
            
            # Запись заголовка
            file.write("# Title:\n")
            file.write(f"**{thread_data['title']}**\n\n")
            
            # Запись создателя
            file.write("# Creator:\n")
            file.write(f"*{thread_data['creator']}*\n\n")
            
            # Запись сообщений
            file.write("# Messages:\n")
            for i, message in enumerate(thread_data['messages']):
                author = message['author']
                content = message['content']
                # Добавляем метку [LAST MESSAGE] если это последнее сообщение
                if i == len(thread_data['messages']) - 1:
                    file.write(f"**{author}:** {content} [LAST MESSAGE]\n\n")
                else:
                    file.write(f"**{author}:** {content}\n\n")
        logger.info(f"Тред '{thread_data['title']}' успешно записан в файл.")
    except Exception as e:
        logger.error(f"Ошибка при записи в файл {file_path}: {e}")
        logger.error(traceback.format_exc())

def send_to_openai(content):
    """
    Отправляет запрос к OpenAI API с переданным содержимым.
    
    :param content: Строка, содержащая объединённую информацию из add_info.txt и thread_output.txt
    :return: Ответ от модели в формате JSON
    """
    logger.info("Отправка запроса к OpenAI API.")
    try:
        response = openai.ChatCompletion.create(
            model='gpt-3.5-turbo',
            messages=[{"role":"user","content":content}],
            temperature=0.1,
            top_p=0.25,
            frequency_penalty=2,
            n=1,
            # response_format по умолчанию JSON, поэтому можно не указывать
        )
        logger.info("Важное: Получен ответ от OpenAI API.")
        logger.info(f"Содержимое ответа OpenAI: {response.choices[0].message.content}")
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка при обращении к OpenAI API: {e}")
        logger.error(traceback.format_exc())
        return None

def send_to_genai(content, bot_config, max_retries=3, base_delay=5):
    """Отправка запроса к Gemini API с использованием конфигурации"""
    if isinstance(content, list):
        logger.info(f"Отправка списка в GenAI. Длина списка: {len(content)} элементов")
    else:
        logger.info(f"Отправка текста в GenAI. Длина текста: {len(content)} символов")
    
    current_content = content
    
    for attempt in range(max_retries * len(bot_config.api_keys)):
        try:
            response = bot_config.model.generate_content(
                contents=current_content, 
                generation_config=bot_config.generation_config
            )
            logger.info("Важное: Получен ответ от Google Generative AI.")
            return response.text
            
        except Exception as e:
            logger.error(f"Ошибка при попытке {attempt + 1}: {e}")
            
            if "quota" in str(e).lower() or "permission" in str(e).lower():
                logger.warning("Обнаружено превышение квоты или ошибка доступа, переключаемся на следующий ключ")
                success, current_content = switch_api_key(bot_config, current_content)
                if not success:
                    continue
            
            if attempt < (max_retries * len(bot_config.api_keys) - 1):
                delay = base_delay * (2 ** (attempt % max_retries))
                logger.info(f"Ожидание {delay} секунд перед следующей попыткой...")
                time.sleep(delay)
            else:
                logger.error("Все попытки и ключи исчерпаны.")
                return None

def handle_new_message(thread_id, genai_request=None, genai_model=None, forum_config=None):
    """
    Обрабатывает новое сообщение: читает необходимые файлы, отправляет запрос в выбранный API,
    обновляет память и логирует изменения.
    
    Args:
        thread_id: ID треда
        genai_request: Запрос для Gemini API (опционально)
        genai_model: Модель Gemini (опционально)
        forum_config: Словарь с настройками форума (опционально)
    """
    if forum_config is None:
        forum_config = {
            'forum_url': "",  # Будет установлено из GUI
            'username': "",   # Будет установлено из GUI
            'password': ""    # Будет установлено из GUI
        }
    
    logger.info("Обнаружено новое сообщение. Начало обработки.")
    # time.sleep(20)
    # Загрузка списка отправленных сообщений
    sent_messages = load_sent_messages()
    # Чтение содержимого add_info.txt
    try:
        with open('add_info.txt', 'r', encoding='utf-8') as f:
            add_info = f.read()
        logger.debug("Содержимое add_info.txt успешно прочитано.")
    except Exception as e:
        logger.error(f"Ошибка при чтении add_info.txt: {e}")
        logger.error(traceback.format_exc())
        add_info = ""
    
    # Чтение содержимого thread_output.txt
    try:
        with open('thread_output.txt', 'r', encoding='utf-8') as f:
            thread_output = f.read()
        logger.debug("Содержимое thread_output.txt успешно прочитано.")
    except Exception as e:
        logger.error(f"Ошибка при чтении thread_output.txt: {e}")
        logger.error(traceback.format_exc())
        thread_output = ""
    
    # Объединение содержимого
    combined_content = f"{add_info}\n\n{thread_output}"
    logger.debug("Содержимое add_info.txt и thread_output.txt объединено.")
    if genai_request:
        # Используем переданную модель
        genai_response = send_to_genai([combined_content] + genai_request, genai_model)
    else:
        # Используем переданную модель
        genai_response = send_to_genai(combined_content, genai_model)
    response_content = genai_response
    if response_content:
        # Проверяем, является ли ответ допустимым JSON
        try:
            openai_data = json.loads(response_content[7:-4])
            logger.debug("Ответ модели успешно преобразован в JSON.")
        except json.JSONDecodeError:
            logger.error("Ответ от OpenAI не является допустимым JSON.")
            openai_data = {"need_comment": False}  # Или какой-либо другой дефолтный объект
        logger.debug("Ответ от GenAI преобразован в JSON-совместимый формат.")
        
        # Получение сообщения для отправки
        reply_message = openai_data.get("message")
        
        if reply_message and is_message_valid(reply_message):
            if not is_duplicate(reply_message, sent_messages):
                # Отправка сообщения на форум
                poster = ForumPoster(forum_config['forum_url'], 
                                   forum_config['username'], 
                                   forum_config['password'])
                if poster.login():
                    success = poster.reply_to_thread(thread_id, reply_message)
                    if success:
                        logger.info("Важное: Сообщение успешно отправлено на форум.")
                        sent_messages.append(reply_message)
                        save_sent_messages(sent_messages)
                        # Увеличиваем счетчик сообщений, если необходимо
                    else:
                        logger.error("Не удалось отправить сообщение на форум.")
                    # time.sleep(30)
                else:
                    logger.error("Авторизация не удалась. Сообщение не было отправлено.")
            else:
                logger.info("Важное: Сообщение дубликат. Отправка не выполнена.")
        else:
            print(openai_data)
            logger.info("Важное: Сообщение отсутствует или не прошло проверку корректности.")
        
        # Сохранение ответа в new_memory.json
        if "need_comment" in openai_data:
            try:
                with open('new_memory.json', 'w', encoding='utf-8') as f:
                    json.dump(openai_data, f, ensure_ascii=False, indent=2)
                logger.info("Ответ от модели сохранён в new_memory.json.")
            except Exception as e:
                logger.error(f"Ошибка при сохранении new_memory.json: {e}")
                logger.error(traceback.format_exc())
            
            # Копирование updated_memory.json в old_memory.json
            try:
                if os.path.exists('updated_memory.json'):
                    os.replace('updated_memory.json', 'old_memory.json')
                    logger.debug("Файл updated_memory.json скопирован в old_memory.json.")
                else:
                    logger.warning("Файл updated_memory.json не найден для копирования.")
            except Exception as e:
                logger.error(f"Ошибка при копировани�� айла памяти: {e}")
                logger.error(traceback.format_exc())
            
            # Обновление памяти с помощью функции из json_pool.py
            message = update_memory('old_memory.json', 'new_memory.json', 'updated_memory.json')
            if message:
                logger.info(f"Сообщение для отправки: {message}")
                print(f"Сообщение для отправки: {message}")
            
            # Логирование обновлений памяти отдельно
            memory_logger = logging.getLogger('memory')
            memory_logger.addHandler(memory_file_handler)
            memory_logger.info(f"Updated Memory:\n{read_memory('updated_memory.json')}")
            memory_logger.removeHandler(memory_file_handler)
    else:
        logger.warning("Не удалось получить ответ от модели.")

def download_image(url, filename):
    """
    Скачивает изображение по указанному URL и сохраняет его под заданным именем.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
    }
    try:
        response = session.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        with open(filename, 'wb') as file:
            file.write(response.content)
        logger.info(f"Изображение успешно скачано: {filename}")
        print(f"Изображение успешно скачано: {filename}", url)
        return True
    except Exception as e:
        logger.error(f"Ошибка при скачивании изображения {url}: {e}")
        return False

def safe_upload_file(file_path, max_retries=3, delay=2):
    """Безопасная загрузка файла с повторными попытками"""
    for attempt in range(max_retries):
        try:
            return genai.upload_file(file_path)
        except Exception as e:
            logger.error(f"Попытка {attempt + 1}/{max_retries} загрузки файла {file_path} не удалась: {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                logger.error(f"Не удалось загрузить файл {file_path} после {max_retries} попыток")
                return None

def cleanup_temp_files(file_patterns=None):
    """Очистка временных файлов по заданным шаблонам"""
    if file_patterns is None:
        file_patterns = ['audio_*.mp3', 'image_*.*']
    
    logger.debug("Начало очистки временных файлов")
    try:
        for pattern in file_patterns:
            for file_path in glob.glob(pattern):
                try:
                    os.remove(file_path)
                    logger.debug(f"Удален временный файл: {file_path}")
                except Exception as e:
                    logger.error(f"Ошибка при удалении файла {file_path}: {e}")
        logger.info("Очистка временных файлов завершена")
    except Exception as e:
        logger.error(f"Ошибка при очистке временных файлов: {e}")

def check_new_messages(thread_url, last_ids, bot_config):
    """Проверка новых сообщений с использованием конфигурации"""
    try:
        logger.info(f"Проверка новых сообщений для треда: {thread_url}")
        thread_data = parse_thread(thread_url, bot_config)
        
        if not thread_data:
            logger.warning(f"Важное: Не удалось получить данные треда {thread_url}. Возможно, он недоступен.")
            return last_ids

        messages = thread_data['messages']
        if not messages:
            logger.warning(f"Важное: Сообщения в треде {thread_url} не найдены.")
            return last_ids

        thread_id = extract_thread_id(thread_url)
        if not thread_id:
            logger.warning(f"Важное: Не удалось извлечь идентификатор треда из URL: {thread_url}")
            return last_ids

        latest_message = messages[-1]
        latest_id = latest_message['id']

        if thread_id in last_ids:
            if latest_id and latest_id != last_ids[thread_id]:
                if latest_message['author'] != "AI_Vocal_Bot":
                    print(f"**Новое сообщение в треде '{thread_data['title']}' от {latest_message['author']}:**\n{latest_message['content']}\n")
                    logger.info(f"Найдено новое сообщение в треде '{thread_data['title']}' от {latest_message['author']}")
                    
                    last_ids[thread_id] = latest_id
                    write_thread_to_file(thread_data, "thread_output.txt", ".\\updated_memory.json")
                    
                    media_files = []
                    media_ids = []
                    
                    try:
                        for i, audio_link in enumerate(thread_data['unique_audio_links']):
                            audio_filename = f'audio_{i+1}.mp3'
                            if download_audio(audio_link, audio_filename):
                                media_files.append(audio_filename)
                                media_ids.append(f'audio_{i+1}.mp3: {audio_link}')
                        
                        for i, image_link in enumerate(thread_data['unique_image_links']):
                            image_filename = f'image_{i+1}.{image_link.split(".")[-1]}'
                            if download_image(image_link, image_filename):
                                media_files.append(image_filename)
                                media_ids.append(f'image_{i+1}: {image_link}')
                        
                        genai_request = []
                        for i, media_file in enumerate(media_files):
                            uploaded_file = safe_upload_file(media_file)
                            if uploaded_file is not None:
                                genai_request.extend([media_ids[i], uploaded_file])
                            else:
                                logger.warning(f"Пропуск файла {media_file} из-за ошибки загрузки")
                        
                        if genai_request:
                            handle_new_message(thread_id, genai_request, bot_config, {
                                'forum_url': bot_config.forum_url,
                                'username': bot_config.username,
                                'password': bot_config.password
                            })
                        else:
                            handle_new_message(thread_id, None, bot_config, {
                                'forum_url': bot_config.forum_url,
                                'username': bot_config.username,
                                'password': bot_config.password
                            })
                    finally:
                        # Очистка временных файлов после обработки
                        cleanup_temp_files()
        else:
            print(f"**Новый тред '{thread_data['title']}' от {thread_data['creator']}:**\n{latest_message['content']}\n")
            logger.info(f"Обнаружен новый тред '{thread_data['title']}' от {thread_data['creator']}")
            
            last_ids[thread_id] = latest_id
            write_thread_to_file(thread_data, "thread_output.txt", ".\\updated_memory.json")
            
            media_files = []
            media_ids = []
            
            try:
                for i, audio_link in enumerate(thread_data['unique_audio_links']):
                    audio_filename = f'audio_{i+1}.mp3'
                    if download_audio(audio_link, audio_filename):
                        media_files.append(audio_filename)
                        media_ids.append(f'audio_{i+1}.mp3: {audio_link}')
                
                for i, image_link in enumerate(thread_data['unique_image_links']):
                    image_filename = f'image_{i+1}.{image_link.split(".")[-1]}'
                    if download_image(image_link, image_filename):
                        media_files.append(image_filename)
                        media_ids.append(f'image_{i+1}: {image_link}')
                
                genai_request = []
                for i, media_file in enumerate(media_files):
                    uploaded_file = safe_upload_file(media_file)
                    if uploaded_file is not None:
                        genai_request.extend([media_ids[i], uploaded_file])
                    else:
                        logger.warning(f"Пропуск файла {media_file} из-за ошибки загрузки")
                
                if genai_request:
                    handle_new_message(thread_id, genai_request, bot_config, {
                        'forum_url': bot_config.forum_url,
                        'username': bot_config.username,
                        'password': bot_config.password
                    })
                else:
                    handle_new_message(thread_id, None, bot_config, {
                        'forum_url': bot_config.forum_url,
                        'username': bot_config.username,
                        'password': bot_config.password
                    })
            finally:
                # Очистка временных файлов после обработки
                cleanup_temp_files()
    except Exception as e:
        logger.error(f"Ошибка при проверке новых сообщений: {e}")
        cleanup_temp_files()  # Очистка в случае ошибки
    
    return last_ids