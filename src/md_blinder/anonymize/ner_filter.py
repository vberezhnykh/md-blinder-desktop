"""Пост-фильтр родовых NER-матчей. Порт src/lib/anonymize/nerFilter.ts."""

from __future__ import annotations

import re
from dataclasses import replace

from .types import RawMatch

# Браузерная NER-модель часто ловит определяемые «ролевые» термины договора
# ("Director", "Client", "Service Provider", "disclosing Party", "registered
# office address") как PERSON/COMPANY/ADDRESS, хотя это не конфиденциальные
# данные, а обозначения ролей. Аналогично метка REG_NUMBER иногда ловит саму
# родовую фразу ("Company registration number", "VAT number") без каких-либо
# цифр. Отбрасываем такие матчи, требуя, чтобы в спане был хотя бы один
# настоящий (не-родовой) токен, — так реальные имена, компании (например
# "John Smith", "Acme Ltd", "Baker & Partners") и настоящие рег. номера
# (содержащие цифры) остаются.
# Прочие структурные матчи (EMAIL/IBAN/SWIFT/ACCOUNT/CARD/PHONE) проходят насквозь.

# Пробельные символы JS (\s и String.prototype.trim): набор отличается от
# Python \s/str.strip() (\ufeff есть только в JS, \x1c-\x1f и \x85 — только
# в Python), поэтому задаём его явно — как содержимое класса символов regex.
_WS_CHARS = r"\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_WS = f"[{_WS_CHARS}]"
_JS_TRIM_CHARS = (
    "\t\n\v\f\r \u00a0\u1680" + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)

DETERMINERS: frozenset[str] = frozenset({
    'the', 'a', 'an', 'such', 'any', 'each', 'every', 'either', 'both', 'this',
    'that', 'these', 'those', 'said', 'its', 'their', 'our', 'your', 'his',
    'her', 'other', 'another', 'same', 'sole', 'aforesaid', 'aforementioned',
    'relevant', 'respective', 'applicable',
})

ROLE_NOUNS: frozenset[str] = frozenset({
    'party', 'parties', 'client', 'clients', 'customer', 'customers',
    'supplier', 'suppliers', 'vendor', 'vendors', 'contractor', 'contractors',
    'subcontractor', 'subcontractors', 'employer', 'employers', 'employee',
    'employees', 'seller', 'sellers', 'buyer', 'buyers', 'purchaser',
    'purchasers', 'licensor', 'licensors', 'licensee', 'licensees', 'lessor',
    'lessee', 'landlord', 'tenant', 'tenants', 'guarantor', 'borrower',
    'lender', 'agent', 'agents', 'principal', 'manufacturer', 'manufacturers',
    'distributor', 'distributors', 'provider', 'providers', 'user', 'users',
    'recipient', 'recipients', 'discloser', 'disclosee', 'beneficiary',
    'affiliate', 'affiliates', 'representative', 'representatives', 'officer',
    'officers', 'director', 'directors', 'manager', 'managers', 'shareholder',
    'shareholders', 'member', 'members', 'partner', 'partners', 'signatory',
    'signatories', 'counterparty', 'company', 'corporation', 'entity',
    'entities', 'individual', 'person', 'persons', 'holder', 'holders',
    'owner', 'owners', 'operator', 'consumer', 'developer',
    # C2: role/defined terms GLiNER over-tags as PERSON/COMPANY on SEC contracts.
    'consultant', 'consultants', 'counsel', 'sponsor', 'sponsors', 'promoter',
    'promoters', 'endorser', 'endorsers', 'appointee', 'appointees', 'addressee',
    'addressees', 'spokesperson', 'staff', 'personnel', 'force', 'agency',
    'agencies', 'trustee', 'trustees', 'nominee', 'nominees', 'assignee',
    'assignor', 'franchisor', 'franchisee', 'payer', 'payee', 'obligor',
    'obligee', 'indemnitor', 'indemnitee', 'attorney', 'advisor', 'adviser',
    'auditor', 'accountant', 'broker', 'dealer', 'reseller', 'integrator',
    'contributor', 'contributors', 'participant', 'participants', 'collaborator',
    'collaborators', 'consignor', 'consignee', 'bailor', 'bailee',
    # standalone titles (also stripped when leading a name via LEADING_TITLE);
    # as bare spans they read as roles, not PII.
    'ceo', 'cfo', 'cto', 'coo', 'chairman', 'chairperson', 'chairwoman',
    'president', 'secretary', 'treasurer', 'mr', 'mrs', 'ms', 'dr', 'prof',
})

