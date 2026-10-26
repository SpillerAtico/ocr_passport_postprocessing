import json
import re
import csv
from datetime import datetime

VALID_CODES = set()
try:
    with open('reference/subdivisions.csv', 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if len(row) > 0:
                VALID_CODES.add(row[0].strip())
except FileNotFoundError:
    pass


def get_text(field_data):
    if not field_data or 'lines' not in field_data:
        return ""
    return " ".join([line['text'].strip() for line in field_data['lines']])


def is_quality_good(quality):
    if quality.get('glare', 0) > 0.2: return False
    if quality.get('sharpness', 0) < 90: return False
    return True


def check_confidence(viz):
    for field_name, field_data in viz.items():
        if field_data is None: return False
        if field_data.get('conf', 0) < 0.96: return False
        for line in field_data['lines']:
            for char_conf in line.get('char_conf', []):
                if char_conf < 0.95: return False
    return True


def has_latin(text):
    return bool(re.search(r'[a-zA-Z]', text))


def is_valid_date(date_str, is_birth=False):
    if not re.match(r'\d{2}\.\d{2}\.\d{4}', date_str): return False
    try:
        day, month, year = map(int, date_str.split('.'))
        if year < 1950 or year > 2026: return False
        if is_birth and year > 2012: return False
        datetime(year, month, day)
        return True
    except ValueError:
        return False


def transliterate_mrz(mrz_text):
    """Переводит латиницу из MRZ обратно в кириллицу для сравнения"""
    # Базовые правила транслитерации (упрощенные)
    mapping = {
        'A': 'А', 'B': 'Б', 'V': 'В', 'G': 'Г', 'D': 'Д', 'E': 'Е', '2': 'Е', 'Z': 'З', '3': 'З',
        'I': 'И', 'Y': 'Й', 'K': 'К', 'L': 'Л', 'M': 'М', 'N': 'Н', 'O': 'О', '0': 'О', 'P': 'П',
        'R': 'Р', 'S': 'С', 'T': 'Т', 'U': 'У', 'F': 'Ф', 'H': 'Х', 'C': 'Ц', '4': 'Ч',
        'W': 'Ш', 'Q': 'Щ', 'X': 'Ъ', 'J': 'Ы', '1': 'Ь'
    }
    # Берем только первую строку (там фамилия и имя)
    name_part = mrz_text.split('<<')[0]
    # Убираем лишние символы <
    name_part = name_part.replace('<', '')
    # Переводим посимвольно
    result = "".join([mapping.get(char, char) for char in name_part])
    return result


def extract_and_validate_fields(viz, mrz):
    fields = {}

    # 1. ФИО
    last_name = get_text(viz.get('Last_name_ru')).upper()
    first_name = get_text(viz.get('First_name_ru')).upper()
    middle_name = get_text(viz.get('Middle_name_ru')).upper()

    for name in [last_name, first_name, middle_name]:
        if has_latin(name) or any(c.isdigit() for c in name) or "  " in name or not re.match(r'^[А-ЯЁ\- ]+$', name):
            return None

    # Сверка с MRZ
    if mrz and 'lines' in mrz and len(mrz['lines']) > 0:
        mrz_name_line = mrz['lines'][0]['text']
        if mrz_name_line:
            mrz_cyrillic = transliterate_mrz(mrz_name_line)
            # Проверяем, что начало фамилии из viz совпадает с началом из MRZ
            # (часто в MRZ фамилия пишется без мягких знаков и т.д.)
            if last_name[:4] not in mrz_cyrillic[:10]:
                return None  # Если не совпадает — значит, в viz ошибка

    fields['Last_name_ru'] = last_name
    fields['First_name_ru'] = first_name
    fields['Middle_name_ru'] = middle_name

    # 2. Пол
    sex = get_text(viz.get('Sex_ru'))
    if "МУЖ" in sex:
        fields['Sex_ru'] = "МУЖ."
    elif "ЖЕН" in sex:
        fields['Sex_ru'] = "ЖЕН."
    else:
        return None

    # 3. Даты
    birth_date = get_text(viz.get('Birth_date'))
    issue_date = get_text(viz.get('Issue_date'))

    if not is_valid_date(birth_date, is_birth=True): return None
    if not is_valid_date(issue_date, is_birth=False): return None

    fields['Birth_date'] = birth_date
    fields['Issue_date'] = issue_date

    # 4. Код подразделения
    code = get_text(viz.get('Issue_organisation_code'))
    fields['Issue_organisation_code'] = code
    if VALID_CODES and code not in VALID_CODES:
        return None

    # 5. Место рождения (запрещаем цифры и латиницу)
    birth_place = get_text(viz.get('Birth_place_ru'))
    if has_latin(birth_place) or any(c.isdigit() for c in birth_place): return None
    place_keywords = ["ОБЛ", "КРАЙ", "РЕСП", "Р-Н", "РАЙОН", "Г.", "С.", "П.", "Д.", "ГОР."]
    if not any(kw in birth_place.upper() for kw in place_keywords):
        return None
    fields['Birth_place_ru'] = birth_place

    # 6. Кем выдан (запрещаем цифры и латиницу)
    org = get_text(viz.get('Issue_organization_ru'))
    if has_latin(org) or any(c.isdigit() for c in org): return None
    org_keywords = ["ОВД", "МВД", "ОТДЕЛ", "УПРАВЛЕНИЕ", "РОССИИ", "ПОЛИЦИИ", "ГУ", "УФМС", "ТП"]
    if not any(kw in org.upper() for kw in org_keywords):
        return None
    fields['Issue_organization_ru'] = org

    # 7. Номер лицензии
    licence = get_text(viz.get('Licence_number'))
    if not re.match(r'^\d{2}\s\d{2}\s\d{6}$', licence):
        return None
    fields['Licence_number'] = licence

    return fields


def main():
    input_file = 'data/test_predictions.jsonl'
    output_file = 'answers.jsonl'

    accepted = 0
    rejected = 0

    with open(input_file, 'r', encoding='utf-8') as f_in, \
            open(output_file, 'w', encoding='utf-8') as f_out:

        for line in f_in:
            if not line.strip(): continue
            doc = json.loads(line)
            doc_id = doc['doc_id']

            if not is_quality_good(doc['quality']) or not check_confidence(doc['viz']):
                result = {"doc_id": doc_id, "decision": "reject", "fields": {}}
                rejected += 1
            else:
                extracted_fields = extract_and_validate_fields(doc['viz'], doc.get('mrz'))
                if extracted_fields is not None:
                    result = {"doc_id": doc_id, "decision": "accept", "fields": extracted_fields}
                    accepted += 1
                else:
                    result = {"doc_id": doc_id, "decision": "reject", "fields": {}}
                    rejected += 1

            f_out.write(json.dumps(result, ensure_ascii=False) + '\n')

    print(f"Готово! Принято: {accepted}, Отклонено: {rejected}")


if __name__ == "__main__":
    main()