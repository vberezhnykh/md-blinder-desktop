"""Пост-фильтр родовых NER-матчей. Порт src/lib/anonymize/nerFilter.ts."""

from __future__ import annotations

import re
from dataclasses import replace

from .config import NER_CONFIRM_THRESHOLD
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
    # Роли и общие обозначения, которые модель выдавала за PERSON/COMPANY в
    # договорах (жалоба пользователя 2026-11 и прогон по корпусу). Множественное
    # число проверяется через _en_forms, отдельно его перечислять не нужно.
    'lawyer', 'banker', 'insurer', 'reinsurer', 'underwriter', 'sender',
    'receiver', 'successor', 'assign', 'transferee', 'transferor', 'executive',
    'claimant', 'investor', 'acquirer', 'advertiser', 'publisher', 'bidder',
    'engineer', 'visitor', 'guest', 'liquidator', 'administrator',
    'conservator', 'arbitrator', 'mediator', 'candidate', 'official',
    'professional', 'board', 'entrepreneur', 'intermediary', 'subscriber',
    'bankrupt', 'vp', 'svp', 'evp', 'firm', 'partnership', 'association',
    'trust', 'subsidiary', 'organization', 'organisation', 'institution',
    'courier', 'contact', 'name', 'exhibit', 'body', 'mail', 'royalty',
    # участники рынка связи и платформ (договор об SMS-рассылках, 2026-11)
    'carrier', 'aggregator', 'coordinator', 'reseller', 'integrator', 'network',
    'platform',
    # местоимения: «it», «she» модель изредка помечает как компанию или человека
    'it', 'he', 'she', 'they', 'them', 'we', 'us', 'you', 'him', 'itself',
    'himself', 'herself', 'themselves',
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
    # прилагательные из ложных находок 2026-11 («Upstream Providers», «indemnified
    # Party», «Chief Executive Officer», «registered U.S. Mail»)
    'professional', 'upstream', 'downstream', 'indemnified', 'warranting',
    'chief', 'hosting', 'external', 'internal', 'independent', 'certified',
    'public', 'licensed', 'duly', 'field', 'present', 'concerned', 'referenced',
    'reasonable', 'rightful', 'neither', 'one', 'personal', 'known', 'invalid',
    'reassigned', 'overnight', 'recognized', 'recognised', 'accounting',
    'unincorporated', 'limited', 'liability', 'voluntary', 'bankruptcy', 'fact',
    'target', 'application', 'u', 's',
    'cloud', 'mobile', 'digital', 'asset', 'sms', 'mms', 'telecom',
    'telecommunications', 'wireless', 'internet', 'online', 'messaging',
    'claiming', 'provided',
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
    # «wallet address», «Notice addresses», «e-mail address», «addresses listed in
    # the Agreement»: обозначение адреса, а не сам адрес.
    'notice', 'notices', 'wallet', 'email', 'e', 'mail', 'ip', 'web', 'website',
    'url', 'return', 'forwarding', 'residential', 'residence', 'domicile',
    'invoice', 'invoicing', 'legal', 'actual', 'territory', 'territories',
    'given', 'stated', 'listed', 'appearing', 'herein', 'hereto', 'hereof',
    'set', 'forth', 'above', 'below', 'beginning', 'agreement', 'contract',
    'this', 'in', 'at', 'for', 'to', 'by', 'on', 'such', 'each', 'respective',
    'party', 'parties',
})

# Голова родовой адресной фразы: спан без цифр, где есть это слово, а остальные
# слова родовые или написаны со строчной буквы, — обозначение адреса.
ADDRESS_HEADS: frozenset[str] = frozenset({'address', 'addresses'})

# «a Nevada corporation», «Delaware limited liability company»: штат и правовая
# форма — описание формы, а не название. Слово «trust» сюда не входит:
# «Delaware Trust Company» — настоящий банк.
US_STATE_WORDS: frozenset[str] = frozenset({
    'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
    'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
    'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana', 'maine',
    'maryland', 'massachusetts', 'michigan', 'minnesota', 'mississippi',
    'missouri', 'montana', 'nebraska', 'nevada', 'hampshire', 'jersey', 'mexico',
    'york', 'carolina', 'dakota', 'ohio', 'oklahoma', 'oregon', 'pennsylvania',
    'rhode', 'island', 'tennessee', 'texas', 'utah', 'vermont', 'virginia',
    'washington', 'west', 'wisconsin', 'wyoming', 'north', 'south', 'new',
})
LEGAL_FORM_WORDS: frozenset[str] = frozenset({
    'corporation', 'company', 'limited', 'liability', 'partnership', 'general',
    'nonprofit', 'non', 'profit', 'statutory', 'professional', 'public', 'benefit',
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
    'гарант', 'контрагент', 'персонал', 'посетител', 'субисполнител',
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
    'регистрационн', 'идентификационн', 'ответственност', 'получающ',
    'контактн', 'информаци',
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
    'первый', 'первая', 'первой', 'первого', 'первому', 'первым', 'первом',
    'второй', 'вторая', 'второго', 'второму', 'вторым', 'втором', 'вторую',
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
    # подписи реквизитов без самого значения
    'инн', 'кпп', 'огрн', 'огрнип', 'бик', 'окпо', 'єдрпоу', 'рнокпп', 'іпн',
})

