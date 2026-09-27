import json
from pathlib import Path

import pytest

from hunter.application.case_claims import load_case_claims
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.import_listings import import_listings
from hunter.application.ports import FetchResult
from hunter.application.sources import fetch_company_website
from hunter.domain.cases import ClaimStatus, presents_planned_as_done
from hunter.domain.entities import FetchStatus, SourceType
from hunter.domain.roles import Role
from hunter.infrastructure.knowledge import FileKnowledgeBase
from tests.helpers import add_user, make_deps
from tests.test_import import GOOD, TODAY

CLAIMS_FILE = Path(__file__).resolve().parents[1] / "data" / "case_claims.json"


@pytest.mark.parametrize(
    ("text", "violation"),
    [
        ("У нас есть готовый продукт — помощник претензионщика с Excel-отчётом.", True),
        ("Мы сделали сервис проверки должников.", True),
        (
            "Разрабатываем помощника для должников: сейчас готова оболочка бота, "
            "остальное в разработке.",
            False,
        ),
        ("Как у вас сейчас устроена претензионная работа с должниками?", False),
        ("Делали CRM для автосервисов.", False),
    ],
    ids=["ready-product", "made-service", "allowed-wording", "prospect-context", "unrelated"],
)
def test_planned_case_not_presented_as_done_t07(text, violation):
    assert presents_planned_as_done(text) is violation


def test_claims_registry_versions():
    deps = make_deps()
    admin = add_user(deps, "boss", Role.ADMIN)
    items = json.loads(CLAIMS_FILE.read_text(encoding="utf-8"))
    assert load_case_claims(deps, admin, items) == (4, 0)
    assert load_case_claims(deps, admin, items) == (0, 4)
    changed = [{**items[0], "status": "verified"}]
    assert load_case_claims(deps, admin, changed) == (1, 0)
    active = {c.case_key: c for c in deps.store.case_claims()}
    assert active["careermate"].id == "careermate:v2"
    assert active["careermate"].supports_problem_fit_20
    assert active["claims_assistant"].status is ClaimStatus.PLANNED
    assert len(deps.store.case_claims(active_only=False)) == 5


def test_only_admin_loads_claims():
    deps = make_deps()
    operator = add_user(deps, "op", Role.OPERATOR)
    with pytest.raises(HunterError) as exc:
        load_case_claims(deps, operator, [])
    assert exc.value.code is ErrorCode.FORBIDDEN


class FakeFetcher:
    def __init__(self, result: FetchResult):
        self.result = result
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return self.result


def _imported_lead(deps):
    operator = add_user(deps, "op", Role.OPERATOR)
    report = import_listings(
        deps,
        operator,
        [GOOD],
        file_name="f",
        campaign="c",
        platform="fl",
        currency="RUB",
        today=TODAY,
    )
    return operator, report.rows[0].lead_id


def test_failed_source_is_stored_with_reason_fr03():
    deps = make_deps()
    operator, lead_id = _imported_lead(deps)
    fetcher = FakeFetcher(
        FetchResult(
            url="x",
            status=FetchStatus.DENIED,
            text=None,
            content_hash=None,
            error_code="private_address",
        )
    )
    report = fetch_company_website(deps, operator, lead_id, fetcher)
    assert fetcher.urls == ["https://test-auto.example/"]
    assert report.status is FetchStatus.DENIED
    lead = deps.store.lead(lead_id)
    [website] = [
        s for s in deps.store.sources_for(lead.company_id) if s.source_type is SourceType.WEBSITE
    ]
    assert website.snapshot is None and website.error_code == "private_address"
    assert "website" not in {e.field for e in deps.store.evidence_for(lead.company_id)}


def test_fetched_source_adds_evidence_and_refetch_keeps_id():
    deps = make_deps()
    operator, lead_id = _imported_lead(deps)
    ok = FetchResult(
        url="x", status=FetchStatus.FETCHED, text="Два новых филиала", content_hash="h"
    )
    fetch_company_website(deps, operator, lead_id, FakeFetcher(ok))
    fetch_company_website(deps, operator, lead_id, FakeFetcher(ok))
    lead = deps.store.lead(lead_id)
    websites = [
        s for s in deps.store.sources_for(lead.company_id) if s.source_type is SourceType.WEBSITE
    ]
    assert len(websites) == 1 and websites[0].snapshot == "Два новых филиала"


def test_knowledge_base_by_prefix(tmp_path):
    (tmp_path / "01-company-and-cases.md").write_text("кейсы", encoding="utf-8")
    kb = FileKnowledgeBase(tmp_path)
    assert kb.documents(("01",)) == {"01-company-and-cases.md": "кейсы"}
    with pytest.raises(HunterError, match="03-"):
        kb.documents(("01", "03"))
