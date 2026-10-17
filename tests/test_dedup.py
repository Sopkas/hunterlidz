"""S1-09: canonical URLs, company keys and duplicate matching (T-01, T-02)."""

import pytest

from hunter.domain.catalog import Stage
from hunter.domain.dedup import (
    DuplicateDomainError,
    MatchOutcome,
    UrlError,
    active_lead,
    canonical_url,
    company_domain,
    match_company,
    normalize_company_name,
)
from hunter.domain.entities import Company, Lead

# --- canonical URL ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "https://example.com/"),
        ("  https://Example.COM/  ", "https://example.com/"),
        ("http://www.example.com", "http://example.com/"),
        ("https://example.com.", "https://example.com/"),
        ("//example.com/a", "https://example.com/a"),
        ("https://example.com:443/a/", "https://example.com/a"),
        ("http://example.com:80", "http://example.com/"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("https://user:secret@example.com/a", "https://example.com/a"),
        ("https://example.com/a#contacts", "https://example.com/a"),
        ("https://example.com/About", "https://example.com/About"),
        ("https://пример.рф/о-нас", "https://xn--e1afmkfd.xn--p1ai/%D0%BE-%D0%BD%D0%B0%D1%81"),
        ("https://xn--e1afmkfd.xn--p1ai/%d0%be-%d0%bd%d0%b0%d1%81",
         "https://xn--e1afmkfd.xn--p1ai/%D0%BE-%D0%BD%D0%B0%D1%81"),
        ("https://[2001:DB8::1]:8080/a", "https://[2001:db8::1]:8080/a"),
        ("https://192.0.2.1/a", "https://192.0.2.1/a"),
        ("https://www.com", "https://www.com/"),
        ("https://example.com/%7euser/%2f", "https://example.com/~user/%2F"),
        ("https://example.com/?q=црм+авто", "https://example.com/?q=%D1%86%D1%80%D0%BC+%D0%B0%D0%B2%D1%82%D0%BE"),
        ("https://пример。рф", "https://xn--e1afmkfd.xn--p1ai/"),
        ("https://a.ru/?a=1&&b=2&", "https://a.ru/?a=1&b=2"),
        ("https://a.ru/?x=%26", "https://a.ru/?x=%26"),
    ],
    ids=[
        "no-scheme", "case-and-spaces", "www", "trailing-dot", "scheme-relative", "default-https",
        "default-http", "own-port", "user-info", "fragment", "path-case", "idn", "idn-escaped",
        "ipv6", "ipv4", "www-is-the-domain", "unreserved-escapes", "query-non-ascii",
        "ideographic-dot", "empty-pairs", "escaped-ampersand",
    ],
)  # fmt: skip
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


def test_significant_query_is_kept_review_1_probe():
    # Review 1: the old key dropped the whole query, task.php?id=1 and ?id=2 became one source.
    one = canonical_url("https://example.com/task.php?id=1")
    two = canonical_url("https://example.com/task.php?id=2")
    assert one == "https://example.com/task.php?id=1"
    assert one != two


@pytest.mark.parametrize(
    "query",
    [
        "utm_source=yandex&utm_medium=cpc",
        "UTM_Campaign=x",
        "yclid=123",
        "ysclid=abc",
        "gclid=1&fbclid=2",
        "_openstat=x",
        "roistat_visit=5",
        "_ga=1.2&_gl=3",
    ],
)
def test_tracking_parameters_are_dropped(query):
    assert canonical_url(f"https://example.com/a?{query}") == "https://example.com/a"


def test_tracking_dropped_and_rest_sorted():
    url = canonical_url("https://example.com/s?q=crm&utm_source=tg&page=2&q=auto")
    assert url == "https://example.com/s?page=2&q=crm&q=auto"


@pytest.mark.parametrize("ambiguous", ["ref=main", "from=catalog", "source=partner"])
def test_ambiguous_parameters_stay(ambiguous):
    assert canonical_url(f"https://example.com/a?{ambiguous}").endswith(f"?{ambiguous}")


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("mailto:ceo@example.com", "scheme"),
        ("javascript:alert(1)", "scheme"),
        ("ftp://example.com", "scheme"),
        ("tel:+79990000000", "scheme"),
        ("https://", "host"),
        ("https://exa mple.com", "host"),
        ("https://-bad-.com", "host"),
        ("https://example.com:99999", "port"),
        ("https://example.com:http", "port"),
        ("https://example.com:0/", "port"),
        ("https://example.com\\@evil.com/", "host"),
        ("https://a..ru", "host"),
    ],
)
def test_bad_addresses_are_rejected(raw, code):
    with pytest.raises(UrlError) as error:
        canonical_url(raw)
    assert error.value.code == code