# Точные формы адресных слов — безопасны и для однословных спанов.
RU_ADDRESS_EXACT: frozenset[str] = frozenset({
    'адрес', 'адреса', 'адресу', 'адресом', 'адресе', 'местонахождения',
    'местонахождение', 'местонахождении', 'нахождения', 'почта', 'почты',
    'почте', 'почту', 'почтой', 'адресов', 'адресам', 'адресами', 'адресах',
})

# Урезанные корни ("почтов" → «почтовый/почтового/…», "электронн" →
# «электронной/электронного/…") — намеренно неоднозначны на однословном
# спане (раньше сюда же входило «эл», ловившее города «Электросталь»/
# «Элиста»/«Эльбрус» — реальная утечка PII), поэтому используются ТОЛЬКО
# для многословных спанов (см. RU_ROLE_STEMS выше и пояснение над ним).
RU_ADDRESS_STEMS: tuple[str, ...] = ('почтов', 'электронн', 'помещени')


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


_SPLIT_RE = re.compile(rf"[{_WS_CHARS}\-/&,.]+")
_DIGIT_RE = re.compile(r"[0-9]")


def _split_words(value: str) -> list[str]:
    return [w.lower() for w in _split_words_cased(value)]


_POSSESSIVE_RE = re.compile(r"['’`]s?\Z", re.I)


def _split_words_cased(value: str) -> list[str]:
    """Слова спана без притяжательного окончания: «Affiliates’» → «Affiliates»."""
    words = (_POSSESSIVE_RE.sub("", w) for w in _SPLIT_RE.split(value.strip(_JS_TRIM_CHARS)))
    return [w for w in words if w]


# Частицы фамилий, которые пишутся со строчной: «Ludwig van Beethoven», «da Silva».
NAME_PARTICLES: frozenset[str] = frozenset({
    'van', 'von', 'der', 'den', 'de', 'del', 'della', 'di', 'da', 'das', 'dos',
    'du', 'la', 'le', 'ten', 'ter', 'bin', 'ibn', 'al', 'el', 'y', 'e', 'zu',
})

# Ссылка на раздел договора: «Section 15», «clause 3.2», «Schedule A», «пункт 5.1».
CROSS_REFERENCE = re.compile(
    r"^(?:section|sections|clause|clauses|article|articles|schedule|annex|appendix|exhibit|"
    r"paragraph|para|item|part|chapter|пункт|пункта|пункте|п|раздел|раздела|статья|статьи|"
    r"приложение|приложения|розділ|стаття|додаток)\.?\s*(?:№\s*)?[0-9A-Za-zА-Яа-я]{1,3}(?:[.)(][0-9a-z]{1,3})*\.?\)?$",
    re.I,
)


_WORD_RE = re.compile(rf"[^{_WS_CHARS}\-/&,.]+")


def _is_lower_word(w: str) -> bool:
    w = _POSSESSIVE_RE.sub("", w)
    return any(ch.isalpha() for ch in w) and not _has_upper(w) and w.lower() not in NAME_PARTICLES


def _trim_lowercase_edges(m: RawMatch) -> "RawMatch | None":
    """Отрезает строчные слова (кроме частиц фамилий) с обоих краев спана.
    None — заглавных слов нет совсем; тот же объект — резать нечего."""
    tokens = [t for t in _WORD_RE.finditer(m.value) if any(ch.isalpha() for ch in t.group(0))]
    keep = [t for t in tokens if not _is_lower_word(t.group(0))]
    if not keep:
        return None
    first, last = keep[0], keep[-1]
    if first is tokens[0] and last is tokens[-1]:
        return m
    # «operators of the Call Center», «сотрудников подрядных организаций …»: перед
    # заглавной частью стоит строчная фраза со связкой — описание, а не имя.
    lead = [t.group(0).lower() for t in tokens[: tokens.index(first)]]
    if any(w in CONNECTORS or w in RU_CONNECTORS for w in lead):
        return None
    start, end = first.start(), last.end()
    return replace(m, value=m.value[start:end], start=m.start + start, end=m.start + end)


def _has_upper(value: str) -> bool:
    return any(ch.isupper() for ch in value)


