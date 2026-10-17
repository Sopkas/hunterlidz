"""S1-13: one spelling per contact address, so duplicates and suppressions are found (T-15)."""

import pytest

from hunter.domain.contacts import (
    Address,
    AddressError,
    classify_address,
    is_company_wide,
    normalize_address,
    normalize_email,
    normalize_phone,
    normalize_telegram,
    suppression_key,
)
from hunter.domain.entities import Channel

# --- e-mail -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "ivan.petrov@example.com",
        "Ivan.Petrov@Example.COM",
        "  ivan.petrov@example.com.  ",
        "mailto:ivan.petrov@example.com",
        "mailto:Ivan.Petrov@example.com?subject=Hi",
        "Иван Петров <ivan.petrov@example.com>",
    ],
)
def test_email_spellings_give_one_address(raw):
    assert normalize_email(raw) == "ivan.petrov@example.com"


def test_email_idn_domain_becomes_punycode():
    assert normalize_email("Ivan@Пример.РФ") == "ivan@xn--e1afmkfd.xn--p1ai"
    assert normalize_email("ivan@xn--e1afmkfd.xn--p1ai") == "ivan@xn--e1afmkfd.xn--p1ai"


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("ivan.petrov@example.com", "ivanpetrov@example.com"),
        ("ivan+crm@example.com", "ivan@example.com"),
        ("ivan@example.com", "ivan@example.ru"),
    ],
    ids=["dots", "plus-tag", "domain"],
)
def test_different_mailboxes_are_not_merged(one, other):
    assert normalize_email(one) != normalize_email(other)


@pytest.mark.parametrize(
    "raw",
    ["ivan", "ivan@", "@example.com", "ivan@localhost", "iv an@example.com", "ivan@exa_mple.com",
     ".ivan@example.com", "ivan@-bad.com", "ivan..petrov@example.com", "ivan@@example.com"],
)  # fmt: skip
def test_bad_email_is_rejected(raw):
    with pytest.raises(AddressError) as error:
        normalize_email(raw)
    assert error.value.code == "email"


# --- phone ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+7 (495) 123-45-67",
        "8 495 123 45 67",
        "84951234567",
        "74951234567",
        "4951234567",
        "+7.495.123.45.67",
        "tel:+74951234567",
    ],
)
def test_russian_phone_spellings_give_one_address(raw):
    assert normalize_phone(raw) == "+74951234567"


def test_plus_89_is_a_russian_mobile_slip():
    assert normalize_phone("+8 999 123-45-67") == "+79991234567"
    assert normalize_phone("8 999 123-45-67") == "+79991234567"
    # +84… is Vietnam: with a plus, only 89 (no such country code) is treated as a slip.
    assert normalize_phone("+84 95 123-45-67") == "+84951234567"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+7 495 123-45-67 доб. 12", "+74951234567;ext=12"),
        ("8 (495) 123-45-67 доб 12", "+74951234567;ext=12"),
        ("8 (495) 123-45-67, доб. 12", "+74951234567;ext=12"),
        ("+74951234567 ext. 305", "+74951234567;ext=305"),
        ("+74951234567 x305", "+74951234567;ext=305"),
    ],
)
def test_extension_is_part_of_the_address(raw, expected):
    assert normalize_phone(raw) == expected


def test_two_extensions_are_two_contacts():
    assert normalize_phone("+74951234567 доб. 1") != normalize_phone("+74951234567 доб. 2")


def test_foreign_number_keeps_its_code():
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"
    assert normalize_phone("+375 (29) 123-45-67") == "+375291234567"


def test_numbers_sharing_the_last_digits_are_not_merged():
    assert normalize_phone("+7 495 123-45-67") != normalize_phone("+7 499 123-45-67")


@pytest.mark.parametrize(
    "raw",
    ["12345", "123-45-67", "+7 495 123", "+7 495 123-45-678", "+0 123 456 789",
     "звонить менеджеру", "+7 495 abc"],
    ids=["five-digits", "city-number", "short-7", "long-7", "zero-code", "text", "letters"],
)  # fmt: skip
def test_bad_phone_is_rejected(raw):
    with pytest.raises(AddressError) as error:
        normalize_phone(raw)
    assert error.value.code == "phone"


# --- Telegram ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "@Ivan_Petrov",
        "ivan_petrov",
        "t.me/ivan_petrov",
        "https://t.me/Ivan_Petrov",
        "https://telegram.me/ivan_petrov?start=hello",
        "http://www.t.me/ivan_petrov/",
        "tg://resolve?domain=Ivan_Petrov",
        "https://t.me/s/ivan_petrov",
        "https://t.me/ivan_petrov/123",
    ],
)
def test_telegram_spellings_give_one_address(raw):
    assert normalize_telegram(raw) == "@ivan_petrov"


