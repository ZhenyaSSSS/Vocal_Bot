import PyInstaller.__main__
import os
import selenium_stealth
import shutil

# Получаем путь к текущей директории
current_dir = os.path.dirname(os.path.abspath(__file__))

# Копируем файлы selenium_stealth в локальную папку проекта
stealth_src = os.path.dirname(selenium_stealth.__file__)
stealth_dst = os.path.join(current_dir, 'selenium_stealth')

# Копируем файлы selenium_stealth, если их еще нет
if not os.path.exists(stealth_dst):
    shutil.copytree(stealth_src, stealth_dst)

PyInstaller.__main__.run([
    'bot_gui.py',  # основной файл
    '--name=Bot_Musforums',  # имя выходного файла
    '--noconsole',  # без консольного окна
    # Добавляем все необходимые файлы и папки
    '--add-data=add_info.txt;.',
    '--add-data=updated_memory.json;.',
    '--add-data=bot_config.json;.',
    '--add-data=bot.py;.',
    '--add-data=forum_poster.py;.',
    '--add-data=memory_updater.py;.',
    f'--add-data={stealth_dst};selenium_stealth',  # Добавляем папку selenium_stealth
    # Добавляем необходимые импорты
    '--hidden-import=google.generativeai',
    '--hidden-import=selenium_stealth',
    '--hidden-import=PyQt6',
    '--hidden-import=requests',
    '--hidden-import=bs4',
    '--hidden-import=urllib3',
    '--hidden-import=logging',
    '--hidden-import=json',
    '--hidden-import=difflib',
    '--hidden-import=openai',
    '--exclude-module=PyQt5',  # Добавлено исключение PyQt5
    '--clean',  # очистка временных файлов
])

# Очищаем временную папку selenium_stealth после сборки
if os.path.exists(stealth_dst):
    shutil.rmtree(stealth_dst) 