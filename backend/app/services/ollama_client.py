from __future__ import annotations

import os
import re

import httpx

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "phi4-mini")




async def check_ollama_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


async def list_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


def build_test_generation_prompt(
    target_class_name: str,
    target_source: str,
    dependencies: list[dict],
    uncovered_lines: list[int],
    coverage_pct: float,
    referenced_classes: list[dict] | None = None,
    class_kind: str | None = None,
    method_return_types: list[tuple[str, str]] | None = None,
) -> str:
    dep_section = ""
    for dep in dependencies:
        dep_section += f"\n### Dependency: {dep['dependency_type']} ({dep['field_name']})\n"
        dep_section += f"Resolution: {dep['resolution']}\n"
        if dep.get("is_external_bean") or dep.get("resolution") == "external_bean":
            dep_section += (
                "Type: External Framework Bean / Class not in project sources.\n"
                "INSTRUCTION: Treat as an opaque type and mock using @Mock or @MockBean.\n"
            )
        if dep.get("ambiguity_note"):
            dep_section += f"Note: {dep['ambiguity_note']}\n"
        for impl in dep.get("implementations", []):
            dep_section += f"\nImplementation: {impl['fqn']}\n"
            if impl.get("annotations"):
                dep_section += f"Annotations: {impl['annotations']}\n"
            dep_section += f"```java\n{impl.get('source', '')}\n```\n"

    ref_section = ""
    if referenced_classes:
        ref_section = (
            "\nReferenced in-project classes (not injected, but used internally):\n"
        )
        for ref in referenced_classes:
            ref_section += f"\n### {ref['class_name']} ({ref['fqn']})\n"
            if ref.get("context_kind") == "signatures":
                ref_section += (
                    "Public API (signatures only — class too large for full source):\n"
                )
            else:
                ref_section += "Source:\n"
            ref_section += f"```java\n{ref.get('source', '')}\n```\n"

    lines_section = (
        ", ".join(str(l) for l in uncovered_lines[:80])
        if uncovered_lines
        else "No line-level data; cover all non-trivial business logic methods."
    )

    kind_section = f"\nClass kind: {class_kind}\n" if class_kind else ""

    return_types_section = ""
    if method_return_types:
        return_types_section = "\nKnown method return types (do not guess these):\n"
        for method_name, return_type in method_return_types:
            return_types_section += f"- {method_name} returns {return_type}\n"

    prompt = f"""You are an expert Java/Spring Boot test engineer. Generate a JUnit 5 test class for the target class below.

Requirements:
- Use JUnit 5 (@Test, @ExtendWith(MockitoExtension.class) or @SpringBootTest as appropriate)
- Use Mockito for mocking dependencies when unit testing; prefer @Mock and @InjectMocks for service layers
- For external framework beans (e.g., RestTemplate, ObjectMapper, JdbcTemplate, EntityManager, Clock), treat as opaque types and mock them using @Mock or @MockBean
- For Spring components, use @SpringBootTest or @WebMvcTest with @MockBean where needed
- Tests MUST compile and pass with Maven
- Focus specifically on covering these uncovered non-trivial line numbers: {lines_section}
- Do NOT waste code or assertions testing Lombok getters/setters, equals/hashCode/toString, or plain DTO field declarations
- Current class line coverage: {coverage_pct}%
- Project goal: reach at least 80% overall line coverage
- Include meaningful assertions, not empty tests
- Use the REAL dependency implementations listed below when wiring mocks or test context
- Output ONLY valid Java code for the test class, no markdown fences or explanation
- Follow Spring's code formatting conventions strictly, since this project enforces them via a build-time formatter check: use tab indentation (not spaces), place the opening brace on the same line as its declaration, one blank line between methods, imports in a single block with no blank lines between them and no wildcard imports, and no trailing whitespace

Target class: {target_class_name}
{kind_section}
```java
{target_source}
```
{return_types_section}
Resolved Spring DI dependencies (static analysis):
{dep_section}{ref_section}
Generate a complete test class named {target_class_name}Test in the correct package with all imports.
"""

    # Debug: dump the exact prompt sent to the model, so it can be inspected
    # directly instead of inferring its contents from output quality alone.
    with open("last_prompt.txt", "w", encoding="utf-8") as f:
        f.write(prompt)

    return prompt