def test_telegram_phone_is_kept_as_a_phone():
    assert normalize_telegram("+7 999 123-45-67") == "+79991234567"


@pytest.mark.parametrize(
    "raw",
    ["https://t.me/+AbCdEf123", "t.me/joinchat/AbCdEf", "https://t.me/c/1234567/89",
     "https://t.me/addstickers/pack"],
    ids=["plus-invite", "joinchat", "message-link", "stickers"],
)  # fmt: skip
def test_invite_and_service_links_are_not_contacts(raw):
    with pytest.raises(AddressError) as error:
        normalize_telegram(raw)
    assert error.value.code == "invite"


@pytest.mark.parametrize("raw", ["@ab", "@1ivan", "@ivan_", "@иван", "https://t.me/", "@iv an"])
def test_bad_telegram_username_is_rejected(raw):
    with pytest.raises(AddressError) as error:
        normalize_telegram(raw)
    assert error.value.code == "telegram"


# --- channels ---------------------------------------------------------------------------------


def test_web_channel_uses_the_canonical_url():
    assert (
        normalize_address(Channel.WEB, "https://www.Example.com/contacts/?utm_source=x")
        == "https://example.com/contacts"
    )


def test_web_channel_reports_url_errors_as_address_errors():
    with pytest.raises(AddressError) as error:
        normalize_address(Channel.WEB, "mailto:a@b.ru")
    assert error.value.code == "scheme"


def test_other_channel_is_only_trimmed():
    # Unknown format: lower-casing or stripping could merge two different addresses.
    assert normalize_address(Channel.OTHER, "  WhatsApp:  +7 999 ") == "WhatsApp: +7 999"


@pytest.mark.parametrize(
    "raw", ["", "   ", "нет", "Нет.", "-", "—", "n/a", "см. сайт", "Не указан", "null"]
)
@pytest.mark.parametrize("channel", list(Channel))
def test_placeholders_are_rejected_on_every_channel(raw, channel):
    with pytest.raises(AddressError) as error:
        normalize_address(channel, raw)
    assert error.value.code in {"empty", "placeholder"}


# --- import column: guess the channel ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "address"),
    [
        ("Ivan.Petrov@Example.com", Address(Channel.EMAIL, "ivan.petrov@example.com")),
        ("Иван <ivan@example.com>", Address(Channel.EMAIL, "ivan@example.com")),
        ("mailto:ivan@example.com", Address(Channel.EMAIL, "ivan@example.com")),
        ("@Ivan_Petrov", Address(Channel.TELEGRAM, "@ivan_petrov")),
        ("https://t.me/ivan_petrov", Address(Channel.TELEGRAM, "@ivan_petrov")),
        ("8 (495) 123-45-67", Address(Channel.PHONE, "+74951234567")),
        ("+7 495 123-45-67 доб. 12", Address(Channel.PHONE, "+74951234567;ext=12")),
        ("example.com/contacts", Address(Channel.WEB, "https://example.com/contacts")),
        ("WhatsApp Ивана", Address(Channel.OTHER, "WhatsApp Ивана")),
    ],
)
def test_classify_address(raw, address):
    assert classify_address(raw) == address


@pytest.mark.parametrize("raw", ["нет", "12345", "https://t.me/+invite", "12.10.2024"])
def test_classify_rejects_junk(raw):
    with pytest.raises(AddressError):
        classify_address(raw)


@pytest.mark.parametrize(
    "raw",
    ["ivan@example.com, petr@example.com", "info@example.com; sales@example.com",
     "+7 999 123-45-67 / +7 999 765-43-21", "@ivan_petrov или +7 999 123-45-67"],
)  # fmt: skip
def test_one_value_is_one_address(raw):
    with pytest.raises(AddressError) as error:
        classify_address(raw)
    assert error.value.code == "several"


def test_extension_after_a_comma_is_not_a_second_address():
    assert classify_address("8 (495) 123-45-67, доб. 12").normalized == "+74951234567;ext=12"


def test_whatsapp_link_is_a_personal_messenger_not_a_web_form():
    address = classify_address("https://wa.me/79991234567")
    assert address == Address(Channel.OTHER, "https://wa.me/79991234567")
    assert not is_company_wide(address)


def test_suppression_matches_every_spelling():
    # T-15: a ban recorded for one spelling must stop a message to any other spelling.
    banned = classify_address("Ivan.Petrov@Example.com")
    for spelling in ["mailto:ivan.petrov@example.com", "Иван <IVAN.PETROV@example.com>"]:
        assert classify_address(spelling) == banned


