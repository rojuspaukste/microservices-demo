# recommendationservice

<!-- docs-bot:meta source-commit=19b8c26 -->
> Generated from commit `19b8c26` · last verified 2026-09-28 · owner: repo-wide team (see Owners) · status: draft

## What it does [LLM, cited]

Returns up to 5 product IDs for the "you might also like" sections of the product page, cart and order confirmation. There is no real recommendation logic: it fetches the whole catalog, removes the products the caller sent, and picks the rest at random (`recommendation_server.py:70-86`). `user_id` is accepted but ignored, so results are neither personalised nor stable between calls.

## Where it fits [extracted]

- **Called by:** `frontend` (`kubernetes-manifests/frontend.yaml`: `RECOMMENDATION_SERVICE_ADDR=recommendationservice:8080`)
- **Calls:** `productcatalogservice:3550` → `ListProducts`, once per request, uncached (`recommendation_server.py:73`)
- **System:** online-boutique

## API [extracted]

<!-- docs-bot:begin api -->
| RPC | Request | Response |
| --- | --- | --- |
| `ListRecommendations` | `ListRecommendationsRequest` | `ListRecommendationsResponse` |
<!-- docs-bot:end api -->

Defined in `protos/demo.proto` (service `RecommendationService`). `product_ids` in the request is an **exclusion** list. The service also implements the standard gRPC health check (`Check` / `Watch`, `recommendation_server.py:88-94`).

## Configuration [extracted]

<!-- docs-bot:begin configuration -->
| Env var | Default in code | Manifest value | Read at |
| --- | --- | --- | --- |
| `COLLECTOR_SERVICE_ADDR` | `localhost:4317` | — | `recommendation_server.py:117` |
| `DISABLE_PROFILER` | — (presence check) | `1` | `recommendation_server.py:102` |
| `ENABLE_TRACING` | — | — | `recommendation_server.py:115` |
| `GCP_PROJECT_ID` | — | — | `recommendation_server.py:46` |
| `MAX_RECOMMENDATIONS` | `5` | `5` | `recommendation_server.py:71` |
| `PORT` | `8080` | `8080` | `recommendation_server.py:131` |
| `PRODUCT_CATALOG_SERVICE_ADDR` | `''` | `productcatalogservice:3550` | `recommendation_server.py:132` |
<!-- docs-bot:end configuration -->

## Run & test locally [extracted + LLM]

```bash
cd src/recommendationservice
pip install -r requirements.txt
PRODUCT_CATALOG_SERVICE_ADDR=localhost:3550 python recommendation_server.py
python client.py 8080        # smoke test: sends user_id="test", product_ids=["test"]
```

Needs a reachable `productcatalogservice`, otherwise every request fails. Container: `python recommendation_server.py`, exposes 8080 (`Dockerfile`).

## How it works [LLM, cited]

1. Calls `ListProducts` on the product catalog for every request, with no cache (`recommendation_server.py:73`).
2. Removes the product IDs sent by the caller from the catalog (`recommendation_server.py:75`).
3. Returns a random sample of at most 5 of the remaining products (`recommendation_server.py:77-81`).

## Gotchas & history [owner]

- The service exits at startup if `PRODUCT_CATALOG_SERVICE_ADDR` is empty.
- The profiler code is commented out ("Temporarily removed in PR #3196"), but "Profiler enabled." is still logged when `DISABLE_PROFILER` is unset (`recommendation_server.py:51-67`).
- Result order is not reproducible, even with a seeded `random`, because of the `set()` in step 2.

## Owners [extracted]

`.github/CODEOWNERS` assigns the whole repository to one team; there is no per-service owner.
