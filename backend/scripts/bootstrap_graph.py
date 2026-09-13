"""
Run this ONCE to build and seed the Neo4j knowledge graph (Step 5).

Nothing in the running app calls init_graph()/populate_graph() automatically
by design — hitting the DB with schema/seed writes on every process start
would be wasteful and is unnecessary once it's been done. Re-run this only
after changing the schema or the seed data in neo4j_client.py.

Usage:
    1. Start Neo4j:
       docker run -d --name neo4j-testgen \\
         -p 7474:7474 -p 7687:7687 \\
         -e NEO4J_AUTH=neo4j/your_real_password \\
         -v neo4j_data:/data \\
         neo4j:5

    2. Export matching env vars (or rely on the defaults in neo4j_client.py):
       export NEO4J_URI=bolt://localhost:7687
       export NEO4J_USER=neo4j
       export NEO4J_PASSWORD=your_real_password

    3. Run this script from the project root:
       python -m scripts.bootstrap_graph
"""
from __future__ import annotations

import sys

from app.services import neo4j_client


def main() -> int:
    try:
        neo4j_client.bootstrap()
    except Exception as e:
        print(f"Graph bootstrap FAILED: {e}", file=sys.stderr)
        print(
            "Check that Neo4j is running and reachable at NEO4J_URI, and that "
            "NEO4J_USER/NEO4J_PASSWORD match the credentials it was started with.",
            file=sys.stderr,
        )
        return 1

    print("Neo4j knowledge graph initialized and seeded successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())