GENERIC_MODIFIERS: frozenset[str] = frozenset({
    'service', 'services', 'end', 'third', 'non', 'defaulting', 'disclosing',
    'receiving', 'violating', 'breaching', 'indemnifying', 'prevailing',
    'terminating', 'requesting', 'providing', 'purchasing', 'selling',
    'paying', 'performing', 'affected', 'data', 'sub', 'co', 'joint',
    'general', 'managing', 'executive', 'senior', 'junior', 'primary',
    'secondary', 'ultimate', 'immediate', 'main', 'registered', 'additional',
    'new', 'existing', 'relevant', 'respective', 'applicable', 'other',
    # C2: modifiers that combine with role nouns into defined-term FPs
    # ("Notifying Party", "Agency sales force", "Vice President").
    'notifying', 'appointing', 'endorsing', 'sponsoring', 'promoting', 'sales',
    'marketing', 'technical', 'financial', 'legal', 'commercial', 'operational',
    'administrative', 'corporate', 'vice', 'deputy', 'assistant', 'acting',
    'interim', 'regional', 'local', 'national', 'international', 'global',
    # причастия/прилагательные из юридических определений ("permitted
    # subcontractors", "Impacted Party", "natural person") — без них родовая
    # фраза не набирала allGeneric и ложняк проходил насквозь.
    'undersigned', 'signing',
    'permitted', 'impacted', 'natural', 'juridical', 'physical', 'authorized',
    'authorised', 'designated', 'approved', 'named', 'nominated', 'appointed',
    'potential', 'prospective', 'former', 'current', 'future', 'participating',
    'contracting', 'remaining', 'surviving', 'successor', 'underlying',
    'intended', 'eligible', 'qualified',
})

CONNECTORS: frozenset[str] = frozenset({'and', 'or', 'of', 'to', 'for', 'in', 'on', 'at', 'by', 'with'})

# Британский почтовый индекс (формат вида SW1A 1AA, EC1A 1BB). GLiNER иногда тегает
# индекс-хвост адреса как REG_NUMBER — это ложняк, отбрасываем.
UK_POSTCODE = re.compile(rf"^[A-Za-z]{{1,2}}[0-9][A-Za-z0-9]?{_WS}*[0-9][A-Za-z]{{2}}\Z")

# Дата целиком (dd.mm.yyyy / dd.mm.yy / yyyy-mm-dd / yyyy.mm.dd, разделители
# . / -, единообразные внутри одной даты — обратная ссылка на группу
# разделителя). В системе нет типа DATE (см. EntityType), значит дата не
# может быть валидной сущностью ни для одного из проверяемых типов —
# GLiNER иногда тегает её как REG_NUMBER или PERSON.
#
# День и месяц должны быть правдоподобны (день 1-31, месяц 1-12), иначе
# регномер вида "12.34.5678" (второй блок 34 — не день) ошибочно матчился
# как дата и терял recall. В day-first ветке порядок первых двух чисел не
# фиксирован (принимаем и европейский d.m, и американский m/d) — важно
# лишь, чтобы оба были в диапазоне 1-31 и хотя бы одно из них — в 1-12.
GENERIC_DATE = re.compile(
    rf"^{_WS}*(?:(?:(?:0?[1-9]|1[0-2])([./-])(?:0?[1-9]|[12][0-9]|3[01])\1[0-9]{{2,4}}"
    rf"|(?:0?[1-9]|[12][0-9]|3[01])([./-])(?:0?[1-9]|1[0-2])\2[0-9]{{2,4}})"
    rf"|[0-9]{{4}}([./-])(?:0?[1-9]|1[0-2])\3(?:0?[1-9]|[12][0-9]|3[01])){_WS}*\Z"
)

