# Shop API

[![CI](https://img.shields.io/badge/ci-passing-brightgreen)](https://example.com)

Order and catalogue service for the sample storefront. It exposes a JSON API for
browsing products and placing orders, keeps state in PostgreSQL, caches catalogue
reads in Redis, and sends order confirmation mail through a Celery worker.

## Running

```bash
docker compose up --build
shopctl serve
```
