"""Сравнение OCR: Tesseract (как в приложении) и Indra OCR (datalab-to/indra-ocr).

Не часть приложения: запускается workflow ocr-bench.yml. Две команды:

    python scripts/bench_ocr.py make --out ocr-bench      # сканы с эталонным текстом
    python scripts/bench_ocr.py run --pages ocr-bench --tessdata tessdata --out ocr-Linux.json

make рисует страницы документов (английский, англ.-рус., англ.-укр.) и портит их
в трех степенях: clean (300 DPI), scan (200 DPI, наклон, шум, JPEG) и poor
(150 DPI, сильнее наклон, шум и сжатие). Каждая страница сохраняется как PDF из
одной картинки, как настоящий скан, рядом лежит эталонный текст.

run распознает каждую страницу движками и сравнивает с эталоном:
- f1: совпадение слов без учета порядка (мультимножества), главная метрика;
- cer: доля ошибочных символов, только для одноколоночных страниц без таблиц;
- pii: доля слов с цифрами или @ (телефоны, номера, почта), прочитанных точно;
- uk: доля слов с і/ї/є/ґ, прочитанных точно (страницы с украинским).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]
PLEX = ROOT / "src" / "md_blinder" / "ui" / "assets" / "fonts"

# ---------------------------------------------------------------- тексты

EN_LETTER = [
    ("h", "Northbridge Logistics Ltd."),
    ("p", "Dear Mr. Thompson,"),
    ("p", "Thank you for your letter of 14 March 2026 regarding the renewal of the service agreement "
          "No. SA-2024/0715 between Northbridge Logistics Ltd. and Harper & Cole Consulting LLP. We have "
          "reviewed the proposed amendments and are prepared to accept most of them, subject to the "
          "clarifications set out below."),
    ("p", "First, the payment terms in clause 5.2 should remain at thirty (30) days from the date of a "
          "correctly issued invoice. Our finance team, led by Ms. Rebecca Lawson, has confirmed that a "
          "shorter period cannot be accommodated without changes to our internal approval process."),
    ("p", "Second, the liability cap proposed in clause 9.1 (GBP 250,000) appears reasonable. However, we "
          "would ask that it does not apply to breaches of confidentiality or data protection obligations, "
          "which should remain uncapped."),
    ("p", "Third, please send all further correspondence to our new office at 42 Wellington Street, Leeds "
          "LS1 4DL, or by e-mail to legal@northbridge-logistics.co.uk. The direct telephone line of our "
          "legal department is +44 113 496 0821."),
    ("p", "We would be grateful if you could return a signed copy of the amended agreement by 30 April 2026."),
    ("p", "Yours sincerely,"),
    ("p", "Daniel Whitfield"),
    ("p", "Head of Legal Affairs"),
]

EN_CONTRACT = [
    ("h", "SOFTWARE LICENCE AGREEMENT"),
    ("p", "This Agreement is made on 2 June 2026 between Meridian Analytics Inc., a company incorporated in "
          "Delaware with registered number 7364512 (the Licensor), and Clearwater Medical Group LLC, "
          "located at 1180 Harbor Drive, San Diego, CA 92101 (the Licensee)."),
    ("p", "1. Grant of Licence. The Licensor grants the Licensee a non-exclusive, non-transferable licence "
          "to install and use the Software on up to 250 workstations for the internal business purposes "
          "of the Licensee only."),
    ("p", "2. Fees. The Licensee shall pay an annual fee of USD 48,600 within 45 days of receipt of each "
          "invoice. Late payments shall bear interest at 1.5% per month until paid in full."),
    ("p", "3. Support. The Licensor shall provide technical support by telephone at +1 (619) 555-0147 and "
          "by e-mail at support@meridian-analytics.com on business days between 8:00 a.m. and 6:00 p.m. "
          "Pacific Time."),
    ("p", "4. Data Protection. The Licensee remains the controller of all patient data processed by the "
          "Software. The Licensor shall process such data only on documented instructions and shall notify "
          "the Licensee of any personal data breach without undue delay, and in any event within 48 hours."),
    ("p", "5. Term and Termination. This Agreement commences on the date above and continues for three "
          "years. Either party may terminate it by giving at least 90 days written notice to the other party."),
    ("p", "Signed for the Licensor: Jonathan R. Mitchell, Chief Executive Officer."),
    ("p", "Signed for the Licensee: Dr. Maria Gonzalez, Chief Operating Officer."),
]

EN_REPORT = [
    ("h", "Quarterly Operations Review, Q2 2026"),
    ("p", "Summary. Shipments handled by the Rotterdam hub rose to 18,420 containers in the second quarter, "
          "up 7.4% on the first quarter and 11.2% on the same period last year. Average dwell time fell from "
          "4.1 to 3.6 days after the new yard scheduling system went live on 15 April."),
    ("p", "Costs. Operating costs were EUR 23.8 million, slightly above budget because of higher energy "
          "prices in May. Fuel surcharges recovered about two thirds of the increase. Overtime fell by 14% "
          "as the night shift was fully staffed for the first time since 2024."),
    ("p", "Safety. There were two lost-time incidents, both minor. The safety committee, chaired by Pieter "
          "van Dijk, recommended extra lighting along the north quay and a refresher course for reach "
          "stacker drivers, which will be completed by the end of August."),
    ("p", "Customers. Three new shipping lines signed contracts during the quarter. Customer satisfaction, "
          "measured by the regular survey of 312 clients, improved to 8.3 out of 10. The main complaints "
          "concerned the online booking portal, which will be replaced in October."),
    ("p", "Outlook. Volumes in July are tracking about 5% above plan. Management expects the full-year "
          "throughput to reach 74,000 containers, provided that the planned strike at the port of Antwerp "
          "does not divert traffic. Questions about this report may be sent to Sophie Laurent, "
          "s.laurent@northsea-terminals.eu, telephone +31 10 794 2286."),
    ("p", "Investments. The board approved EUR 6.2 million for two electric straddle carriers and a new "
          "gate system with automatic number plate recognition. Delivery of the carriers is expected in "
          "the first quarter of 2027, and the gate system should be in service before the winter peak."),
]

EN_INVOICE_HEAD = [
    ("h", "INVOICE No. INV-2026-00318"),
    ("p", "Date: 18 September 2026. Due date: 18 October 2026."),
    ("p", "Bill to: Ashford Property Management Ltd, 9 Kingsway, London WC2B 6XF, VAT GB 284 7163 92."),
]
EN_INVOICE_TABLE = [
    ["Item", "Description", "Qty", "Unit price", "Amount"],
    ["1", "Office cleaning, monthly", "3", "1,250.00", "3,750.00"],
    ["2", "Window cleaning", "2", "480.00", "960.00"],
    ["3", "Carpet shampooing", "14", "35.50", "497.00"],
    ["4", "Waste removal", "6", "62.00", "372.00"],
    ["5", "Consumables", "1", "214.80", "214.80"],
    ["6", "Emergency call-out", "1", "190.00", "190.00"],
    ["", "Subtotal", "", "", "5,983.80"],
    ["", "VAT 20%", "", "", "1,196.76"],
    ["", "Total due", "", "", "7,180.56"],
]
EN_INVOICE_TAIL = [
    ("p", "Please pay by bank transfer to Brightway Facility Services, sort code 20-45-77, account 53917284. "
          "Questions: accounts@brightway-services.co.uk."),
]

EN_RU_PARAGRAPHS = [
    ("h", "Minutes / Протокол"),
    ("p", "Minutes of the meeting of the Board of Directors of Volga Trading Company held on 12 May 2026 in Kazan."),
    ("p", "Протокол заседания Совета директоров ООО «Волжская торговая компания», состоявшегося 12 мая 2026 года в Казани."),
    ("p", "Present: Igor Sokolov (Chairman), Anna Petrova, Mikhail Orlov and Elena Smirnova. The quorum "
          "required by the Articles of Association was present."),
    ("p", "Присутствовали: Игорь Соколов (председатель), Анна Петрова, Михаил Орлов и Елена Смирнова. "
          "Кворум, установленный уставом общества, имеется."),
    ("p", "Agenda item 1: approval of the annual accounts for 2025. Net revenue amounted to RUB 412.7 "
          "million, an increase of 18% compared with the previous year."),
    ("p", "Вопрос 1: утверждение годовой бухгалтерской отчетности за 2025 год. Чистая выручка составила "
          "412,7 млн рублей, что на 18% больше, чем в предыдущем году."),
    ("p", "Agenda item 2: appointment of a new Chief Financial Officer. The Board resolved to appoint "
          "Dmitry Volkov, passport 92 15 604381, with effect from 1 June 2026."),
    ("p", "Вопрос 2: назначение нового финансового директора. Совет директоров решил назначить Дмитрия "
          "Волкова, паспорт 92 15 604381, с 1 июня 2026 года."),
    ("p", "Contact for questions: corporate secretary Olga Fedorova, tel. +7 (843) 555-28-14, e-mail "
          "o.fedorova@volgatrade.ru."),
    ("p", "Контактное лицо по вопросам: корпоративный секретарь Ольга Федорова, тел. +7 (843) 555-28-14, "
          "электронная почта o.fedorova@volgatrade.ru."),
]

EN_RU_COLUMNS = [
    ("SUPPLY AGREEMENT No. 17/26", "ДОГОВОР ПОСТАВКИ № 17/26"),
    ("1. The Supplier shall deliver to the Buyer laboratory equipment in the quantity and assortment "
     "specified in Annex 1 to this Agreement.",
     "1. Поставщик обязуется поставить Покупателю лабораторное оборудование в количестве и ассортименте, "
     "указанных в Приложении 1 к настоящему Договору."),
    ("2. The price of the equipment is RUB 2,845,000, including VAT at 20%. Payment shall be made within "
     "15 banking days after delivery.",
     "2. Стоимость оборудования составляет 2 845 000 рублей, включая НДС 20%. Оплата производится в "
     "течение 15 банковских дней после поставки."),
    ("3. Delivery shall be made to the Buyer's warehouse at 7 Promyshlennaya Street, Novosibirsk, no "
     "later than 30 September 2026.",
     "3. Поставка осуществляется на склад Покупателя по адресу: г. Новосибирск, ул. Промышленная, д. 7, "
     "не позднее 30 сентября 2026 года."),
    ("4. If the delivery is delayed, the Supplier shall pay a penalty of 0.1% of the price of the "
     "undelivered equipment for each day of delay.",
     "4. В случае просрочки поставки Поставщик уплачивает неустойку в размере 0,1% от стоимости "
     "непоставленного оборудования за каждый день просрочки."),
    ("5. Buyer: Siberian Research Centre JSC, INN 5408123456, KPP 540801001, represented by General "
     "Director Sergey Lebedev.",
     "5. Покупатель: АО «Сибирский научный центр», ИНН 5408123456, КПП 540801001, в лице генерального "
     "директора Сергея Лебедева."),
    ("6. Supplier: LabTech Solutions LLC, tel. +7 (383) 210-44-97, e-mail sales@labtech-nsk.ru.",
     "6. Поставщик: ООО «ЛабТех Решения», тел. +7 (383) 210-44-97, эл. почта sales@labtech-nsk.ru."),
]

EN_UK_PARAGRAPHS = [
    ("h", "Memorandum of Understanding / Меморандум про взаєморозуміння"),
    ("p", "This Memorandum of Understanding is entered into on 3 February 2026 between Dnipro Technologies "
          "LLC, represented by its Director Oksana Kovalenko, and Baltic Software Solutions Ltd, "
          "represented by its Chief Executive Officer James Walker."),
    ("p", "Цей Меморандум про взаєморозуміння укладено 3 лютого 2026 року між ТОВ «Дніпровські технології» "
          "в особі директорки Оксани Коваленко та компанією Baltic Software Solutions Ltd в особі "
          "генерального директора Джеймса Вокера."),
    ("p", "The Parties intend to cooperate in the development of a document management platform for public "
          "institutions in Ukraine. Each Party shall bear its own costs unless otherwise agreed in writing."),
    ("p", "Сторони мають намір співпрацювати у розробці платформи електронного документообігу для державних "
          "установ України. Кожна Сторона самостійно несе власні витрати, якщо інше не погоджено письмово."),
    ("p", "The Parties shall act in good faith and on the basis of mutual benefit."),
    ("p", "Сторони ґрунтуються на принципах добросовісності та взаємної вигоди."),
    ("p", "Contact details of Dnipro Technologies LLC: 22 Khreshchatyk Street, Kyiv 01001, Ukraine; EDRPOU "
          "code 41235678; telephone +380 44 235 17 90; e-mail office@dnipro-tech.com.ua."),
    ("p", "Реквізити ТОВ «Дніпровські технології»: вул. Хрещатик, 22, м. Київ, 01001, Україна; код ЄДРПОУ "
          "41235678; телефон +380 44 235 17 90; електронна пошта office@dnipro-tech.com.ua."),
    ("p", "This Memorandum is not legally binding, except for the provisions on confidentiality, which "
          "shall remain in force for three years after its signature."),
    ("p", "Цей Меморандум не є юридично обов'язковим, за винятком положень про конфіденційність, які "
          "залишаються чинними протягом трьох років після його підписання."),
]

EN_UK_COLUMNS = [
    ("SERVICES AGREEMENT No. 04-03/2026", "ДОГОВІР ПРО НАДАННЯ ПОСЛУГ № 04-03/2026"),
    ("1. The Contractor undertakes to provide translation and localisation services, and the Customer "
     "undertakes to accept and pay for these services in accordance with this Agreement.",
     "1. Виконавець зобов'язується надати послуги з перекладу та локалізації, а Замовник зобов'язується "
     "прийняти та оплатити ці послуги відповідно до цього Договору."),
    ("2. The total price of the services is UAH 184,500, including all taxes.",
     "2. Загальна вартість послуг становить 184 500 гривень, включно з усіма податками."),
    ("3. The services shall be provided from 1 March 2026 to 31 August 2026. The Customer may extend this "
     "period by giving written notice at least ten days in advance.",
     "3. Послуги надаються з 1 березня 2026 року по 31 серпня 2026 року. Замовник може продовжити цей "
     "строк, надіславши письмове повідомлення не пізніше ніж за десять днів."),
    ("4. Each Party shall keep confidential all information received from the other Party and shall not "
     "disclose it to third parties without prior written consent.",
     "4. Кожна Сторона зобов'язується зберігати конфіденційність усієї інформації, отриманої від іншої "
     "Сторони, і не розголошувати її третім особам без попередньої письмової згоди."),
    ("5. Contractor: individual entrepreneur Taras Hnatiuk, tax number 3125409876, IBAN UA21 3223 1300 "
     "0002 6007 2335 6600 1.",
     "5. Виконавець: фізична особа-підприємець Тарас Гнатюк, РНОКПП 3125409876, IBAN UA21 3223 1300 "
     "0002 6007 2335 6600 1."),
    ("6. Customer: Lingua Bridge GmbH, Friedrichstrasse 68, 10117 Berlin, e-mail contracts@linguabridge.de.",
     "6. Замовник: Lingua Bridge GmbH, Friedrichstrasse 68, 10117 Berlin, ел. пошта contracts@linguabridge.de."),
]

# --------------------------------------------------------------- рисование

DPI = 300
PAGE_W, PAGE_H = 2480, 3508  # A4 при 300 DPI
MARGIN = 260


def _font_path(name: str) -> Path:
    """Liberation (метрики Times New Roman и Arial) из системы, иначе IBM Plex из репозитория."""
    liberation = {"serif": "LiberationSerif-Regular.ttf", "serif-bold": "LiberationSerif-Bold.ttf",
                  "sans": "LiberationSans-Regular.ttf", "sans-bold": "LiberationSans-Bold.ttf"}
    plex = {"serif": "IBMPlexSerif-Regular.ttf", "serif-bold": "IBMPlexSerif-SemiBold.ttf",
            "sans": "IBMPlexSans-Regular.ttf", "sans-bold": "IBMPlexSans-Bold.ttf",
            "plex-serif": "IBMPlexSerif-Regular.ttf", "plex-serif-bold": "IBMPlexSerif-SemiBold.ttf",
            "plex-sans": "IBMPlexSans-Regular.ttf", "plex-sans-bold": "IBMPlexSans-Bold.ttf"}
    if name in liberation:
        for base in ("/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/liberation2"):
            path = Path(base) / liberation[name]
            if path.is_file():
                return path
    return PLEX / plex[name]


def _font(name: str, pt: float):
    from PIL import ImageFont

    return ImageFont.truetype(str(_font_path(name)), round(pt * DPI / 72))


def _wrap(text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}" if line else word
        if line and font.getlength(trial) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


class Sheet:
    def __init__(self):
        from PIL import Image, ImageDraw

        self.img = Image.new("L", (PAGE_W, PAGE_H), 255)
        self.draw = ImageDraw.Draw(self.img)
        self.truth: list[str] = []

    def block(self, x: int, y: int, width: int, text: str, font, spacing: float = 1.3) -> int:
        step = round(font.size * spacing)
        for line in _wrap(text, font, width):
            self.draw.text((x, y), line, font=font, fill=0)
            self.truth.append(line)
            y += step
        return y

    def flow(self, items, x: int, y: int, width: int, body: str, pt: float, gap: float = 0.6) -> int:
        regular, bold = _font(body, pt), _font(f"{body}-bold", pt * 1.35)
        for kind, text in items:
            font = bold if kind == "h" else regular
            y = self.block(x, y, width, text, font)
            y += round(font.size * gap)
        return y

    def check(self, y: int) -> None:
        assert y < PAGE_H - MARGIN // 2, f"текст не помещается на страницу: {y}"


def page_single(items, body: str, pt: float):
    sheet = Sheet()
    sheet.check(sheet.flow(items, MARGIN, MARGIN, PAGE_W - 2 * MARGIN, body, pt))
    return sheet


def page_two_columns(items, body: str, pt: float):
    """Заголовок на всю ширину, абзацы газетными колонками: левая, затем правая."""
    sheet = Sheet()
    width = PAGE_W - 2 * MARGIN
    y = sheet.flow(items[:1], MARGIN, MARGIN, width, body, pt)
    gap = 90
    col = (width - gap) // 2
    half = len(items) // 2
    y_left = sheet.flow(items[1:1 + half], MARGIN, y, col, body, pt)
    y_right = sheet.flow(items[1 + half:], MARGIN + col + gap, y, col, body, pt)
    sheet.check(max(y_left, y_right))
    return sheet


def page_parallel(rows, body: str, pt: float):
    """Двуязычный договор: строки пунктов, слева английский, справа перевод."""
    sheet = Sheet()
    width = PAGE_W - 2 * MARGIN
    gap = 90
    col = (width - gap) // 2
    regular, bold = _font(body, pt), _font(f"{body}-bold", pt * 1.15)
    y = MARGIN
    left_truth, right_truth = [], []
    for idx, (left, right) in enumerate(rows):
        font = bold if idx == 0 else regular
        before = len(sheet.truth)
        y_left = sheet.block(MARGIN, y, col, left, font)
        left_truth += sheet.truth[before:]
        before = len(sheet.truth)
        y_right = sheet.block(MARGIN + col + gap, y, col, right, font)
        right_truth += sheet.truth[before:]
        y = max(y_left, y_right) + round(font.size * 0.8)
    sheet.truth = left_truth + right_truth
    sheet.check(y)
    return sheet


def page_invoice(body: str, pt: float):
    sheet = Sheet()
    width = PAGE_W - 2 * MARGIN
    y = sheet.flow(EN_INVOICE_HEAD, MARGIN, MARGIN, width, body, pt)
    regular, bold = _font(body, pt), _font(f"{body}-bold", pt)
    shares = (0.08, 0.44, 0.1, 0.19, 0.19)
    xs = [MARGIN]
    for share in shares:
        xs.append(xs[-1] + round(width * share))
    row_h = round(regular.size * 2.0)
    pad = round(regular.size * 0.4)
    top = y
    for r, row in enumerate(EN_INVOICE_TABLE):
        font = bold if r == 0 or row[1] == "Total due" else regular
        for c, cell in enumerate(row):
            if cell:
                sheet.draw.text((xs[c] + pad, y + pad), cell, font=font, fill=0)
        sheet.truth.append(" ".join(cell for cell in row if cell))
        y += row_h
    for line_y in range(top, y + 1, row_h):
        sheet.draw.line([(xs[0], line_y), (xs[-1], line_y)], fill=0, width=3)
    for x in xs:
        sheet.draw.line([(x, top), (x, y)], fill=0, width=3)
    y += row_h
    sheet.check(sheet.flow(EN_INVOICE_TAIL, MARGIN, y, width, body, pt))
    return sheet


DOCS = {
    # имя: (группа, разметка, функция)
    "en_letter": ("en", "single", lambda: page_single(EN_LETTER, "serif", 12)),
    "en_contract": ("en", "single", lambda: page_single(EN_CONTRACT, "sans", 10.5)),
    "en_report": ("en", "columns", lambda: page_two_columns(EN_REPORT, "plex-serif", 9)),
    "en_invoice": ("en", "table", lambda: page_invoice("sans", 10.5)),
    "en_ru_paragraphs": ("en_ru", "single", lambda: page_single(EN_RU_PARAGRAPHS, "serif", 11.5)),
    "en_ru_columns": ("en_ru", "columns", lambda: page_parallel(EN_RU_COLUMNS, "plex-sans", 10)),
    "en_uk_paragraphs": ("en_uk", "single", lambda: page_single(EN_UK_PARAGRAPHS, "sans", 10.5)),
    "en_uk_columns": ("en_uk", "columns", lambda: page_parallel(EN_UK_COLUMNS, "serif", 11)),
}

LEVELS = {
    # dpi, наклон в градусах, размытие, шум, фон, цвет текста, качество JPEG, крапинки
    "clean": dict(dpi=300, angle=0.0, blur=0.0, noise=0.0, paper=255, ink=0, jpeg=92, speckle=0.0),
    "scan": dict(dpi=200, angle=1.0, blur=0.6, noise=8.0, paper=240, ink=40, jpeg=70, speckle=0.0),
    "poor": dict(dpi=150, angle=2.5, blur=1.0, noise=18.0, paper=222, ink=85, jpeg=40, speckle=0.0015),
}


def degrade(img, level: dict, seed: int) -> bytes:
    import numpy as np
    from PIL import Image, ImageFilter

    rng = np.random.default_rng(seed)
    if level["angle"]:
        angle = level["angle"] * rng.choice([-1, 1]) * rng.uniform(0.6, 1.0)
        img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    if level["dpi"] != DPI:
        scale = level["dpi"] / DPI
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    if level["blur"]:
        img = img.filter(ImageFilter.GaussianBlur(level["blur"]))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = level["ink"] + arr * (level["paper"] - level["ink"])
    if level["noise"]:
        arr += rng.normal(0, level["noise"], arr.shape)
    if level["speckle"]:
        mask = rng.random(arr.shape) < level["speckle"]
        arr[mask] = rng.choice([level["ink"], 255.0], size=int(mask.sum()))
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L")
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=level["jpeg"], dpi=(level["dpi"], level["dpi"]))
    return buf.getvalue()


def make(out: Path) -> None:
    import pymupdf

    out.mkdir(parents=True, exist_ok=True)
    pages = []
    for d_idx, (name, (group, layout, build)) in enumerate(DOCS.items()):
        sheet = build()
        for l_idx, (level_name, level) in enumerate(LEVELS.items()):
            jpeg = degrade(sheet.img, level, seed=1000 * d_idx + l_idx)
            doc = pymupdf.open()
            page = doc.new_page(width=595.28, height=841.89)
            page.insert_image(page.rect, stream=jpeg)
            pdf = f"{name}__{level_name}.pdf"
            doc.save(out / pdf)
            doc.close()
            pages.append(dict(doc=name, group=group, layout=layout, level=level_name, pdf=pdf,
                              truth="\n".join(sheet.truth)))
        sheet.img.save(out / f"{name}.png")
    (out / "pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(pages)} страниц в {out}")


# ---------------------------------------------------------------- метрики

_QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‘": "'", "’": "'",
                         "ʼ": "'", "–": "-", "—": "-", "‐": "-", "‑": "-", " ": " "})
_EDGE = "\"'.,;:!?()[]{}*|#<>"
UK_LETTERS = set("іїєґІЇЄҐ")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip()


def words(text: str) -> list[str]:
    tokens = (t.strip(_EDGE) for t in normalize(text).split(" "))
    return [t for t in tokens if t]


def word_scores(truth: str, got: str) -> dict:
    t, g = Counter(words(truth)), Counter(words(got))
    hit = sum((t & g).values())
    recall = hit / max(1, sum(t.values()))
    precision = hit / max(1, sum(g.values()))
    f1 = 2 * precision * recall / (precision + recall) if hit else 0.0

    def subset_recall(pred) -> float | None:
        sub = Counter({w: n for w, n in t.items() if pred(w)})
        if not sub:
            return None
        return sum((sub & g).values()) / sum(sub.values())

    return dict(f1=f1, recall=recall, precision=precision,
                pii=subset_recall(lambda w: "@" in w or any(ch.isdigit() for ch in w)),
                uk=subset_recall(lambda w: any(ch in UK_LETTERS for ch in w)))


def cer(truth: str, got: str) -> float:
    from rapidfuzz.distance import Levenshtein

    t, g = normalize(truth), normalize(got)
    return Levenshtein.distance(t, g) / max(1, len(t))


# ----------------------------------------------------------------- движки

def tesseract_engine(tessdata: Path, langs: str):
    """Путь приложения: convert/ocr.py, MuPDF + Tesseract, 144 DPI."""
    import pymupdf

    from md_blinder.convert import ocr

    if langs == ocr.OCR_LANGS:
        def run(pdf: Path) -> str:
            return ocr.ocr_pdf_pages(pdf, [1], tessdata)[1]
    else:
        def run(pdf: Path) -> str:
            with pymupdf.open(pdf) as doc:
                page = doc[0]
                tp = page.get_textpage_ocr(language="+".join(langs), dpi=ocr.OCR_DPI, full=True,
                                           tessdata=str(tessdata))
                return page.get_text(textpage=tp)
    return run


def indra_engine(threads: int):
    import indra_ocr

    started = time.perf_counter()
    conv = indra_ocr.load(threads=threads, mode="ocr")
    load_s = time.perf_counter() - started

    def run(pdf: Path) -> str:
        return "\n".join(page.text for _, page in conv.convert(str(pdf)))
    return run, load_s


def run(pages_dir: Path, tessdata: Path, out: Path, only: list[str] | None) -> None:
    from md_blinder.convert.ocr import OCR_LANGS

    pages = json.loads((pages_dir / "pages.json").read_text(encoding="utf-8"))
    threads = os.cpu_count() or 4
    engines: dict[str, tuple] = {}
    info: dict = dict(os=platform.platform(), python=platform.python_version(), cpu=platform.processor(),
                      cores=threads)
    if not only or "indra" in only:
        import indra_ocr
        import onnxruntime

        fn, load_s = indra_engine(threads)
        info.update(indra_version=indra_ocr.__version__, onnxruntime=onnxruntime.__version__,
                    ort_providers=onnxruntime.get_available_providers(), indra_load_s=round(load_s, 2))
        fn(pages_dir / pages[0]["pdf"])  # прогрев, не в замере
        engines["indra"] = (fn, None)
    if not only or "tesseract" in only:
        engines["tesseract"] = (tesseract_engine(tessdata, OCR_LANGS), None)
    if not only or "tesseract_eng" in only:
        # Только английский, для сравнения с набором языков приложения (rus+ukr+eng).
        engines["tesseract_eng"] = (tesseract_engine(tessdata, ("eng",)), "en")

    texts_dir = out.with_suffix("")
    texts_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for page in pages:
        for name, (fn, group) in engines.items():
            if group and page["group"] != group:
                continue
            started = time.perf_counter()
            try:
                text, error = fn(pages_dir / page["pdf"]), None
            except Exception as exc:  # noqa: BLE001
                text, error = "", f"{type(exc).__name__}: {exc}"
            seconds = time.perf_counter() - started
            row = dict(doc=page["doc"], group=page["group"], layout=page["layout"], level=page["level"],
                       engine=name, seconds=round(seconds, 3), error=error, **word_scores(page["truth"], text))
            row["cer"] = cer(page["truth"], text) if page["layout"] == "single" else None
            results.append(row)
            (texts_dir / f"{Path(page['pdf']).stem}__{name}.txt").write_text(text, encoding="utf-8")
            print(f"{page['doc']:18} {page['level']:6} {name:14} f1={row['f1']:.3f} "
                  f"cer={row['cer'] if row['cer'] is None else round(row['cer'], 3)} {seconds:.2f}s"
                  + (f" ОШИБКА {error}" if error else ""), flush=True)
    out.write_text(json.dumps(dict(info=info, results=results), ensure_ascii=False, indent=1), encoding="utf-8")
    print(summary(info, results))


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _fmt(value, pct: bool = True) -> str:
    if value is None:
        return "–"
    return f"{value * 100:.1f}" if pct else f"{value:.2f}"


def summary(info: dict, results: list[dict]) -> str:
    engines = list(dict.fromkeys(r["engine"] for r in results))
    groups = list(dict.fromkeys(r["group"] for r in results))
    levels = list(dict.fromkeys(r["level"] for r in results))
    lines = [f"### OCR: {info['os']}, ядер {info['cores']}", ""]
    if "indra_load_s" in info:
        lines += [f"Indra {info['indra_version']}, onnxruntime {info['onnxruntime']} "
                  f"({', '.join(info['ort_providers'])}), загрузка моделей {info['indra_load_s']} с", ""]
    header = "| группа | качество | " + " | ".join(
        f"{e} слова % | {e} CER % | {e} с/стр" for e in engines) + " |"
    lines += [header, "|" + "---|" * (2 + 3 * len(engines))]
    for group in groups:
        for level in levels:
            cells = []
            for engine in engines:
                rows = [r for r in results if r["engine"] == engine and r["group"] == group and r["level"] == level]
                cells += [_fmt(_mean(r["f1"] for r in rows)), _fmt(_mean(r["cer"] for r in rows)),
                          _fmt(_mean(r["seconds"] for r in rows), pct=False)]
            lines.append(f"| {group} | {level} | " + " | ".join(cells) + " |")
    lines += ["", "| движок | номера, телефоны, почта % | слова с і/ї/є/ґ % | таблица (слова) % | колонки (слова) % |",
              "|---|---|---|---|---|"]
    for engine in engines:
        rows = [r for r in results if r["engine"] == engine]
        lines.append(f"| {engine} | {_fmt(_mean(r['pii'] for r in rows))} | {_fmt(_mean(r['uk'] for r in rows))} | "
                     f"{_fmt(_mean(r['f1'] for r in rows if r['layout'] == 'table'))} | "
                     f"{_fmt(_mean(r['f1'] for r in rows if r['layout'] == 'columns'))} |")
    errors = [r for r in results if r["error"]]
    if errors:
        lines += ["", f"Ошибок: {len(errors)}, первая: {errors[0]['engine']} {errors[0]['doc']}: {errors[0]['error']}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_make = sub.add_parser("make")
    p_make.add_argument("--out", type=Path, required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--pages", type=Path, required=True)
    p_run.add_argument("--tessdata", type=Path, required=True)
    p_run.add_argument("--out", type=Path, required=True)
    p_run.add_argument("--only", nargs="*", choices=["indra", "tesseract", "tesseract_eng"])
    p_sum = sub.add_parser("summary")
    p_sum.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    if args.cmd == "make":
        make(args.out)
    elif args.cmd == "run":
        run(args.pages, args.tessdata, args.out, args.only)
    else:
        for path in args.files:
            data = json.loads(path.read_text(encoding="utf-8"))
            print(summary(data["info"], data["results"]))


if __name__ == "__main__":
    main()