# Суффиксы юрлиц: PERSON-спан, содержащий такой токен, — это компания, ошибочно
# помеченная человеком (обычно OCR-склейка двух названий: "ACME LIMITED GLOBEX LLC").
# Компании ловятся отдельно, поэтому такой PERSON отбрасываем.
COMPANY_SUFFIXES: frozenset[str] = frozenset({
    'ltd', 'limited', 'llc', 'llp', 'ltda', 'inc', 'incorporated', 'plc',
    'gmbh', 'kft', 'corp', 'corporation',
})

# Ведущий титул/должность перед именем ("Director John Smith"). Требует
# разделитель после титула, поэтому срабатывает только когда дальше есть имя.
LEADING_TITLE = re.compile(
    r"^(?:(?:director|manager|mr|mrs|ms|dr|prof|ceo|cfo|cto|coo|chairman|chairperson|chairwoman"
    rf"|president|secretary|treasurer)\.?[{_WS_CHARS},]+)+",
    re.I | re.A,
)

GENERIC_WORDS: frozenset[str] = DETERMINERS | ROLE_NOUNS | GENERIC_MODIFIERS | CONNECTORS

GENERIC_ADDRESS_WORDS: frozenset[str] = frozenset({
    'registered', 'office', 'offices', 'address', 'addresses', 'principal',
    'place', 'business', 'head', 'correspondence', 'service', 'home',
    'postal', 'physical', 'mailing', 'billing', 'shipping', 'delivery',
    'main', 'seat', 'premises', 'location', 'locations', 'headquarters',
    'hq', 'of', 'and', 'the', 'its', 'their', 'our',
})

_GENERIC_REGNUM_LIST: tuple[str, ...] = (
    'company', 'companies', 'registration', 'registered', 'number', 'no', 'nos',
    'reg', 'vat', 'tax', 'id', 'identification', 'identifier', 'code', 'corporate',
    'corporation', 'incorporation', 'business', 'license', 'licence', 'permit',
    'certificate', 'ein', 'crn', 'tin', 'nif', 'cnpj', 'of', 'and', 'the', 'its',
    'their', 'our',
)
# Порядок важен: из списка собирается альтернатива LEADING_REGNUM_GENERIC.
GENERIC_REGNUM_WORDS: frozenset[str] = frozenset(_GENERIC_REGNUM_LIST)

# Ведущая родовая обёртка reg-номера ("registration number 193394577",
# "Company registration no. 193...", "VAT No. GB12"). NER часто включает её в
# спан — отрезаем её и оставляем сам номер, требуя разделитель после каждого
# слова (сработает только когда дальше идёт номер). Собрано из GENERIC_REGNUM_WORDS.
LEADING_REGNUM_GENERIC = re.compile(
    rf"^(?:(?:{'|'.join(dict.fromkeys(_GENERIC_REGNUM_LIST))})[{_WS_CHARS}.,:#/&-]+)+",
    re.I | re.A,
)

# Мультиязычная NER-модель так же уверенно (0.8-0.95) тегает русские
# ролевые/родовые термины договора ("Заказчик", "Исполнитель", "Сторона",
# "третьи лица", "порядковый номер", "адрес электронной почты") как
# PERSON/COMPANY/REG_NUMBER/ADDRESS — это обозначения ролей, а не PII.
# Русские слова склоняются ("Заказчик/Заказчика/Заказчиком"), поэтому
# точное сравнение недостаточно. Гибрид: длинные однозначные корни матчим
# по префиксу (word.startsWith(stem)), короткие/неоднозначные — точным
# списком форм (иначе префикс «лиц» ошибочно поймал бы «лицензия», поэтому
# «лицо» — списком форм). RU_REGNUM_STEMS/RU_ADDRESS_STEMS тоже матчатся по
# префиксу: часть записей там — намеренно урезанные корни («электронн»,
# «почтов»), нужные, чтобы поймать «электронной», «почтовый» и т.п.
#
# ВАЖНО (утечка PII, найдена код-ревью): префиксное матчение работает только
# для МНОГОСЛОВНЫХ спанов — там его подстраховывает allGeneric (реальное имя
# содержит хотя бы одно НЕ-родовое слово, значит allGeneric не станет true
# целиком по случайности). Для ОДНОСЛОВНОГО спана (words.length === 1) это
# не так: allGeneric и hasRoleNoun схлопываются в одну и ту же проверку
# одного слова, и префикс типа «работник» ловит фамилию «Работников», «эл»
# ловит город «Электросталь», «сторон» — фамилию «Сторонин», «гарант» — бренд
# «Гарант». Поэтому однословный спан дропаем ТОЛЬКО по ТОЧНОЙ форме (exact-
# хелперы ниже), без префикса; префиксные RU_*_STEMS остаются в деле только
# для многословных спанов.

