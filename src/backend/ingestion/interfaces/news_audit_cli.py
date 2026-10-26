"""Verify a completed news snapshot against its stored source files."""
import argparse
import json

from ..application.audit_snapshot import audit


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',required=True);parser.add_argument('--seed')
    parser.add_argument('--allow-partial', action='store_true', help='Verify saved rows without claiming the requested target was reached')
    args=parser.parse_args()
    print(json.dumps(audit(args.snapshot,args.seed,args.allow_partial),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
