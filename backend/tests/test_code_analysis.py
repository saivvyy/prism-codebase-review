import pytest
from app.services.code_analysis import CodeAnalysisService, get_code_analysis_service


@pytest.fixture
def analyzer():
    return CodeAnalysisService()


def test_analyze_python_valid(analyzer):
    code = """
import os
from app.database import db

class UserController:
    def __init__(self, service):
        self.service = service

    def get_users(self):
        return db.query("SELECT * FROM users")
"""
    patch = """@@ -9,2 +9,2 @@
+    def get_users(self):
+        return db.query("SELECT * FROM users")
"""
    result = analyzer.analyze_file("src/controllers/user.py", code, patch)

    assert result.is_supported is True
    assert result.parse_success is True
    assert result.language == "python"

    # Classes
    assert len(result.classes) == 1
    assert result.classes[0].name == "UserController"
    assert "get_users" in result.classes[0].methods

    # Functions
    fn_names = [f.name for f in result.functions]
    assert "__init__" in fn_names
    assert "get_users" in fn_names

    # Imports
    assert any("database" in imp.source for imp in result.imports)

    # Function Calls
    call_targets = [c.target for c in result.calls]
    assert any("db.query" in t for t in call_targets)

    # Changed Regions
    assert len(result.changed_regions) >= 1
    assert result.changed_regions[0].enclosing_class == "UserController"
    assert result.changed_regions[0].enclosing_function == "get_users"


def test_analyze_typescript_valid(analyzer):
    code = """
import { Request, Response } from 'express';
import { db } from '../db';

export class OrderController {
    async handleCreateOrder(req: Request, res: Response) {
        const order = await db.insert(req.body);
        return res.json(order);
    }
}
"""
    patch = """@@ -4,4 +4,5 @@
+    async handleCreateOrder(req: Request, res: Response) {
+        const order = await db.insert(req.body);
"""
    result = analyzer.analyze_file("src/controllers/order_controller.ts", code, patch)

    assert result.is_supported is True
    assert result.parse_success is True
    assert result.language == "typescript"
    assert len(result.classes) == 1
    assert result.classes[0].name == "OrderController"
    assert len(result.functions) == 1
    assert result.functions[0].name == "handleCreateOrder"
    assert result.functions[0].is_async is True

    # Function calls
    call_targets = [c.target for c in result.calls]
    assert any("db.insert" in t or "res.json" in t for t in call_targets)


def test_analyze_unsupported_file(analyzer):
    content = "# Project Documentation\n\nThis is a markdown file with architectural notes."
    patch = "@@ -1,2 +1,3 @@\n+# New section\n"

    result = analyzer.analyze_file("README.md", content, patch)

    assert result.is_supported is False
    assert result.parse_success is True
    assert result.language == "unsupported"
    assert len(result.classes) == 0
    assert len(result.functions) == 0
    assert len(result.changed_regions) == 1
    assert "Unsupported format" in result.summary


def test_analyze_malformed_code(analyzer):
    # Malformed syntax in Python
    malformed_code = "def broken_syntax(\n  return 123 +"

    result = analyzer.analyze_file("broken.py", malformed_code)

    assert result.is_supported is True
    assert result.parse_success is True  # Tree-sitter recovers and returns partial AST
    assert result.language == "python"
    assert isinstance(result.summary, str)


def test_analyze_diff_only(analyzer):
    patch = """@@ -10,3 +10,8 @@
+from database import db
+class DirectQueryController:
+    def query(self):
+        return db.select()
"""
    result = analyzer.analyze_diff("patch_only.py", patch)

    assert result.is_supported is True
    assert result.language == "python"
    assert len(result.classes) >= 1
    assert result.classes[0].name == "DirectQueryController"


def test_analyze_java(analyzer):
    code = """
package com.example.service;

import java.sql.Connection;

public class PaymentService {
    public void processPayment() {
        Connection.execute();
    }
}
"""
    result = analyzer.analyze_file("PaymentService.java", code)

    assert result.is_supported is True
    assert result.language == "java"
    assert len(result.classes) == 1
    assert result.classes[0].name == "PaymentService"
    assert len(result.functions) == 1
    assert result.functions[0].name == "processPayment"


def test_analyze_cpp(analyzer):
    code = """
#include <iostream>

class Engine {
public:
    void start() {
        std::cout << "Starting";
    }
};
"""
    result = analyzer.analyze_file("engine.cpp", code)

    assert result.is_supported is True
    assert result.language == "cpp"
    assert len(result.classes) == 1
    assert result.classes[0].name == "Engine"
