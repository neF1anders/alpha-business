"""Run the project with an isolated PostgreSQL cluster on macOS (no Docker)."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / '.local'
PGDATA = LOCAL / 'postgres'
SOCKET = LOCAL / 'run'
PORT = '55432'


def pg_bin():
    candidate = Path('/opt/homebrew/opt/postgresql@17/bin')
    if not (candidate / 'pg_ctl').is_file():
        raise RuntimeError('Install PostgreSQL 17: brew install postgresql@17')
    return candidate


def environment():
    env = os.environ.copy()
    # Force this project cluster; never inherit a connection to another database.
    env.pop('DATABASE_URL', None)
    env.pop('RETRIEVAL_DATABASE_URL', None)
    for key in ['PGPASSWORD', 'PGSERVICE', 'PGSERVICEFILE', 'PGPASSFILE', 'PGOPTIONS']:
        env.pop(key, None)
    env.update(PGHOST=str(SOCKET), PGPORT=PORT, PGDATABASE='insights', PGUSER='insights')
    env['PATH'] = str(pg_bin()) + os.pathsep + env.get('PATH', '')
    if (LOCAL / 'tessdata' / 'rus.traineddata').exists():
        env['TESSDATA_PREFIX'] = str(LOCAL / 'tessdata')
    return env


def pg_status():
    if not (PGDATA / 'PG_VERSION').is_file():
        return False
    result = subprocess.run([str(pg_bin() / 'pg_ctl'), '-D', str(PGDATA), 'status'],
                            capture_output=True, text=True)
    return result.returncode == 0


def start():
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    SOCKET.mkdir(mode=0o700, exist_ok=True)
    if not (PGDATA / 'PG_VERSION').exists():
        if PGDATA.exists() and any(PGDATA.iterdir()):
            raise RuntimeError('Nonempty uninitialized PostgreSQL directory; inspect it manually')
        subprocess.run([str(pg_bin() / 'initdb'), '-D', str(PGDATA), '-U', 'insights',
                        '--encoding=UTF8', '--locale=en_US.UTF-8', '--auth-local=trust', '--auth-host=reject'],
                       check=True)
    if not pg_status():
        options = "-h '' -k " + str(SOCKET) + " -p " + PORT + " -c unix_socket_permissions=0700"
        subprocess.run([str(pg_bin() / 'pg_ctl'), '-D', str(PGDATA), '-l', str(LOCAL / 'postgres.log'),
                        '-o', options, '-w', 'start'], check=True)
    import psycopg
    with psycopg.connect('', **dict(host=str(SOCKET), port=PORT, user='insights', dbname='postgres'),
                         autocommit=True, connect_timeout=5) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='insights'").fetchone():
            conn.execute('CREATE DATABASE insights')
    print('Local PostgreSQL ready: database insights, private Unix socket ' + str(SOCKET))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'stop', 'status', 'run'])
    parser.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'start':
        start()
    elif args.command == 'stop':
        if pg_status():
            subprocess.run([str(pg_bin() / 'pg_ctl'), '-D', str(PGDATA), '-m', 'fast', '-w', 'stop'], check=True)
        else:
            print('Local PostgreSQL is stopped')
    elif args.command == 'status':
        print('running' if pg_status() else 'stopped')
    else:
        command = args.args
        if command and command[0] == '--':
            command = command[1:]
        if not command:
            parser.error('run requires a command, e.g. run python -m ingestion search ставка')
        if command[0] in ['python', 'python3']:
            command[0] = sys.executable
        raise SystemExit(subprocess.run(command, cwd=ROOT, env=environment()).returncode)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