RU_ROLE_STEMS: tuple[str, ...] = (
    'заказчик', 'исполнител', 'арендатор', 'субарендатор', 'арендодател',
    'поставщик', 'покупател', 'продавец', 'продавц', 'подрядчик',
    'субподрядчик', 'заёмщик', 'заемщик', 'кредитор', 'должник', 'поручител',
    'залогодател', 'залогодержател', 'посредник', 'принципал', 'доверител',
    'поверенн', 'представител', 'работник', 'сотрудник', 'соисполнител',
    'обладател', 'правообладател', 'пользовател', 'потребител', 'приобретател',
    'участник', 'учредител', 'акционер', 'руководител', 'директор', 'сторон',
    'гарант', 'контрагент',
)

# Точные формы ролевых слов — используются как есть для однословных спанов
# (без префикса, см. пояснение выше). Родительный падеж множественного числа
# на -ов/-ев («заказчиков», «работников», «директоров», «поставщиков» и т.п.)
# сюда НЕ включён: эти формы совпадают с русскими фамилиями от профессий
# («Работников», «Директоров») — включать их означало бы дропать реальные
# фамилии. По той же причине не включён «гарант» (бренд «Гарант»).
RU_ROLE_FORMS: frozenset[str] = frozenset({
    'лицо', 'лица', 'лицу', 'лицом', 'лице', 'лиц', 'лицам', 'лицами', 'лицах',
    'фио', 'общество', 'общества', 'обществу', 'обществом', 'обществе',
    'агент', 'агента', 'агенту', 'агентом', 'агенте', 'агенты', 'фирма',
    'фирмы', 'фирме', 'фирму', 'фирмой',
    'заказчик', 'заказчика', 'заказчику', 'заказчиком', 'заказчике', 'заказчики',
    'заказчикам', 'заказчиками', 'заказчиках',
    'исполнитель', 'исполнителя', 'исполнителю', 'исполнителем', 'исполнителе',
    'исполнители', 'исполнителям', 'исполнителями', 'исполнителях', 'исполнителей',
    'сторона', 'стороны', 'стороне', 'сторону', 'стороной', 'стороною', 'сторон',
    'сторонам', 'сторонами', 'сторонах',
    'арендатор', 'арендатора', 'арендатору', 'арендатором', 'арендаторе',
    'арендаторы', 'арендаторам', 'арендаторами', 'арендаторах',
    'субарендатор', 'субарендатора', 'субарендатору', 'субарендатором',
    'субарендаторе', 'субарендаторы', 'субарендаторам', 'субарендаторами',
    'субарендаторах',
    'арендодатель', 'арендодателя', 'арендодателю', 'арендодателем',
    'арендодателе', 'арендодатели', 'арендодателям', 'арендодателями',
    'арендодателях', 'арендодателей',
    'подрядчик', 'подрядчика', 'подрядчику', 'подрядчиком', 'подрядчике',
    'подрядчики', 'подрядчикам', 'подрядчиками', 'подрядчиках',
    'субподрядчик', 'субподрядчика', 'субподрядчику', 'субподрядчиком',
    'субподрядчике', 'субподрядчики', 'субподрядчикам', 'субподрядчиками',
    'субподрядчиках',
    'поставщик', 'поставщика', 'поставщику', 'поставщиком', 'поставщике',
    'поставщики', 'поставщикам', 'поставщиками', 'поставщиках',
    'покупатель', 'покупателя', 'покупателю', 'покупателем', 'покупателе',
    'покупатели', 'покупателям', 'покупателями', 'покупателях', 'покупателей',
    'продавец', 'продавца', 'продавцу', 'продавцом', 'продавце', 'продавцы',
    'продавцам', 'продавцами', 'продавцах',
    'представитель', 'представителя', 'представителю', 'представителем',
    'представителе', 'представители', 'представителям', 'представителями',
    'представителях', 'представителей',
    'работник', 'работника', 'работнику', 'работником', 'работнике',
    'работники', 'работникам', 'работниками', 'работниках',
    'сотрудник', 'сотрудника', 'сотруднику', 'сотрудником', 'сотруднике',
    'сотрудники', 'сотрудникам', 'сотрудниками', 'сотрудниках',
    'контрагент', 'контрагента', 'контрагенту', 'контрагентом', 'контрагенте',
    'контрагенты', 'контрагентам', 'контрагентами', 'контрагентах',
    'руководитель', 'руководителя', 'руководителю', 'руководителем',
    'руководителе', 'руководители', 'руководителям', 'руководителями',
    'руководителях', 'руководителей',

    # Украинские формы (буквы і/ї/є/ґ не входят в русский диапазон, но здесь
    # это неважно: набор используется как обычный Set строк, а не как regex —
    # добавляем те же самые точные формы для украинских ролевых терминов.
    # Родительный падеж мн.ч. на -ів/-ов сюда намеренно НЕ включён (см. пояснение
    # выше про RU_ROLE_STEMS) — он совпадает с украинскими фамилиями.
    'замовник', 'замовника', 'замовнику', 'замовником', 'замовники',
    'замовниками', 'замовниках',
    'виконавець', 'виконавця', 'виконавцю', 'виконавцем', 'виконавці',
    'виконавцями', 'виконавцях',
    'підрядник', 'підрядника', 'підряднику', 'підрядником', 'підрядники',
    'підрядниками', 'підрядниках',
    'субпідрядник', 'субпідрядника', 'субпідряднику', 'субпідрядником',
    'субпідрядники',
    'орендар', 'орендаря', 'орендарю', 'орендарем', 'орендарі', 'орендарями',
    'орендарях',
    'орендодавець', 'орендодавця', 'орендодавцю', 'орендодавцем', 'орендодавці',
    'постачальник', 'постачальника', 'постачальнику', 'постачальником',
    'постачальники',
    'покупець', 'покупця', 'покупцю', 'покупцем', 'покупці', 'покупцями',
    'продавець', 'продавця', 'продавцю', 'продавцем', 'продавці',
    'представник', 'представника', 'представнику', 'представником',
    'представники', 'представниками',
    'сторона', 'сторони', 'стороні', 'сторону', 'стороною', 'сторін',
    'сторонам', 'сторонами', 'сторонах',
    'особа', 'особи', 'особу', 'особою', 'особі', 'осіб', 'особам', 'особами',
    'особах',
    'товариство', 'товариства', 'товариству', 'товариством', 'товаристві',
    'підприємець', 'підприємця', 'підприємцю', 'підприємцем',
})

