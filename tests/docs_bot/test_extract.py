from tools.docs_bot import extract


def test_env_vars_in_real_server(component, base_files):
    reads = {r.name: r for r in extract.env_reads(base_files[component.code_files[0]], "recommendation_server.py")}
    assert {n: (r.kind, r.default, r.line) for n, r in reads.items()} == {
        "GCP_PROJECT_ID": ("index", None, 46),
        "DISABLE_PROFILER": ("presence", None, 101),
        "ENABLE_TRACING": ("index", None, 114),
        "COLLECTOR_SERVICE_ADDR": ("get", "localhost:4317", 116),
        "PORT": ("get", "8080", 130),
        "PRODUCT_CATALOG_SERVICE_ADDR": ("get", "''", 131),
    }


def test_env_var_forms():
    source = "\n".join([
        'a = os.environ["A"]',
        "b = os.environ.get('B')",
        'c = int(os.getenv("C", str(5)))',
        '# d = os.environ["COMMENTED_OUT"]',
        'if "E" in os.environ: pass',
        'a2 = os.environ.get("A", "late default")',
        'n = os.environ.get("N", 3)',
    ])
    reads = {r.name: r for r in extract.env_reads(source, "x.py")}
    assert {n: (r.kind, r.default, r.line) for n, r in reads.items()} == {
        "A": ("index", None, 1),  # first read wins
        "B": ("get", None, 2),
        "C": ("get", "str(5)", 3),
        "E": ("presence", None, 5),
        "N": ("get", "3", 7),
    }


def test_manifest_extractor_on_real_manifest(component, base_files):
    env, ports = extract.manifest_facts(base_files[component.manifest])
    assert env == {"PORT": "8080", "PRODUCT_CATALOG_SERVICE_ADDR": "productcatalogservice:3550",
                   "DISABLE_PROFILER": "1"}
    assert ports == [8080]


def test_proto_extractor_finds_list_recommendations(component, base_files):
    rpcs = extract.proto_rpcs(base_files[component.proto_file], "RecommendationService")
    assert rpcs == [("ListRecommendations", "ListRecommendationsRequest", "ListRecommendationsResponse")]
    assert extract.proto_rpcs(base_files[component.proto_file], "NoSuchService") == []


def test_proto_extractor_skips_comments_and_handles_streams():
    proto = """
    service S {
      // rpc Old(A) returns (B) {}
      rpc Get(GetReq) returns (GetResp) { option deprecated = true; }
      rpc Watch(stream WReq) returns (stream WResp) {}
    }
    service Other { rpc Nope(X) returns (Y) {} }
    """
    assert extract.proto_rpcs(proto, "S") == [("Get", "GetReq", "GetResp"), ("Watch", "WReq", "WResp")]


def test_extract_combines_sources(component, pr1_files):
    facts = extract.extract(component, pr1_files.get)
    assert facts.env["MAX_RECOMMENDATIONS"].default == "5"
    assert facts.env["MAX_RECOMMENDATIONS"].line == 71
    assert facts.manifest_env["MAX_RECOMMENDATIONS"] == "5"
    assert [r[0] for r in facts.rpcs] == ["ListRecommendations"]
