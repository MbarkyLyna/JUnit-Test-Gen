"""Standalone smoke test for qwen2.5-coder:1.5b JUnit generation output."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ollama_client

HELLO_CONTROLLER = """package com.example.demo;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class HelloController {
    private final UserService userService;

    public HelloController(UserService userService) {
        this.userService = userService;
    }

    @GetMapping("/hello")
    public String hello(@RequestParam(required = false) String name) {
        if (!userService.isValidName(name)) {
            return userService.welcomeUser(null);
        }
        return userService.welcomeUser(name);
    }
}"""


async def main() -> None:
    prompt = ollama_client.build_test_generation_prompt(
        target_class_name="HelloController",
        target_source=HELLO_CONTROLLER,
        dependencies=[
            {
                "dependency_type": "UserService",
                "field_name": "userService",
                "resolution": "concrete",
                "implementations": [
                    {
                        "fqn": "com.example.demo.UserService",
                        "annotations": ["@Service"],
                        "source": "public class UserService { /* ... */ }",
                    }
                ],
            }
        ],
        uncovered_lines=[17, 18, 19, 20],
        coverage_pct=45.0,
    )
    print(f"Model: {ollama_client.DEFAULT_MODEL}")
    print("Generating test class…")
    code = await ollama_client.generate_tests(prompt)
    valid, reason = ollama_client.validate_java_test_code(code)
    print(f"Valid: {valid} ({reason})")
    print("--- Generated code (first 80 lines) ---")
    for i, line in enumerate(code.splitlines()[:80], 1):
        print(f"{i:3}| {line}")
    if not valid:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