def _trim_to_java_boundaries(code: str) -> str:
    """
    Trim preamble text before package/import/class and postamble text after final closing brace '}'.
    """
    start_idx = -1
    for match_str in ["package ", "import ", "public class ", "class "]:
        idx = code.find(match_str)
        if idx != -1:
            if start_idx == -1 or idx < start_idx:
                start_idx = idx

    if start_idx != -1:
        code = code[start_idx:]

    last_brace = code.rfind("}")
    if last_brace != -1:
        code = code[: last_brace + 1]

    return code.strip()


def extract_java_from_response(text: str) -> str:
    """
    Robustly extract raw Java source code from LLM generation responses.
    Handles:
    - Fenced markdown blocks (```java ... ``` or ``` ...)
    - Preambles ("Here is the test class...")
    - Postambles / explanations after the test class
    - Code starting from `package` / `import` / `class` down to final closing brace `}`.
    """
    if not text:
        return ""

    raw = text.strip()

    # 1. Try regex pattern matching ```java ... ``` or ``` ... ```
    fenced_blocks = re.findall(r"```(?:java|JAVA)?\s*\n?(.*?)```", raw, re.DOTALL)
    if fenced_blocks:
        for block in fenced_blocks:
            b_clean = block.strip()
            if "class " in b_clean or "package " in b_clean:
                return _trim_to_java_boundaries(b_clean)
        return _trim_to_java_boundaries(fenced_blocks[0].strip())

    # 2. If no valid code fence found, strip preamble and postamble using Java boundaries
    return _trim_to_java_boundaries(raw)



def validate_java_test_code(code: str) -> tuple[bool, str]:
    """
    Validate that extracted code is a non-empty, syntactically plausible JUnit test class.
    Returns (is_valid: bool, reason: str).
    """
    if not code or not code.strip():
        return False, "Extracted code response is completely empty."

    if "class " not in code and "interface " not in code:
        return False, "Response does not contain a Java class definition ('class Name')."

    if "@Test" not in code and "@ParameterizedTest" not in code and "@RepeatedTest" not in code:
        return False, "Response does not contain any JUnit test annotations (@Test)."

    open_braces = code.count("{")
    close_braces = code.count("}")
    if open_braces == 0 or open_braces != close_braces:
        return False, f"Unbalanced braces in class definition ({open_braces} open '{{', {close_braces} close '}}')."

    return True, "Valid Java test class structure."


async def generate_tests(
    prompt: str,
    model: str = DEFAULT_MODEL,
    timeout: float = 300.0,
    target_source: str | None = None,
    has_di_dependencies: bool = False,
) -> str:
    # NOTE: target_source / has_di_dependencies are accepted here to match the
    # call signature test_generator.py already uses. Their original intended
    # behavior in this function was lost when an outdated copy of this file
    # was restored — they are currently accepted but not used for additional
    # logic. If you relied on specific behavior tied to these (e.g. rejecting
    # output that just echoes target_source back unchanged, or adjusting
    # retry behavior when DI mocking is involved), that logic will need to be
    # re-added; it is not reconstructable from the file alone.
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Attempt 1
        resp = await client.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.05, "num_predict": 4096},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("response", "")
        code = extract_java_from_response(raw)

        valid, reason = validate_java_test_code(code)
        if valid:
            return code

        # Attempt 2 (Retry with strict instruction)
        retry_prompt = prompt + "\n\nCRITICAL REMINDER: Output ONLY executable Java code starting with 'package ...'. Do NOT include markdown fences, preamble or explanation."
        retry_resp = await client.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": model,
                "prompt": retry_prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 4096},
            },
        )
        retry_resp.raise_for_status()
        retry_raw = retry_resp.json().get("response", "")
        retry_code = extract_java_from_response(retry_raw)

        valid, reason = validate_java_test_code(retry_code)
        if valid:
            return retry_code

        # Fallback: Raise explicit ValueError so backend handles it as an explicit parsing error, never writing a silent broken/empty file
        raise ValueError(f"LLM Output Parsing Failed: Model output could not be parsed into a valid JUnit test class. Details: {reason}")