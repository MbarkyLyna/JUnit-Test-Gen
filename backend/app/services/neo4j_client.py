from __future__ import annotations

import os
from neo4j import GraphDatabase, AsyncGraphDatabase, AsyncSession

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "test")

# Synchronous driver for initial setup, but we'll use async for main operations.
_driver = None

def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver

async def get_async_driver():
    return AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None

# ---------- Bootstrap (Step 5) ----------
def bootstrap() -> None:
    """Idempotent one-call setup: create constraints/indexes, then seed the
    known classes. Call this ONCE at app startup (or via
    scripts/bootstrap_graph.py) — nothing in this module calls it
    automatically, since hitting the DB on every import/request would be
    wasteful and populate_graph() has no reason to run more than once per
    schema change."""
    init_graph()
    populate_graph()


# ---------- Graph Initialization ----------
def init_graph():
    """Create constraints and indexes (idempotent)."""
    driver = get_driver()
    with driver.session() as session:
        # Unique constraint on class names (node key)
        session.run("CREATE CONSTRAINT class_name_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.name IS UNIQUE")
        # Index on package
        session.run("CREATE INDEX class_package_idx IF NOT EXISTS FOR (c:Class) ON (c.package)")
        # Index on test_code property (for quick retrieval)
        session.run("CREATE INDEX class_test_code_idx IF NOT EXISTS FOR (c:Class) ON (c.test_code)")
    print("Neo4j graph initialized with constraints and indexes.")

def populate_graph():
    """Insert the 6 known classes with their test code and relationships."""
    driver = get_driver()
    with driver.session() as session:
        # Define class data: (name, package, superclass, interfaces, test_code)
        classes = [
            {
                "name": "UserController",
                "package": "com.example.demo.controller",
                "superclass": "BaseController",
                "interfaces": ["Controller"],
                "test_code": '''package com.example.demo.controller;

import com.example.demo.service.UserService;
import com.example.demo.model.User;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(UserController.class)
class UserControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private UserService userService;

    @Test
    void shouldReturnUser() throws Exception {
        User user = new User();
        user.setId(1L);
        when(userService.getUser(1L)).thenReturn(user);

        mockMvc.perform(get("/users/1"))
               .andExpect(status().isOk());
    }
}
'''
            },
            {
                "name": "UserService",
                "package": "com.example.demo.service",
                "superclass": "BaseService",
                "interfaces": [],
                "test_code": '''package com.example.demo.service;

import com.example.demo.repository.UserRepository;
import com.example.demo.model.User;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class UserServiceTest {

    @Mock
    private UserRepository userRepository;

    @InjectMocks
    private UserService userService;

    @Test
    void shouldGetUser() {
        User user = new User();
        user.setId(1L);
        when(userRepository.findById(1L)).thenReturn(java.util.Optional.of(user));

        User result = userService.getUser(1L);
        assertThat(result).isNotNull();
    }
}
'''
            },
            {
                "name": "UserRepository",
                "package": "com.example.demo.repository",
                "superclass": None,
                "interfaces": ["JpaRepository"],
                "test_code": '''package com.example.demo.repository;

import com.example.demo.model.User;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
class UserRepositoryTest {

    @Autowired
    private UserRepository userRepository;

    @Test
    void shouldFindUserById() {
        User user = new User();
        user.setName("test");
        User saved = userRepository.save(user);
        assertThat(userRepository.findById(saved.getId())).isPresent();
    }
}
'''
            },
            {
                "name": "User",
                "package": "com.example.demo.model",
                "superclass": None,
                "interfaces": [],
                "test_code": '''package com.example.demo.model;

import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.assertThat;

class UserTest {

    @Test
    void testSettersAndGetters() {
        User user = new User();
        user.setId(1L);
        user.setName("John");
        assertThat(user.getId()).isEqualTo(1L);
        assertThat(user.getName()).isEqualTo("John");
    }
}
'''
            },
            {
                "name": "TransferService",
                "package": "com.example.demo.service",
                "superclass": "BaseService",
                "interfaces": [],
                "test_code": '''package com.example.demo.service;

import com.example.demo.model.Transfer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.assertj.core.api.Assertions.assertThat;

@ExtendWith(MockitoExtension.class)
class TransferServiceTest {

    @Mock
    private SomeDependency dependency;

    @InjectMocks
    private TransferService transferService;

    @Test
    void shouldExecuteTransfer() {
        Transfer transfer = new Transfer();
        transfer.setAmount(100.0);
        assertThat(transferService.process(transfer)).isTrue();
    }
}
'''
            },
            {
                "name": "CreateController",
                "package": "com.example.demo.controller",
                "superclass": "BaseController",
                "interfaces": ["Controller"],
                "test_code": '''package com.example.demo.controller;

import com.example.demo.service.CreateService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(CreateController.class)
class CreateControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private CreateService createService;

    @Test
    void shouldCreateResource() throws Exception {
        mockMvc.perform(post("/api/resource"))
               .andExpect(status().isCreated());
    }
}
'''
            }
        ]

        for cls in classes:
            # Create or merge the Class node
            result = session.run(
                """
                MERGE (c:Class {name: $name})
                SET c.package = $package,
                    c.superclass = $superclass,
                    c.test_code = $test_code
                RETURN c
                """,
                name=cls["name"],
                package=cls["package"],
                superclass=cls["superclass"],
                test_code=cls["test_code"]
            )
            # Create relationships: EXTENDS (superclass) and IMPLEMENTS (interfaces)
            if cls["superclass"]:
                session.run(
                    """
                    MATCH (child:Class {name: $child})
                    MATCH (parent:Class {name: $parent})
                    MERGE (child)-[:EXTENDS]->(parent)
                    """,
                    child=cls["name"],
                    parent=cls["superclass"]
                )
            for iface in cls["interfaces"]:
                session.run(
                    """
                    MATCH (child:Class {name: $child})
                    MATCH (parent:Class {name: $parent})
                    MERGE (child)-[:IMPLEMENTS]->(parent)
                    """,
                    child=cls["name"],
                    parent=iface
                )
        print("Neo4j graph populated with 6 classes and their tests.")

