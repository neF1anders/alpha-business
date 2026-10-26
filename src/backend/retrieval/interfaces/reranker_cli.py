"""Offline cross-encoder ranking. Scores express relevance, never causal impact."""
import argparse

from ..infrastructure.cross_encoder import model_path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['download'])
    p.parse_args();print(model_path(download=True))

if __name__=='__main__':main()
