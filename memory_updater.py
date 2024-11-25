import json
import difflib

def update_memory(old_memory_path: str, new_data_path: str, updated_memory_path: str):
    """
    Обновляет память бота, объединяя старые данные с новыми.
    
    :param old_memory_path: Путь к файлу со старой памятью
    :param new_data_path: Путь к файлу с новыми данными
    :param updated_memory_path: Путь для сохранения обновленной памяти
    :return: Сообщение для отправки или None
    """
    # Загружаем старую память и новые данные
    with open(old_memory_path, 'r', encoding='utf-8') as f:
        old_memory = json.load(f)
    
    with open(new_data_path, 'r', encoding='utf-8') as f:
        new_data = json.load(f)
    
    message = new_data.get("message")
    memory_update = new_data.get("memory_update", {})
    
    def merge_dicts(old, new):
        """Рекурсивно объединяет два словаря с учетом специальной обработки списков"""
        for key, value in new.items():
            if isinstance(value, dict):
                old[key] = merge_dicts(old.get(key, {}), value)
            elif isinstance(value, list):
                if key == "additional_information":
                    # Обработка списка дополнительной информации
                    existing = old.get(key, [])
                    existing_set = set(existing) if all(isinstance(item, str) for item in existing) else set(map(tuple, existing))
                    
                    # Добавляем только уникальные элементы
                    for item in value:
                        if isinstance(item, str):
                            if item not in existing_set:
                                existing.append(item)
                                existing_set.add(item)
                        elif isinstance(item, dict):
                            item_tuple = tuple(item.items())
                            if item_tuple not in existing_set:
                                existing.append(item)
                                existing_set.add(item_tuple)
                    
                    # Ограничиваем размер списка
                    old[key] = existing[:70]
                else:
                    old[key] = value
            else:
                old[key] = value
        return old
    
    # Объединяем старую память с обновлениями
    updated_memory = merge_dicts(old_memory, memory_update)
    
    # Сохраняем обновленную память
    with open(updated_memory_path, 'w', encoding='utf-8') as f:
        json.dump(updated_memory, f, ensure_ascii=False, indent=2)
    
    return message