RU_MODIFIER_STEMS: tuple[str, ...] = (
    'уполномоченн', 'ответственн', 'виновн', 'аффилированн', 'должностн',
    'физическ', 'юридическ', 'генеральн', 'ограниченн', 'соответствующ',
    'указанн', 'настоящ', 'названн', 'получивш', 'передающ', 'раскрывающ',
    'нарушивш', 'направивш', 'обязанн', 'уведомляющ', 'порядков',
    'регистрационн', 'идентификационн', 'ответственност',
    # Украинские корни модификаторов (для многословных «фізична особа»,
    # «юридична особа», «уповноважений представник» и т.п.).
    'фізичн', 'юридичн', 'уповноважен', 'відповідальн', 'наступн', 'зазначен',
    'відповідн',
)

RU_MODIFIER_FORMS: frozenset[str] = frozenset({
    'третий', 'третье', 'третья', 'третьи', 'третьих', 'третьим', 'третьими',
    'третьему', 'третьего', 'данный', 'данного', 'данному', 'данным',
    'данном', 'иной', 'иного', 'иному', 'иным', 'ином', 'прочий', 'прочие',
    'прочих',
})

RU_DETERMINERS: frozenset[str] = frozenset({
    'настоящий', 'настоящего', 'настоящему', 'настоящим', 'настоящем',
    'указанный', 'соответствующий', 'такой', 'такого', 'каждый', 'любой',
    'любого', 'другой', 'другого', 'свой', 'своего', 'его', 'её', 'ее',
    'их', 'этот', 'этого', 'этом', 'тот', 'того', 'сам', 'самого',
})