# --- doc 04 contactability --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "company_wide"),
    [
        (Address(Channel.EMAIL, "info@example.com"), True),
        (Address(Channel.EMAIL, "sales@example.com"), True),
        (Address(Channel.EMAIL, "ivan.petrov@example.com"), False),
        (Address(Channel.EMAIL, "information@example.com"), False),
        (Address(Channel.WEB, "https://example.com/contacts"), True),
        (Address(Channel.TELEGRAM, "@ivan_petrov"), False),
        (Address(Channel.PHONE, "+74951234567"), False),
    ],
)
def test_company_wide_channels(address, company_wide):
    assert is_company_wide(address) is company_wide


# --- review A on #6 ---------------------------------------------------------------------------

IDEMPOTENCE = [
    (Channel.EMAIL, "Иван Петров <Ivan.Petrov+crm@Пример.РФ>"),
    (Channel.EMAIL, "mailto:Ivan.Petrov@example.com?subject=Hi"),
    (Channel.PHONE, "8 (495) 123-45-67"),
    (Channel.PHONE, "+7 495 123-45-67 доб. 12"),
    (Channel.PHONE, "8 (495) 123-45-67, ext. 7"),
    (Channel.PHONE, "+44 20 7946 0958"),
    (Channel.TELEGRAM, "https://t.me/Ivan_Petrov"),
    (Channel.TELEGRAM, "t.me/+79161234567"),
    (Channel.WEB, "www.www.Example.com/contacts/?utm_source=x"),
    (Channel.OTHER, "  WhatsApp:  +7 999 "),
]


@pytest.mark.parametrize(("channel", "raw"), IDEMPOTENCE)
def test_normalizing_a_stored_address_gives_the_same_address(channel, raw):
    # The stored form is normalized again on re-import and when a ban is recorded (T-15).
    once = normalize_address(channel, raw)
    assert normalize_address(channel, once) == once


@pytest.mark.parametrize("raw", ["+74951234567;ext=12", "tel:+74951234567;ext=12"])
def test_stored_extension_form_is_accepted(raw):
    assert normalize_phone(raw) == "+74951234567;ext=12"
    assert classify_address(raw) == Address(Channel.PHONE, "+74951234567;ext=12")


@pytest.mark.parametrize("raw", ["80291234567", "+7 029 123-45-67", "+7 123 456-78-90"])
def test_unassigned_plus_7_codes_are_rejected(raw):
    with pytest.raises(AddressError) as error:
        normalize_phone(raw)
    assert error.value.code == "phone"


def test_kazakhstan_plus_7_numbers_stay():
    assert normalize_phone("+7 701 123 45 67") == "+77011234567"


def test_telegram_link_by_phone_is_a_phone():
    assert normalize_telegram("https://t.me/+79161234567") == "+79161234567"


@pytest.mark.parametrize(
    "raw", ["vk.com/ivan", "https://instagram.com/shop", "https://www.ozon.ru/seller/alpha"]
)
def test_page_on_a_shared_platform_is_not_a_company_web_form(raw):
    address = classify_address(raw)
    assert address.channel is Channel.OTHER
    assert not is_company_wide(address)


@pytest.mark.parametrize("raw", ["https://example.com/?a=1;b=2", "https://example.com/a,b"])
def test_one_url_with_separators_is_one_address(raw):
    assert classify_address(raw).channel is Channel.WEB


def test_two_urls_are_still_several():
    with pytest.raises(AddressError) as error:
        classify_address("https://a.ru, https://b.ru")
    assert error.value.code == "several"


def test_ban_on_a_number_covers_its_extensions():
    main = suppression_key(classify_address("+7 495 123-45-67"))
    for spelling in ["+7 495 123-45-67 доб. 12", "8 (495) 123-45-67, ext. 7"]:
        assert suppression_key(classify_address(spelling)) == main
    # Deduplication still keeps two people behind one switchboard apart.
    assert classify_address("+7 495 123-45-67 доб. 12") != classify_address("+7 495 123-45-67")


def test_ban_on_an_email_covers_tags_and_gmail_dots():
    banned = suppression_key(classify_address("ivan.petrov@gmail.com"))
    for spelling in ["IvanPetrov@gmail.com", "ivan.petrov+crm@googlemail.com"]:
        assert suppression_key(classify_address(spelling)) == banned
    corporate = suppression_key(classify_address("ivan.petrov+crm@example.com"))
    assert corporate == "ivan.petrov@example.com"  # dots matter on other servers
