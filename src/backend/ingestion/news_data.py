"""Entry point: python -m ingestion.news_data (see interfaces/news_collect_cli.py)."""
from .interfaces.news_collect_cli import main

if __name__ == '__main__':
    main()
