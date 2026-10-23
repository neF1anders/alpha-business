"""Conservative CSV filtering, with disk-backed staging and row-level audit."""
import csv
import json
import re
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .common import sha256, write_json

COMPANY_COLUMNS = '''SYNTHETIC_U ORG_TYPE OKFS_NAME COMPANY_AGE_YEARS_GRADATION
ECO_MARK_LABEL MAINOKVED_CODE MAINOKVED_NAME NEGATIVEMARK
AMOUNT_WORKERS_FNS_GRADATION AMOUNT_WORKERS_SPARK_GRADATION CL_TYPE
CL_PRODUCT_RKO_MARK CL_PRODUCT_GAR_MARK CL_PRODUCT_LOA_MARK CL_PRODUCT_LEA_MARK
CL_PRODUCT_DEP_MARK CL_PRODUCT_ZPP_MARK FTS_FLAG FTS_COUNTRY_NAME FACTORING_MARK
REVENUE_COMPANY GROUP_ID CITY REGION'''.split()
LINK_COLUMNS = 'SYNTHETIC_F CITY_NAME RC_NAME SYNTHETIC_U'.split()
PAYMENT_COLUMNS = 'BENEFICIARY PAYER PAY_AMT PAY_CNT'.split()
SOURCES = {
    'companies': ('df_Uid_data_hack.csv', COMPANY_COLUMNS),
    'person_links': ('df_Fid_Uid_link_hack.csv', LINK_COLUMNS),
    'payments': ('df_Uid_payment_hack.csv', PAYMENT_COLUMNS),
}
FLAGS = {c for c in COMPANY_COLUMNS if c.startswith('CL_PRODUCT_')} | {
    'NEGATIVEMARK', 'FTS_FLAG', 'FACTORING_MARK'}


def number(value, *, integer=False, positive=False):
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('invalid_number') from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError('invalid_number')
    if integer and (result != result.to_integral_value() or result > 9223372036854775807):
        raise ValueError('invalid_count')
    return str(int(result)) if integer else format(result, 'f')


def normalize(row):
    result = {k: v.strip() for k, v in row.items()}
    for key, value in result.items():
        if '\x00' in value:
            raise ValueError('nul_character')
        if key in {'SYNTHETIC_U', 'PAYER', 'BENEFICIARY', 'SYNTHETIC_F'}:
            prefix = 'F' if key == 'SYNTHETIC_F' else 'U'
            if not re.fullmatch(prefix + r'_\d+', value):
                raise ValueError('invalid_id')
        elif key in FLAGS and value:
            if value not in {'0', '0.0', '1', '1.0'}:
                raise ValueError('invalid_flag')
            result[key] = str(int(float(value)))
        elif key in {'REVENUE_COMPANY', 'PAY_AMT', 'PAY_CNT'} and value:
            result[key] = number(value, integer=key == 'PAY_CNT', positive=key != 'REVENUE_COMPANY')
    if 'PAY_AMT' in result and (not result['PAY_AMT'] or not result['PAY_CNT']):
        raise ValueError('missing_payment_value')
    return result


