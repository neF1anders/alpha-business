"""Resumable, bounded collection from dated business archives.

State and compressed originals live in a separate snapshot directory. No LLM.
"""
import argparse
from datetime import date

from ..application.archive_crawl import run
from ..infrastructure.crawl_state import connect_state
from ..infrastructure.http_fetcher import UrllibFetcher
from .composition import create_storage


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True);p.add_argument('--seed',required=True)
    p.add_argument('--target',type=int,default=5000)
    p.add_argument('--since',type=date.fromisoformat,default=date(2026,6,1))
    p.add_argument('--until',type=date.fromisoformat,default=date(2026,10,24))
    p.add_argument('--workers',type=int,default=4);p.add_argument('--interval',type=float,default=.25)
    a=p.parse_args()
    if a.target<1 or a.target>20000 or not 1<=a.workers<=4 or a.interval<.25 or a.since>a.until:
        p.error('invalid bounds: target 1..20000, workers 1..4, interval >= .25, since <= until')
    result=run(create_storage(),UrllibFetcher(),connect_state,a)
    if not result['target_met']:raise SystemExit(2)


if __name__=='__main__':main()
