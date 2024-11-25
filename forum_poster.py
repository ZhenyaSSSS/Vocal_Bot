import requests
from bs4 import BeautifulSoup
import logging
import traceback
import time
from urllib.parse import urlparse

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

class ForumPoster:
    def __init__(self, forum_url, username, password):
        """
        Инициализация класса для публикации сообщений на форуме.
        
        Параметры:
            forum_url (str): URL форума (например, "https://musforums.ru/")
            username (str): Имя пользователя для авторизации
            password (str): Пароль пользователя
        """
        # Убеждаемся, что forum_url содержит схему и заканчивается на /
        if not forum_url.startswith(('http://', 'https://')):
            forum_url = 'https://' + forum_url
        if not forum_url.endswith('/'):
            forum_url += '/'
            
        # Извлекаем базовый домен
        parsed_url = urlparse(forum_url)
        self.base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"
        
        self.forum_url = forum_url
        self.login_url = requests.compat.urljoin(self.base_url, 'index.php?login/login')
        self.post_url = requests.compat.urljoin(self.base_url, 'index.php?threads/create/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        
        # Стандартные заголовки для запросов
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Connection': 'keep-alive',
            'Referer': self.login_url,
            'Origin': self.base_url.rstrip('/'),
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        self.session.headers.update(self.headers)

    def login(self):
        """
        Авторизация на форуме.
        
        Возвращает:
            bool: True при успешной авторизации, False при ошибке
        """
        logger.info("Попытка авторизации на форуме")
        try:
            response = self.session.get(self.login_url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            login_form = soup.find('form', {'action': lambda x: x and 'login/login' in x})
            
            csrf_token = soup.find('input', {'name': '_xfToken'})['value']
            redirect = soup.find('input', {'name': '_xfRedirect'})['value']
            
            login_data = {
                '_xfRedirect': redirect,
                '_xfToken': csrf_token,
                'login': self.username,
                'password': self.password,
                'remember': '1'
            }
            
            time.sleep(2)
            
            post_response = self.session.post(self.login_url, data=login_data, timeout=15, allow_redirects=True)
            post_response.raise_for_status()

            if "data-logged-in=\"true\"" in post_response.text:
                logger.info("Авторизация успешна")
                return True
            else:
                logger.error("Ошибка авторизации")
                return False

        except Exception as e:
            logger.error(f"Ошибка при авторизации: {e}")
            logger.error(traceback.format_exc())
            return False

    def create_thread(self, title, message, forum_id):
        """
        Создание новой темы на форуме.
        
        Параметры:
            title (str): Заголовок темы
            message (str): Текст сообщения
            forum_id (int): ID раздела форума
            
        Возвращает:
            bool: True при успешном создании, False при ошибке
        """
        logger.info(f"Создание темы: {title}")
        try:
            create_url = requests.compat.urljoin(self.forum_url, f'index.php?threads/create/?forumId={forum_id}')
            response = self.session.get(create_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            csrf_token = soup.find('input', {'name': '_xfToken'})['value']
            
            thread_data = {
                '_xfRedirect': self.forum_url,
                '_xfToken': csrf_token,
                'title': title,
                'message[message]': message,
                'forum_id': forum_id,
                'subscribe': '1'
            }
            
            post_response = self.session.post(create_url, data=thread_data, headers=self.headers, timeout=15)
            post_response.raise_for_status()
            
            if "Спасибо за Ваше сообщение" in post_response.text or "Topics - " in post_response.url:
                logger.info("Тема создана успешно")
                return True
            else:
                logger.error("Ошибка создания темы")
                return False

        except Exception as e:
            logger.error(f"Ошибка при создании темы: {e}")
            logger.error(traceback.format_exc())
            return False

    def reply_to_thread(self, thread_id, message):
        """
        Ответ в существующей теме.
        
        Параметры:
            thread_id (int): ID темы
            message (str): Текст ответа
            
        Возвращает:
            bool: True при успешной отправке, False при ошибке
        """
        logger.info(f"Отправка ответа в тему {thread_id}")
        try:
            thread_url = requests.compat.urljoin(self.forum_url, f'index.php?threads/{thread_id}/')
            response = self.session.get(thread_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            reply_form = soup.find('form', {'action': lambda x: x and '/add-reply' in x})
            if not reply_form:
                logger.error("Форма ответа не найдена")
                return False
            
            action = reply_form.get('action')
            reply_url = requests.compat.urljoin(self.forum_url, action)
            
            form_inputs = reply_form.find_all(['input', 'textarea', 'select'])
            form_data = {}
            for input_field in form_inputs:
                name = input_field.get('name')
                if not name:
                    continue
                value = input_field.get('value', '')
                if input_field.name == 'textarea':
                    value = input_field.text
                form_data[name] = value
            
            form_data['message'] = message
            form_data['subscribe'] = '1'
            
            time.sleep(2)
            
            post_response = self.session.post(reply_url, data=form_data, headers=self.headers, timeout=15)
            post_response.raise_for_status()
            return True

        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP ошибка: {http_err}")
            return False
        except Exception as e:
            logger.error(f"Ошибка при отправке ответа: {e}")
            logger.error(traceback.format_exc())
            return False