RU_CONNECTORS: frozenset[str] = frozenset({
    'с', 'и', 'или', 'по', 'для', 'от', 'за', 'при', 'между', 'а', 'но',
})

# Уже точные (несокращённые) формы — прежде матчились по префиксу, хотя
# префикс им не был нужен. Оставлены Set'ом и используются через .has() и
# для много-, и для однословных спанов.
RU_REGNUM_EXACT: frozenset[str] = frozenset({
    'номер', 'номера', 'номеру', 'номером', 'номере', 'реестровый', 'реестровым',
})

# Точные формы адресных слов — безопасны и для однословных спанов.
RU_ADDRESS_EXACT: frozenset[str] = frozenset({
    'адрес', 'адреса', 'адресу', 'адресом', 'адресе', 'местонахождения',
    'местонахождение', 'местонахождении', 'нахождения', 'почта', 'почты',
    'почте', 'почту', 'почтой',
})

# Урезанные корни ("почтов" → «почтовый/почтового/…», "электронн" →
# «электронной/электронного/…") — намеренно неоднозначны на однословном
# спане (раньше сюда же входило «эл», ловившее города «Электросталь»/
# «Элиста»/«Эльбрус» — реальная утечка PII), поэтому используются ТОЛЬКО
# для многословных спанов (см. RU_ROLE_STEMS выше и пояснение над ним).
RU_ADDRESS_STEMS: tuple[str, ...] = ('почтов', 'электронн')


def _starts_with_any(word: str, stems: tuple[str, ...]) -> bool:
    return any(word.startswith(stem) for stem in stems)


# --- Префиксные хелперы: только для МНОГОСЛОВНЫХ спанов ---

def _ru_is_role_noun(w: str) -> bool:
    return w in RU_ROLE_FORMS or _starts_with_any(w, RU_ROLE_STEMS)


def _ru_is_generic(w: str) -> bool:
    return (
        _ru_is_role_noun(w)
        or w in RU_MODIFIER_FORMS
        or w in RU_DETERMINERS
        or w in RU_CONNECTORS
        or _starts_with_any(w, RU_MODIFIER_STEMS)
    )


def _ru_is_regnum_generic(w: str) -> bool:
    return w in RU_REGNUM_EXACT or _ru_is_generic(w)


def _ru_is_address_generic(w: str) -> bool:
    return w in RU_ADDRESS_EXACT or _starts_with_any(w, RU_ADDRESS_STEMS) or _ru_is_generic(w)


# --- Exact-хелперы: только для ОДНОСЛОВНЫХ спанов (без префикса) ---

def _ru_is_generic_exact(w: str) -> bool:
    return (
        w in RU_ROLE_FORMS
        or w in RU_MODIFIER_FORMS
        or w in RU_DETERMINERS
        or w in RU_CONNECTORS
        or w in RU_ADDRESS_EXACT
        or w in RU_REGNUM_EXACT
    )


def _ru_is_role_noun_exact(w: str) -> bool:
    return w in RU_ROLE_FORMS


def _ru_is_address_generic_exact(w: str) -> bool:
    return w in RU_ADDRESS_EXACT or _ru_is_generic_exact(w)


def _ru_is_regnum_generic_exact(w: str) -> bool:
    return w in RU_REGNUM_EXACT or _ru_is_generic_exact(w)


