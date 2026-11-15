#!/usr/bin/env python3
"""Sync the published CBE USD/EGP BUY and SELL rates to Supabase.

Read-only CBE publication retrieval; one privileged RPC write to FX rate cache.
This script never writes booking or payment rows, and never guesses an exchange rate.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

CBE_URL = 'https://www.cbe.org.eg/en/economic-research/statistics/cbe-exchange-rates'
API_SOURCE = 'https://allratestoday.com/central-bank-rates-api/cbe'
API_URL = 'https://allratestoday.com/api/v1/central-bank/cbe/latest'


def get_url(url: str, token: str | None = None) -> bytes:
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; DesertRoseHotelFxSync/1.0; administrative rate retrieval)',
        'Accept': 'text/html,application/json;q=0.9,*/*;q=0.8',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(f'Upstream HTTP {response.status}')
        return response.read(2_000_000)


def as_decimal(value) -> Decimal:
    try:
        decimal = Decimal(str(value).replace(',', '').strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f'Invalid FX rate value {value!r}') from exc
    if not decimal.is_finite() or decimal < Decimal('1') or decimal > Decimal('500'):
        raise ValueError('FX rate is outside a supported range')
    if decimal != decimal.quantize(Decimal('.0001')):
        raise ValueError('FX rate has more than 4 decimals: prevent silent rounding')
    return decimal


def check_date(rate_date: dt.date) -> None:
    # Cairo day; GitHub Actions invokes in the afternoon so UTC date should match.
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo('Africa/Cairo')).date()
    if not (today - dt.timedelta(days=7) <= rate_date <= today):
        raise ValueError(f'CBE source has a stale/future rate date: {rate_date}, Cairo {today}')


def parse_official_page(html_bytes: bytes) -> tuple[dt.date, Decimal, Decimal]:
    soup = BeautifulSoup(html_bytes, 'html.parser')
    for el in soup(['script', 'style', 'noscript']):
        el.decompose()
    text = ' '.join(soup.stripped_strings)
    if 'requested URL was rejected' in text.lower():
        raise ValueError('CBE site blocked automated HTTP access')
    match = re.search(r'Rates\s+for\s+Date\s*:\s*(\d{1,2}/\d{1,2}/\d{4})', text, re.I)
    if not match:
        raise ValueError('Official CBE page date cannot be parsed; STOP, do not guess')
    rate_date = dt.datetime.strptime(match.group(1), '%d/%m/%Y').date()
    us_dollar_rows = []
    for tr in soup.select('tr'):
        cells = [cell.get_text(' ', strip=True) for cell in tr.select('th, td')]
        if cells and re.search(r'^\s*(?:US\s+Dollar|USD)\s*$', cells[0], re.I):
            numbers = [re.sub(r'[^0-9.]', '', c) for c in cells[1:]]
            numbers = [n for n in numbers if re.fullmatch(r'\d+(?:\.\d+)?', n)]
            if len(numbers) >= 2:
                us_dollar_rows.append((numbers[0], numbers[1]))
    if len(us_dollar_rows) == 1:
        buy, sell = map(as_decimal, us_dollar_rows[0])
    else:
        # Fallback to visible text when CBE renders tabular data without native <tr> tags.
        snippet = re.search(r'\bUS\s+Dollar\s+(\d{1,3}(?:\.\d{1,4})?)\s+(\d{1,3}(?:\.\d{1,4})?)', text, re.I)
        if not snippet:
            raise ValueError('Official CBE USD buy/sell row cannot be parsed')
        buy, sell = as_decimal(snippet.group(1)), as_decimal(snippet.group(2))
    if sell < buy:
        raise ValueError('Official sell rate is smaller than buy rate')
    check_date(rate_date)
    return rate_date, buy, sell


def parse_republisher(json_bytes: bytes) -> tuple[dt.date, Decimal, Decimal]:
    data = json.loads(json_bytes)
    if data.get('bank', '').lower() != 'cbe':
        raise ValueError('API did not identify the CBE publication')
    rate_date = dt.date.fromisoformat(data['rate_date'])
    check_date(rate_date)
    rows = data.get('rates')
    if not isinstance(rows, list):
        raise ValueError('API returned no CBE rate table')
    values = {}
    for row in rows:
        if row.get('base') == 'USD' and row.get('quote') == 'EGP' and row.get('type') in ('buy', 'sell'):
            values[row['type']] = as_decimal(row['value'])
    if 'buy' not in values or 'sell' not in values or values['sell'] < values['buy']:
        raise ValueError('CBE published USD BUY and SELL are not both available')
    return rate_date, values['buy'], values['sell']


def load_cbe() -> tuple[dt.date, Decimal, Decimal, str]:
    try:
        result = parse_official_page(get_url(CBE_URL))
        print('CBE official website read succeeded')
        return (*result, CBE_URL)
    except Exception as exc:
        print(f'CBE direct page unavailable: {exc}', file=sys.stderr)
    # Optional backup: an API that republishes CBE official figures, requires a key.
    token = os.environ.get('CBE_REPUBLISHER_API_KEY', '').strip()
    if not token:
        raise RuntimeError('CBE website could not be read. Set CBE_REPUBLISHER_API_KEY to enable licensed/API backup; no FX rate was stored.')
    result = parse_republisher(get_url(API_URL, token))
    print('CBE publication read through third-party API (source labeled in database)')
    return (*result, API_SOURCE)


def upload_rate(rate_date: dt.date, buy: Decimal, sell: Decimal, source: str) -> dict:
    base = os.environ['SUPABASE_URL'].strip().rstrip('/')
    if not re.fullmatch(r'https://[a-z0-9-]+\.supabase\.co', base):
        raise ValueError('SUPABASE_URL is not an expected Supabase project URL')
    secret = os.environ['SUPABASE_SERVER_KEY'].strip()
    if not secret:
        raise ValueError('Missing SUPABASE_SERVER_KEY')
    payload = json.dumps({
        'p_rate_date': rate_date.isoformat(),
        'p_buy_rate': str(buy),
        'p_sell_rate': str(sell),
        'p_source_url': source,
    }).encode('utf-8')
    headers = {'apikey': secret, 'Content-Type': 'application/json', 'Accept': 'application/json'}
    # Legacy service_role keys are JWTs; new sb_secret keys are NOT bearer JWTs.
    if secret.count('.') == 2:
        headers['Authorization'] = 'Bearer ' + secret
    request = urllib.request.Request(
        base + '/rest/v1/rpc/hotel_fx_upsert_cbe_v17',
        data=payload, headers=headers, method='POST')
    with urllib.request.urlopen(request, timeout=25) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f'Supabase responded HTTP {response.status}')
        raw = response.read(100000)
    return json.loads(raw)


def main() -> int:
    try:
        rate_date, buy, sell, source = load_cbe()
        result = upload_rate(rate_date, buy, sell, source)
        print(json.dumps({'status': 'ok', 'date': rate_date.isoformat(), 'cbe_buy': str(buy),
                          'cbe_sell': str(sell), 'publication_url': source,
                          'supabase_result': result}, ensure_ascii=False))
        return 0
    except urllib.error.HTTPError as exc:
        body = exc.read(1000).decode('utf-8', 'replace')
        print(f'FX sync HTTP error {exc.code}: {body}', file=sys.stderr)
    except Exception as exc:
        print(f'FX sync aborted; historical rate remains unchanged: {exc}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
