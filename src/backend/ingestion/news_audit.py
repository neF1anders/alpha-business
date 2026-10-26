"""Entry point: python -m ingestion.news_audit (see interfaces/news_audit_cli.py)."""
from .interfaces.news_audit_cli import main

if __name__ == '__main__':
    main()