_SPLIT_RE = re.compile(rf"[{_WS_CHARS}\-/&,.]+")
_DIGIT_RE = re.compile(r"[0-9]")


def _split_words(value: str) -> list[str]:
    return [w for w in _SPLIT_RE.split(value.strip(_JS_TRIM_CHARS).lower()) if w]


def _strip_leading_determiners(words: list[str]) -> list[str]:
    i = 0
    while i < len(words) and words[i] in DETERMINERS:
        i += 1
    return words[i:]


def filter_generic_ner_matches(matches: list[RawMatch]) -> list[RawMatch]:
    out: list[RawMatch] = []
    for m in matches:
        if m.type not in ("PERSON", "COMPANY", "ADDRESS", "REG_NUMBER"):
            out.append(m)
            continue

        # Индекс, ошибочно помеченный REG_NUMBER (формат "SW1A 1AA"). Проверяем ДО
        # раннего пропуска «в спане есть цифра» — в индексе цифры есть.
        if m.type == "REG_NUMBER" and UK_POSTCODE.search(m.value.strip(_JS_TRIM_CHARS)):
            continue

        # Дата, ошибочно помеченная любым из проверяемых типов ("05.06.2026" как
        # REG_NUMBER, "5/6/26" как PERSON). Тоже ДО шортката на цифры.
        if GENERIC_DATE.search(m.value):
            continue

        # Обрезаем ведущий титул у PERSON ("Director John Smith" → "John Smith"),
        # сдвигая start на длину совпадения, чтобы позиции остались валидными.
        if m.type == "PERSON":
            t = LEADING_TITLE.match(m.value)
            if t and len(t.group(0)) < len(m.value):
                cut = len(t.group(0))
                m = replace(m, value=m.value[cut:], start=m.start + cut)

        # Обрезаем ведущую родовую обёртку у REG_NUMBER ("registration number
        # 193394577" → "193394577"). ДО шортката на цифры — иначе обёртка ушла бы
        # в значение целиком.
        if m.type == "REG_NUMBER":
            t = LEADING_REGNUM_GENERIC.match(m.value)
            if t and len(t.group(0)) < len(m.value):
                cut = len(t.group(0))
                m = replace(m, value=m.value[cut:], start=m.start + cut)

        words = _strip_leading_determiners(_split_words(m.value))
        if not words:
            continue

        if _DIGIT_RE.search(m.value):
            out.append(m)
            continue

        # Однословный спан дропаем только по ТОЧНОЙ форме (без префикса): на одном
        # слове защита allGeneric исчезает, и префикс «работник»/«гарант» ловил бы
        # реальные фамилии/бренды/города (см. пояснение над RU_ROLE_STEMS).
        single = len(words) == 1

        if m.type in ("PERSON", "COMPANY"):
            # PERSON со суффиксом юрлица — это компания, ошибочно помеченная человеком.
            if m.type == "PERSON" and any(w in COMPANY_SUFFIXES for w in words):
                continue
            all_generic = all(w in GENERIC_WORDS for w in words)
            has_role_noun = any(w in ROLE_NOUNS for w in words)
            if all_generic and has_role_noun:
                continue
            # Русская ветка (параллельно английской): те же критерии по русскому
            # словарю с учётом склонений.
            is_generic = _ru_is_generic_exact if single else _ru_is_generic
            is_role = _ru_is_role_noun_exact if single else _ru_is_role_noun
            if all(is_generic(w) for w in words) and any(is_role(w) for w in words):
                continue
            out.append(m)
            continue

        if m.type == "REG_NUMBER":
            if all(w in GENERIC_REGNUM_WORDS for w in words):
                continue
            if (
                _ru_is_regnum_generic_exact(words[0])
                if single
                else all(_ru_is_regnum_generic(w) for w in words)
            ):
                continue
            out.append(m)
            continue

        # ADDRESS
        if all(w in GENERIC_ADDRESS_WORDS for w in words):
            continue
        if (
            _ru_is_address_generic_exact(words[0])
            if single
            else all(_ru_is_address_generic(w) for w in words)
        ):
            continue
        out.append(m)
    return out
