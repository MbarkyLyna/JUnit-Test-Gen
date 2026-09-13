from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.services import neo4j_client

from app.api.routes import router

app = FastAPI(
    title="Spring Test Generator",
    description="AI-powered JUnit test generation for Java/Spring Boot projects",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- STARTUP EVENT (add this block) ----------
@app.on_event("startup")
async def startup_event():
    # Initialize Neo4j constraints and indexes
    neo4j_client.init_graph()
    # Populate the graph only if it's empty
    driver = neo4j_client.get_driver()
    with driver.session() as session:
        result = session.run("MATCH (c:Class) RETURN count(c) AS cnt")
        count = result.single()["cnt"]
        if count == 0:
            neo4j_client.populate_graph()
# ----------------------------------------------------

app.include_router(router)

@app.get("/")
async def root() -> dict:
    return {"service": "spring-test-gen", "docs": "/docs"}