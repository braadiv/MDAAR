"""Tests for PolyglotFactExtractor, SchemaParser, APISpecParser, and RouteParser."""

import json

from mdaar.analyzers import (
    APISpecParser,
    PolyglotFactExtractor,
    RepoScanner,
    RouteParser,
    SchemaParser,
)


def test_polyglot_fact_extractor_multi_language(tmp_path):
    (tmp_path / "composer.json").write_text(
        json.dumps({"require": {"yiisoft/yii2": "~2.0.14"}}),
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("fastapi==0.115.0\nsqlalchemy>=2.0\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "14.2.0", "react": "18.3.0"}}),
        encoding="utf-8",
    )

    (tmp_path / "app.py").write_text(
        "from fastapi import FastAPI\n"
        "class OrderService:\n"
        "    async def process_order(self, order_id: int):\n"
        "        return order_id\n",
        encoding="utf-8",
    )
    (tmp_path / "controllers").mkdir()
    (tmp_path / "controllers" / "OrderController.php").write_text(
        "<?php\nnamespace app\\controllers;\n"
        "class OrderController extends Controller {\n"
        "    public function behaviors() { return ['access' => ['class' => AccessControl::class]]; }\n"
        "    public function actionCreateOrder() { return true; }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text(
        "package main\ntype HealthStatus struct { Ok bool }\nfunc CheckHealth() bool { return true }\n",
        encoding="utf-8",
    )

    scan_res = RepoScanner(tmp_path).scan()
    facts = PolyglotFactExtractor().extract(scan_res, project_name="PolyTest")

    assert "Yii2 (PHP)" in facts.detected_frameworks
    assert "FastAPI (Python)" in facts.detected_frameworks
    assert "Next.js (React/TS)" in facts.detected_frameworks
    assert "controllers/OrderController.php" in facts.rbac_files
    assert "controllers/OrderController.php" in facts.event_behavior_files
    assert "OrderService" in facts.verified_identifiers
    assert "OrderController" in facts.verified_identifiers
    assert "CheckHealth" in facts.verified_identifiers


def test_schema_and_api_parsers_extract_ddl_migrations_postman_and_routes(tmp_path):
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE `users` (\n"
        "  `id` INT NOT NULL AUTO_INCREMENT,\n"
        "  `email` VARCHAR(255) NOT NULL,\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB;\n\n"
        "CREATE TABLE `orders` (\n"
        "  `id` INT NOT NULL AUTO_INCREMENT,\n"
        "  `user_id` INT NOT NULL,\n"
        "  `item_name` VARCHAR(100) NOT NULL,\n"
        "  PRIMARY KEY (`id`),\n"
        "  FOREIGN KEY (`user_id`) REFERENCES `users`(`id`)\n"
        ");\n",
        encoding="utf-8",
    )
    (tmp_path / "Orders.postman_collection.json").write_text(
        json.dumps(
            {
                "info": {"name": "Orders API"},
                "item": [
                    {
                        "name": "Auth",
                        "item": [
                            {
                                "name": "Login",
                                "request": {
                                    "method": "POST",
                                    "url": {"raw": "{{base_url}}/api/auth/login", "path": ["api", "auth", "login"]},
                                    "body": {"mode": "raw", "raw": '{"username":"demo","password":"secret123"}'},
                                },
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "controllers").mkdir()
    (tmp_path / "controllers" / "OrderController.php").write_text(
        "<?php\nclass OrderController extends ActiveController {\n"
        "    public function actionPendingShipments($warehouseId) { return []; }\n"
        "}\n",
        encoding="utf-8",
    )

    scan_res = RepoScanner(tmp_path).scan()
    tables, rels = SchemaParser.parse_repository(scan_res)
    endpoints = APISpecParser.parse_repository(scan_res)

    table_names = {t.name for t in tables}
    assert {"users", "orders"} <= table_names
    assert any(r.from_table == "orders" and r.to_table == "users" for r in rels)

    erd = SchemaParser.build_mermaid_erd_chunks(tables, rels)
    assert "erDiagram" in erd[0]
    assert "users" in erd[0] and "orders" in erd[0]

    paths = {(ep.method, ep.path) for ep in endpoints}
    assert ("POST", "/api/auth/login") in paths
    assert any("/order/pending-shipments" in ep.path for ep in endpoints)

    # Backwards-compatible RouteParser check
    py_routes = RouteParser.extract_routes_from_code('@router.post("/v1/orders")', ".py")
    assert py_routes == [{"method": "POST", "path": "/v1/orders", "framework": "Python (FastAPI/Flask)"}]