def _en_forms(w: str) -> tuple[str, ...]:
    """Слово и его возможные формы единственного числа: «advisers» → «adviser»,
    «subsidiaries» → «subsidiary». Словари ролей хранят единственное число."""
    forms = [w]
    if len(w) > 3 and w.endswith("ies"):
        forms.append(w[:-3] + "y")
    if len(w) > 3 and w.endswith("es"):
        forms.append(w[:-2])
    if len(w) > 2 and w.endswith("s"):
        forms.append(w[:-1])
    return tuple(forms)


def _en_in(w: str, vocab: frozenset[str]) -> bool:
    return any(f in vocab for f in _en_forms(w))


def is_generic_word(w: str) -> bool:
    """Родовое слово договора (роль, определитель, связка) на английском, русском
    или украинском — по точной форме, без префиксов. Нужен поиску похожих
    вхождений (related.py): такие слова не ищутся как части имени или названия."""
    w = w.lower()
    return _en_in(w, GENERIC_WORDS) or _ru_is_generic_exact(w)


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

        if CROSS_REFERENCE.match(m.value.strip(_JS_TRIM_CHARS)):
            continue

        # Имя человека пишется с заглавной буквы. Спан PERSON совсем без заглавных
        # («lawyers», «sender», «minors», «посетителя», «контактное лицо») — роль
        # или общее слово, а не имя.
        if m.type == "PERSON" and not _has_upper(m.value):
            continue

        # Имя и название начинаются и заканчиваются словом с заглавной. Строчное
        # слово с краю («claiming Party», «SMS recipient», «Digital Asset network»,
        # «Affiliates’ employees») — описание. Для человека это верно всегда, для
        # компании только на неуверенных находках: бывают названия строчными.
        # Строчные слова с краев отрезаются («John Smith, director» → «John Smith»);
        # если в остатке только родовые слова или ничего, находка отбрасывается.
        if m.type == "PERSON" or (
            m.type == "COMPANY" and m.score is not None and m.score < NER_CONFIRM_THRESHOLD
        ):
            trimmed = _trim_lowercase_edges(m)
            if trimmed is None:
                continue
            if trimmed is not m:
                rest = _split_words(trimmed.value)
                if all(_en_in(w, GENERIC_WORDS) or _ru_is_generic_exact(w) for w in rest):
                    continue
                m = trimmed

        if _DIGIT_RE.search(m.value):
            out.append(m)
            continue

        # Регистрационный номер без единой цифры — родовая фраза («part number»,
        # «Telecopy No.», «Licensee»), настоящий номер всегда содержит цифры.
        if m.type == "REG_NUMBER":
            continue

        # Однословный спан дропаем только по ТОЧНОЙ форме (без префикса): на одном
        # слове защита allGeneric исчезает, и префикс «работник»/«гарант» ловил бы
        # реальные фамилии/бренды/города (см. пояснение над RU_ROLE_STEMS).
        single = len(words) == 1

        if m.type in ("PERSON", "COMPANY"):
            # PERSON со суффиксом юрлица — это компания, ошибочно помеченная человеком.
            if m.type == "PERSON" and any(w in COMPANY_SUFFIXES for w in words):
                continue
            # Неуверенная «компания» без заглавных букв («courier service»,
            # «companies») — общее слово. Уверенную не трогаем: бывают названия
            # строчными буквами.
            if (
                m.type == "COMPANY" and not _has_upper(m.value)
                and m.score is not None and m.score < NER_CONFIRM_THRESHOLD
            ):
                continue
            if (
                m.type == "COMPANY" and any(w in US_STATE_WORDS for w in words)
                and any(w in LEGAL_FORM_WORDS for w in words)
                and all(w in US_STATE_WORDS or w in LEGAL_FORM_WORDS for w in words)
            ):
                continue
            # Все слова родовые (английские, русские или украинские, в том числе
            # вперемешку: «The Customer / Замовник»), и среди них есть роль.
            ru_generic = _ru_is_generic_exact if single else _ru_is_generic
            ru_role = _ru_is_role_noun_exact if single else _ru_is_role_noun
            if (
                all(_en_in(w, GENERIC_WORDS) or ru_generic(w) for w in words)
                and any(_en_in(w, ROLE_NOUNS) or ru_role(w) for w in words)
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
        # Адрес без цифр и без заглавных букв («territory», «wallet address»).
        if not _has_upper(m.value):
            continue
        # «Notice addresses», «addresses listed in the Agreement», «адрес нахождения
        # Помещений»: есть слово «адрес», остальное — родовые слова или строчные.
        cased = _split_words_cased(m.value)
        if any(w.lower() in ADDRESS_HEADS or w.lower().startswith("адрес") for w in cased) and all(
            w.lower() in GENERIC_ADDRESS_WORDS or w.lower() in ADDRESS_HEADS or w.lower().startswith("адрес")
            or not _has_upper(w) or _ru_is_address_generic(w.lower())
            for w in cased
        ):
            continue
        out.append(m)
    return out