def prepare_csv(source_dir, output_dir):
    source_dir, output_dir = Path(source_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / 'manifest.json'
    # A failed run must not leave a valid manifest referring to incomplete outputs.
    manifest.unlink(missing_ok=True)
    stats = {name: {'input': 0, 'accepted': 0, 'rejected': Counter()} for name in SOURCES}
    source_hashes = {name: sha256(source_dir / file) for name, (file, _) in SOURCES.items()}
    with tempfile.TemporaryDirectory(prefix='csv-stage-') as tmp, \
            closing(sqlite3.connect(Path(tmp) / 'stage.db')) as db, \
            (output_dir / 'rejected.jsonl').open('w', encoding='utf-8') as rejected:
        def reject(table, row_num, raw, reason):
            stats[table]['rejected'][reason] += 1
            rejected.write(json.dumps({'file': SOURCES[table][0], 'row': row_num,
                                       'reason': reason, 'data': raw}, ensure_ascii=False) + '\n')

        for table, (filename, columns) in SOURCES.items():
            db.execute(f'CREATE TABLE {table}(row_num INTEGER PRIMARY KEY, key1 TEXT, key2 TEXT, payload TEXT, raw TEXT)')
            with (source_dir / filename).open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream, delimiter=';')
                if reader.fieldnames != columns:
                    raise ValueError(f'{filename}: unexpected CSV header: {reader.fieldnames}')
                for row_num, raw in enumerate(reader, 2):
                    stats[table]['input'] += 1
                    if None in raw or any(v is None for v in raw.values()):
                        reject(table, row_num, raw, 'malformed_row')
                        continue
                    try:
                        row = normalize(raw)
                    except ValueError as exc:
                        reject(table, row_num, raw, str(exc))
                        # Keep a bad company row in conflict detection if its ID is valid.
                        if table == 'companies' and re.fullmatch(r'U_\d+', raw['SYNTHETIC_U'].strip()):
                            db.execute('INSERT INTO companies VALUES (?, ?, ?, ?, ?)',
                                       (-row_num, raw['SYNTHETIC_U'].strip(), '', json.dumps(raw, sort_keys=True), json.dumps(raw)))
                        continue
                    key1 = row.get('PAYER', row.get('SYNTHETIC_U'))
                    key2 = row.get('BENEFICIARY', row.get('SYNTHETIC_F', ''))
                    db.execute(f'INSERT INTO {table} VALUES (?, ?, ?, ?, ?)',
                               (row_num, key1, key2, json.dumps(row, sort_keys=True), json.dumps(raw)))
            if stats[table]['input'] == 0:
                raise ValueError(f'{filename}: empty source table')
            db.execute(f'CREATE INDEX {table}_keys ON {table}(key1, key2)')
            db.commit()

        db.executescript('''
          CREATE TABLE bad_companies AS
            SELECT key1 FROM companies GROUP BY key1 HAVING count(DISTINCT payload)>1 OR min(row_num)<0;
          CREATE UNIQUE INDEX bad_company_key ON bad_companies(key1);
          CREATE TABLE bad_pairs AS
            SELECT key1,key2 FROM payments GROUP BY key1,key2 HAVING count(DISTINCT payload)>1;
          CREATE UNIQUE INDEX bad_pair_key ON bad_pairs(key1,key2);
        ''')
        bad_ids = {r[0] for r in db.execute('SELECT key1 FROM bad_companies')}
        for table, (_, columns) in SOURCES.items():
            with (output_dir / f'{table}.csv').open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=columns + ['SOURCE_ROW'], delimiter=';')
                writer.writeheader()
                # Stable sorting places exact duplicates together; no million-row Python set.
                query = f'''SELECT t.row_num,t.key1,t.key2,t.payload,t.raw,
                    {'EXISTS(SELECT 1 FROM bad_pairs b WHERE b.key1=t.key1 AND b.key2=t.key2)' if table == 'payments' else '0'}
                    FROM {table} t WHERE t.row_num>0 ORDER BY t.key1,t.key2,t.payload,t.row_num'''
                previous = None
                for row_num, key1, key2, payload, raw, bad_pair in db.execute(query):
                    reason = None
                    if key1 in bad_ids or (table == 'payments' and key2 in bad_ids):
                        reason = 'conflicting_company_id'
                    elif table == 'payments' and key1 == key2:
                        reason = 'self_payment'
                    elif bad_pair:
                        reason = 'conflicting_payment_pair'
                    elif payload == previous:
                        reason = 'duplicate'
                    previous = payload
                    if reason:
                        reject(table, row_num, json.loads(raw), reason)
                    else:
                        row = json.loads(payload)
                        row['SOURCE_ROW'] = row_num
                        writer.writerow(row)
                        stats[table]['accepted'] += 1
        for name, (filename, _) in SOURCES.items():
            if sha256(source_dir / filename) != source_hashes[name]:
                raise RuntimeError(f'Source changed during processing: {filename}')
        report = {'version': 1, 'conflicting_company_ids': sorted(bad_ids), 'tables': stats,
                  'sources': source_hashes,
                  'outputs': {name: sha256(output_dir / f'{name}.csv') for name in SOURCES}}
        write_json(manifest, report)
        return report
