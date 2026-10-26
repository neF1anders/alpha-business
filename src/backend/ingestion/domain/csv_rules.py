"""Company/payment CSV schema and conservative row normalisation rules."""
import re
from decimal import Decimal, InvalidOperation

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