# --- company key ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "domain"),
    [
        ("https://www.example.com/contacts?utm_source=x", "example.com"),
        ("shop.example.com", "example.com"),
        ("https://m.example.co.uk/a", "example.co.uk"),
        ("Пример.РФ", "xn--e1afmkfd.xn--p1ai"),
        ("https://www.xn--e1afmkfd.xn--p1ai.", "xn--e1afmkfd.xn--p1ai"),
        ("https://shop.msk.ru", "shop.msk.ru"),
        ("https://sub.example.com.ru", "example.com.ru"),
        ("https://name.github.io/page", "name.github.io"),
        ("https://name.blogspot.com", "name.blogspot.com"),
        ("https://autoservice.tilda.ws/", "autoservice.tilda.ws"),
        ("https://www.autoservice.tilda.ws/", "autoservice.tilda.ws"),
        ("https://m.blog.ucoz.ru", "blog.ucoz.ru"),
    ],
    ids=[
        "www-path-query", "subdomain", "two-level-suffix", "idn", "punycode-www-dot",
        "private-suffix-msk", "com-ru", "github-pages", "blogspot", "tilda", "tilda-www",
        "ucoz-nested",
    ],
)  # fmt: skip
def test_company_domain_is_registrable_domain(raw, domain):
    assert company_domain(raw) == domain


def test_one_domain_in_every_spelling():
    # Review of PR #4: one company must not get two keys.
    spellings = [
        "пример.рф",
        "ПРИМЕР.РФ",
        "www.пример.рф",
        "xn--e1afmkfd.xn--p1ai",
        "https://www.xn--e1afmkfd.xn--p1ai./about?utm_source=vk",
        "http://shop.пример.рф:80",
    ]
    assert {company_domain(s) for s in spellings} == {"xn--e1afmkfd.xn--p1ai"}


def test_last_two_labels_are_not_the_key():
    # Doc 03: two clients of a two-level suffix are two companies.
    assert company_domain("alpha.co.uk") != company_domain("beta.co.uk")
    assert company_domain("alpha.msk.ru") != company_domain("beta.msk.ru")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "https://vk.com/autoservice_spb",
        "https://m.vk.com/club123",
        "https://t.me/autoservice",
        "https://hh.ru/employer/123",
        "https://www.avito.ru/brands/x",
        "https://2gis.ru/spb/firm/1",
        "https://www.rusprofile.ru/id/1",
        "https://tilda.ws",
        "https://192.0.2.1/",
        "https://[2001:db8::1]/",
        "https://localhost:8080/",
        "https://co.uk",
        "https://example.notatld",
    ],
    ids=[
        "none", "empty", "vk", "vk-mobile", "telegram", "hh", "avito", "2gis", "registry",
        "builder-itself", "ipv4", "ipv6", "localhost", "public-suffix", "unknown-tld",
    ],
)  # fmt: skip
def test_no_company_key(raw):
    assert company_domain(raw) is None


def test_two_vk_pages_are_not_one_company():
    assert company_domain("https://vk.com/alpha") is None
    assert company_domain("https://vk.com/beta") is None


def test_company_domain_rejects_non_http():
    with pytest.raises(UrlError):
        company_domain("mailto:ceo@example.com")


def test_company_domain_works_offline(monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("public suffix lookup must not use the network")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    assert company_domain("https://shop.example.co.uk") == "example.co.uk"


# --- names ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("ООО «Ромашка»", "ромашка"),
        ('Общество с ограниченной ответственностью "Ромашка"', "ромашка"),
        ("ромашка, ООО", "ромашка"),
        ("Ёлка Сервис", "елка сервис"),
        ("Romashka LLC", "romashka"),
        ("ИП Иванов", "иванов"),
        ("ООО", ""),
    ],
)
def test_normalize_company_name(name, key):
    assert normalize_company_name(name) == key


# --- matching (T-01, T-02) --------------------------------------------------------------------


def _company(cid, name, domain=None, merged_into=None):
    return Company(id=cid, name=name, normalized_domain=domain, merged_into_id=merged_into)


