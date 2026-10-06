import httpx

from papertrail.core.validators.rxnorm import RxNormClient, strip_strength

ROUTES = {
    ("/REST/rxcui.json", "amoxicillin"): {"idGroup": {"rxnormId": ["723"]}},
    ("/REST/rxcui.json", "tylenol"): {"idGroup": {"rxnormId": ["202433"]}},
    ("/REST/rxcui.json", "amoxycilin"): {"idGroup": {}},
    ("/REST/rxcui/723/properties.json", None): {"properties": {"rxcui": "723", "name": "amoxicillin", "tty": "IN"}},
    ("/REST/rxcui/202433/properties.json", None): {"properties": {"rxcui": "202433", "name": "Tylenol", "tty": "BN"}},
    ("/REST/rxcui/202433/related.json", None): {
        "relatedGroup": {"conceptGroup": [{"tty": "IN", "conceptProperties": [{"name": "acetaminophen"}]}]}
    },
    ("/REST/approximateTerm.json", None): {
        "approximateGroup": {"candidate": [{"rxcui": "723", "name": "Amoxicillin", "score": "7"}]}
    },
}


def make_client(tmp_path=None, fail_times=0):
    calls = {"n": 0, "fails": fail_times}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["fails"] > 0:
            calls["fails"] -= 1
            return httpx.Response(503)
        key = (request.url.path, request.url.params.get("name"))
        body = ROUTES.get(key) or ROUTES.get((request.url.path, None))
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)

    http = httpx.Client(base_url="https://rxnav.nlm.nih.gov/REST", transport=httpx.MockTransport(handler))
    return RxNormClient(cache_dir=tmp_path, http=http, sleep=lambda _s: None), calls


def test_strip_strength():
    assert strip_strength("Tab. Amoxicillin 500 mg") == "amoxicillin"
    assert strip_strength("Dolo 650") == "dolo"
    assert strip_strength("Syp Cetirizine 5mg/5ml") == "cetirizine"


def test_generic_found():
    client, _ = make_client()
    r = client.lookup("Amoxicillin 500mg")
    assert r.found and r.rxcui == "723" and r.generic_names == ["amoxicillin"]


def test_brand_normalised_to_ingredient():
    client, _ = make_client()
    r = client.lookup("Tylenol 500")
    assert r.found and r.ingredients == ["acetaminophen"]


def test_misspelling_gets_suggestion():
    client, _ = make_client()
    r = client.lookup("Amoxycilin")
    assert not r.found and r.suggestion == "amoxicillin" and r.error is None


def test_retry_then_success_and_disk_cache(tmp_path):
    client, calls = make_client(tmp_path, fail_times=1)
    assert client.lookup("amoxicillin").found
    first_calls = calls["n"]
    fresh, calls2 = make_client(tmp_path)
    assert fresh.lookup("Amoxicillin").found and calls2["n"] == 0  # served from disk
    assert first_calls >= 3  # one 503 retried, then rxcui + properties


def test_outage_reports_error_not_not_found():
    client, _ = make_client(fail_times=99)
    r = client.lookup("amoxicillin")
    assert not r.found and r.error and r.error.startswith("rxnorm_unavailable")
