import json

import httpx
import pytest

from application.research_completion import registered_domain_resolver
from application.source_facts import SearchConfig
from governance.aep_executors import BraveConnectorError, BraveSearchExecutor
from governance.origin import host_matches_allowed_domain


def _run_executor(results):
    executor = BraveSearchExecutor(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"web": {"results": results}})),
        api_key_getter=lambda: "test-key",
    )
    resource = json.dumps({"provider_ref": "brave.search.v1", "allowed_domains": ["nasa.gov", "esa.int"]})
    args = (("request", json.dumps({
        "requested_domains": ["nasa.gov", "esa.int"], "query": "mission launch", "result_limit": 1,
    })),)
    return executor, resource, args


@pytest.mark.parametrize("host,domain", [
    ("nasa.gov", "nasa.gov"), ("www.nasa.gov", "nasa.gov"), ("science.nasa.gov", "nasa.gov"),
    ("esa.int", "esa.int"), ("www.esa.int", "esa.int"), ("exploration.esa.int", "esa.int"),
])
def test_domain_scope_accepts_exact_and_boundary_subdomains(host, domain):
    assert host_matches_allowed_domain(host, domain)


@pytest.mark.parametrize("host,domain", [
    ("evilnasa.gov", "nasa.gov"), ("nasa.gov.evil.com", "nasa.gov"),
    ("notnasa.gov", "nasa.gov"), ("evil-esa.int", "esa.int"),
])
def test_domain_scope_rejects_prefix_suffix_and_substring_attacks(host, domain):
    assert not host_matches_allowed_domain(host, domain)


def test_publisher_resolver_and_search_scope_share_boundary_semantics():
    resolver = registered_domain_resolver({"nasa.gov": "NASA", "esa.int": "ESA"})
    assert resolver("https://www.nasa.gov/mission") == "NASA"
    assert resolver("https://exploration.esa.int/rosetta") == "ESA"
    assert resolver("https://nasa.gov.evil.com/") is None


def test_brave_accepts_www_subdomain_and_emits_sanitized_filter_diagnostic():
    executor, resource, args = _run_executor([
        {"title": "NASA", "url": "https://www.nasa.gov/mission/artemis-i"},
        {"title": "outside", "url": "https://evil.example/result"},
    ])
    result = executor.run(resource, args, None)
    assert result.content["results"][0]["url"].startswith("https://www.nasa.gov/")
    assert executor.last_domain_filter == {
        "expected_domains": ["esa.int", "nasa.gov"],
        "returned_hosts": ["www.nasa.gov", "evil.example"],
        "accepted_hosts": ["www.nasa.gov"],
        "rejected_hosts": ["evil.example"],
        "result_count": 2,
        "in_scope_count": 1,
    }


def test_out_of_scope_only_results_still_fail_with_filter_diagnostic():
    executor, resource, args = _run_executor([
        {"title": "prefix", "url": "https://evilnasa.gov/result"},
        {"title": "suffix", "url": "https://nasa.gov.evil.com/result"},
    ])
    with pytest.raises(BraveConnectorError, match="result_domain_out_of_scope"):
        executor.run(resource, args, None)
    assert executor.last_domain_filter["expected_domains"] == ["esa.int", "nasa.gov"]
    assert executor.last_domain_filter["returned_hosts"] == ["evilnasa.gov", "nasa.gov.evil.com"]
    assert executor.last_domain_filter["accepted_hosts"] == []
    assert executor.last_domain_filter["in_scope_count"] == 0


def test_v16_search_contract_keeps_budget_and_result_limit():
    config = SearchConfig(("esa.int", "nasa.gov"), result_limit=1, max_searches=4)
    assert config.allowed_domains == ("esa.int", "nasa.gov")
    assert config.result_limit == 1 and config.max_searches == 4