# ---------- Query Functions ----------
async def get_test_for_class_async(class_name: str) -> str | None:
    """Retrieve test code for a class from Neo4j (async)."""
    driver = await get_async_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (c:Class {name: $name}) RETURN c.test_code AS test_code",
            name=class_name
        )
        record = await result.single()
        return record["test_code"] if record else None

async def find_similar_test_async(
    superclass: str | None,
    interfaces: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """
    Structural fallback used ONLY when there is no exact class-name match.
    Returns (test_code, match_reason) or (None, None). IMPORTANT: the caller
    must treat this as an LLM STYLE EXAMPLE to include in the prompt, never
    as a file to write directly — a class with a different name and shape
    will not compile against a template built for an unrelated domain class,
    even if it shares a superclass/interface.
    """
    driver = await get_async_driver()
    async with driver.session() as session:
        if superclass:
            result = await session.run(
                """
                MATCH (c:Class)-[:EXTENDS]->(:Class {name: $superclass})
                RETURN c.test_code AS test_code, c.name AS name
                LIMIT 1
                """,
                superclass=superclass,
            )
            record = await result.single()
            if record and record["test_code"]:
                return record["test_code"], (
                    f"structural match via shared superclass '{superclass}' "
                    f"(pattern class: {record['name']})"
                )

        for iface in interfaces or []:
            result = await session.run(
                """
                MATCH (c:Class)-[:IMPLEMENTS]->(:Class {name: $iface})
                RETURN c.test_code AS test_code, c.name AS name
                LIMIT 1
                """,
                iface=iface,
            )
            record = await result.single()
            if record and record["test_code"]:
                return record["test_code"], (
                    f"structural match via shared interface '{iface}' "
                    f"(pattern class: {record['name']})"
                )

    return None, None


def get_test_for_class_sync(class_name: str) -> str | None:
    """Synchronous wrapper (for startup scripts)."""
    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            "MATCH (c:Class {name: $name}) RETURN c.test_code AS test_code",
            name=class_name
        )
        record = result.single()
        return record["test_code"] if record else None