def test_t01_reimport_matches_by_domain():
    existing = [_company("c1", "Автосервис Север", "sever-auto.ru")]
    domain = company_domain("https://www.sever-auto.ru/contacts?utm_source=import")
    result = match_company(name="ООО «Север-Авто»", domain=domain, companies=existing)
    assert result.outcome is MatchOutcome.MATCHED
    assert result.company_id == "c1"
    assert not result.needs_review


def test_t01_merged_record_resolves_to_target():
    existing = [
        _company("old", "Север", "sever-auto.ru", merged_into="mid"),
        _company("mid", "Север", None, merged_into="main"),
        _company("main", "Север Авто", None),
    ]
    result = match_company(name="Север", domain="sever-auto.ru", companies=existing)
    assert result == match_company(name="x", domain="sever-auto.ru", companies=existing)
    assert result.company_id == "main"


def test_t02_same_name_other_domain_is_new_and_flagged():
    existing = [_company("c1", "ООО «Ромашка»", "romashka.ru")]
    result = match_company(name="Ромашка", domain="romashka-spb.ru", companies=existing)
    assert result.outcome is MatchOutcome.NEW
    assert result.company_id is None
    assert result.similar_ids == ("c1",)
    assert result.needs_review


def test_t02_no_domain_never_merges_by_name():
    existing = [_company("c1", "Ромашка", None), _company("c2", "ромашка", "romashka.ru")]
    result = match_company(name="РОМАШКА", domain=None, companies=existing)
    assert result.outcome is MatchOutcome.NEW
    assert result.similar_ids == ("c1", "c2")


def test_t02_similar_but_different_names_are_not_flagged():
    existing = [_company("c1", "Ромашка Сервис", "a.ru"), _company("c2", "Ромашка", "b.ru")]
    result = match_company(name="Ромашка Плюс", domain=None, companies=existing)
    assert result.similar_ids == ()


def test_merged_companies_are_not_flagged():
    existing = [_company("c1", "Ромашка", None, merged_into="c2"), _company("c2", "Другая", None)]
    assert match_company(name="Ромашка", domain=None, companies=existing).similar_ids == ()


def test_legal_form_only_name_is_not_flagged():
    existing = [_company("c1", "ООО", None)]
    assert match_company(name="ООО", domain=None, companies=existing).similar_ids == ()


def test_two_active_companies_with_one_domain_is_a_store_error():
    existing = [_company("c1", "A", "a.ru"), _company("c2", "B", "a.ru")]
    with pytest.raises(DuplicateDomainError):
        match_company(name="A", domain="a.ru", companies=existing)


@pytest.mark.parametrize(
    "existing",
    [
        [_company("c1", "A", "a.ru", merged_into="missing")],
        [
            _company("c1", "A", "a.ru", merged_into="c2"),
            _company("c2", "A", None, merged_into="c1"),
        ],
    ],
    ids=["missing-target", "cycle"],
)
def test_broken_merge_chain(existing):
    with pytest.raises(ValueError, match="merge chain"):
        match_company(name="A", domain="a.ru", companies=existing)


# --- one active lead per company + campaign ---------------------------------------------------


def _lead(lid, stage, company="c1", campaign="k1"):
    reason = "no budget" if stage is Stage.LOST else None
    return Lead(id=lid, company_id=company, campaign_id=campaign, stage=stage, loss_reason=reason)


def test_t01_active_lead_is_found():
    leads = [_lead("l1", Stage.LOST), _lead("l2", Stage.CONTACTED), _lead("l3", Stage.NEW, "c2")]
    assert active_lead(company_id="c1", campaign_id="k1", leads=leads).id == "l2"


def test_closed_leads_do_not_block_a_new_one():
    leads = [_lead("l1", Stage.LOST), _lead("l2", Stage.WON)]
    assert active_lead(company_id="c1", campaign_id="k1", leads=leads) is None


def test_other_campaign_is_another_opportunity():
    leads = [_lead("l1", Stage.NEW, campaign="k2")]
    assert active_lead(company_id="c1", campaign_id="k1", leads=leads) is None


def test_nurture_is_still_active():
    leads = [_lead("l1", Stage.NURTURE)]
    assert active_lead(company_id="c1", campaign_id="k1", leads=leads).id == "l1"


def test_two_active_leads_is_a_store_error():
    leads = [_lead("l1", Stage.NEW), _lead("l2", Stage.CONTACTED)]
    with pytest.raises(ValueError, match="several active leads"):
        active_lead(company_id="c1", campaign_id="k1", leads=leads)
