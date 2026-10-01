from scripts.s3_request_report import parse_line, prefix_of, report, tier

LIST = (
    "79a5 checkyouraqi-242254325008 [01/Oct/2026:12:01:02 +0000] 1.2.3.4 arn:aws:iam::1:user/dev "
    'REQ1 REST.GET.BUCKET - "GET /?list-type=2&prefix=bronze%2Fopenmeteo%2Factuals%2Fdt%3D2025-01-01%2F HTTP/1.1" 200 - 500 - 12 11 "-" "agent" -'
)
PUT = (
    "79a5 checkyouraqi-242254325008 [01/Oct/2026:12:01:03 +0000] 1.2.3.4 arn:aws:iam::1:user/dev "
    'REQ2 REST.PUT.OBJECT lake/aqi_silver/t/data/x.parquet "PUT /lake/aqi_silver/t/data/x.parquet HTTP/1.1" 200 - - 100 20 10 "-" "agent" -'
)
GET = (
    "79a5 checkyouraqi-242254325008 [01/Oct/2026:12:01:04 +0000] 1.2.3.4 arn:aws:iam::1:user/dev "
    'REQ3 REST.GET.OBJECT bronze/firms/dt=2025-01-01/VIIRS_SNPP_SP.csv "GET /bronze/firms/dt=2025-01-01/VIIRS_SNPP_SP.csv HTTP/1.1" 200 - 900 900 5 4 "-" "agent" -'
)


def test_parse_and_tiers():
    e = parse_line(LIST)
    assert e["op"] == "REST.GET.BUCKET" and e["time"].hour == 12
    assert tier("REST.GET.BUCKET") == 1 and tier("REST.PUT.OBJECT") == 1
    assert tier("REST.GET.OBJECT") == 2 and tier("REST.HEAD.OBJECT") == 2
    assert prefix_of(e) == "bronze/openmeteo/actuals"
    assert prefix_of(parse_line(PUT)) == "lake/aqi_silver/t"


def test_report_prices_requests():
    r = report([parse_line(x) for x in (LIST, PUT, GET)])
    assert (r["tier1"], r["tier2"]) == (2, 1)
    assert abs(r["cost_usd"] - (2 * 0.005 + 0.0004) / 1000) < 1e-12
    assert parse_line("garbage") is None
