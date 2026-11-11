"""CLI for corpus ingestion: make ingest / make ingest-rebuild. Logic lives in rag_agent.ingest."""

import sys

from rag_agent.ingest import main

if __name__ == "__main__":
    sys.exit(main())
