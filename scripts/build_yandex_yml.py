#!/usr/bin/env python3
"""Build a safe variant-accurate YML from the public Tilda feed.

No changes to Tilda, no publishing to Yandex. Python 3.9+ stdlib only.

Usage:
  python3 scripts/build_yandex_yml.py --output exports/yandex/catalog.yml
  python3 scripts/build_yandex_yml.py --source path/to/feed.xml --sitemap path/to/sitemap-store.xml --output clean.xml
"""
import argparse
import copy
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, parse_qs
from xml.etree import ElementTree as ET

SOURCE_URL = 'https://euro-elki.ru/tstore/yml/b38b27feaee313b3f633cdddfd130283.yml'
SITEMAP_URL = 'https://euro-elki.ru/sitemap-store.xml'
SOURCE_HOST = 'euro-elki.ru'
MAX_XML_BYTES = 12 * 1024 * 1024
ROOT_PRODUCT_PATH = re.compile(r'^/(?:catalog/(?:tree|accessories|sets)/)?tproduct/(\d+)-[a-z0-9-]+$')
CATEGORY_KIND = {'882381299902': 'tree', '204963358202': 'accessories', '849870954512': 'sets'}
RANGE_TITLE = re.compile(r'(?<!\d)(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*(?:метра|метров|м\b)', re.I)
RANGE_PARAM = re.compile(r'^\s*(\d+(?:[.,]\d+)?)\s*м\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*м\s*$', re.I)

class FeedError(ValueError):
    pass


def _url_path(raw):
    parsed = urlsplit(raw)
    if parsed.scheme != 'https' or parsed.netloc.lower() != SOURCE_HOST:
        raise FeedError('External or insecure product URL')
    if not ROOT_PRODUCT_PATH.fullmatch(parsed.path):
        raise FeedError('Unrecognized product URL path: ' + parsed.path[:100])
    return parsed.path


def load_bytes(source, default_url):
    if source is None:
        source = default_url
    if source.startswith(('https://', 'http://')):
        parsed = urlsplit(source)
        if parsed.scheme != 'https' or parsed.netloc.lower() != SOURCE_HOST:
            raise FeedError('Source host differs from the trusted shop')
        request = Request(source, headers={'User-Agent': 'EuroElkiFeed/1.0', 'Accept': 'application/xml,text/xml,*/*'})
        for attempt in range(4):
            try:
                with urlopen(request, timeout=35) as resp:
                    if urlsplit(resp.geturl()).netloc.lower() != SOURCE_HOST:
                        raise FeedError('Unexpected source redirect')
                    if resp.status != 200:
                        raise FeedError('HTTP status: ' + str(resp.status))
                    body = resp.read(MAX_XML_BYTES + 1)
                break
            except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
                if isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                    raise FeedError('Source '+parsed.path+': '+str(exc)) from exc
                if attempt == 3:
                    raise FeedError('Source '+parsed.path+' failed after 4 attempts: '+str(exc)) from exc
                delay = (3, 9, 20)[attempt]
                print('Retry source '+parsed.path+' after '+str(exc)+' in '+str(delay)+'s', file=sys.stderr, flush=True)
                time.sleep(delay)
    else:
        body = Path(source).read_bytes()
    if not body or len(body) > MAX_XML_BYTES:
        raise FeedError('Empty or oversized source')
    return body


def xmlroot(body):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise FeedError('DTD and entities not supported')
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise FeedError('Invalid XML: ' + str(exc)) from exc


def sitemap_product_paths(raw):
    root = xmlroot(raw)
    paths = set()
    for loc in root.iter():
        if not loc.tag.endswith('loc'):
            continue
        path = _url_path(loc.text or '')
        if path in paths:
            raise FeedError('Duplicate canonical sitemap path: ' + path)
        paths.add(path)
    if not paths:
        raise FeedError('Sitemap has no products')
    return paths


def title_range(title):
    match = RANGE_TITLE.search(title or '')
    return (Decimal(match.group(1).replace(',', '.')), Decimal(match.group(2).replace(',', '.'))) if match else None


def offer_height(offer):
    vals = [(p.text or '') for p in offer.findall('param') if (p.get('name') or '').strip().casefold() == 'высота']
    if len(vals) != 1:
        return None
    match = RANGE_PARAM.fullmatch(vals[0])
    return (Decimal(match.group(1).replace(',', '.')), Decimal(match.group(2).replace(',', '.'))) if match else None


def check_offer_fields(offer):
    price = offer.findtext('price')
    currency = offer.findtext('currencyId')
    name = (offer.findtext('name') or '').strip()
    link = (offer.findtext('url') or '').strip()
    picture = (offer.findtext('picture') or '').strip()
    category = offer.findtext('categoryId')
    try:
        positive = Decimal(price or '0') > 0
    except InvalidOperation:
        positive = False
    if not (positive and name and link and picture and category and currency):
        raise FeedError('Missing price/image/category/name or invalid price: ' + name[:90])
    if currency not in ('RUB', 'RUR'):
        raise FeedError('Unexpected currency: ' + currency)
    if not picture.startswith('https://'):
        raise FeedError('Image URL is not HTTPS')
    _url_path(link)


def choose(group, path, warnings):
    title = (group[0].findtext('name') or '').strip()
    if not title or any((e.findtext('name') or '').strip() != title for e in group):
        raise FeedError('Conflicting root names for ' + path)
    categories = {e.findtext('categoryId') for e in group}
    kind = CATEGORY_KIND.get(next(iter(categories))) if len(categories) == 1 else None
    if not kind or (path.startswith('/catalog/') and path.split('/')[2] != kind):
        raise FeedError('Unexpected or conflicting product category: ' + path)
    if kind == 'tree':
        expected = title_range(title)
        if expected is None:
            raise FeedError('Cannot parse tree height in title: ' + title)
        matches = [x for x in group if offer_height(x) == expected]
        if len(matches) != 1:
            raise FeedError('Tree variant mismatch: ' + title + ' matches=' + str(len(matches)))
        return matches
    if len(group) == 1:
        return group
    # Accessory sizes and bundle heights are real variants, not tree duplicates.
    # Preserve every explicit edition and label it distinctly.
    chosen = []
    labels = set()
    for original in group:
        offer = copy.deepcopy(original)
        if kind == 'sets':
            height = offer_height(offer)
            if not height:
                raise FeedError('Bundle edition has no height: ' + title)
            clean = lambda n: format(n.normalize(), 'f').replace('.', ',')
            label = clean(height[0]) + '–' + clean(height[1]) + ' м'
        else:
            sizes = [(p.text or '').strip() for p in offer.findall('param')
                     if 'размер' in (p.get('name') or '').casefold()]
            if len(sizes) != 1 or not sizes[0]:
                raise FeedError('Accessory edition ambiguous size: ' + title)
            label = 'размер ' + sizes[0]
        if label in labels:
            raise FeedError('Duplicate variant label: ' + title)
        labels.add(label)
        offer.find('name').text = title + ' — ' + label
        chosen.append(offer)
    warnings.append(title + ': all ' + str(len(chosen)) + ' distinct editions included')
    return chosen


def convert(feed, sitemap, *, expected_min=1):
    src_root = xmlroot(feed)
    if src_root.tag != 'yml_catalog':
        raise FeedError('Expected yml_catalog')
    shop = src_root.find('shop')
    if shop is None or shop.find('offers') is None:
        raise FeedError('Missing shop/offers')
    if (shop.findtext('url') or '').rstrip('/') != 'https://' + SOURCE_HOST:
        raise FeedError('Unexpected shop URL')
    offers_el = shop.find('offers')
    original = list(offers_el.findall('offer'))
    if not original:
        raise FeedError('No offers')
    valid_paths = sitemap_product_paths(sitemap)
    by_path = {}
    for offer in original:
        p = _url_path(offer.findtext('url') or '')
        by_path.setdefault(p, []).append(offer)
    if set(by_path) != valid_paths:
        raise FeedError('Feed and sitemap disagree: onlyFeed=' + str(len(set(by_path)-valid_paths)) + ', onlySitemap=' + str(len(valid_paths-set(by_path))))
    if len(by_path) < expected_min:
        raise FeedError('Suspicious loss of products in source')
    categories = {x.get('id') for x in shop.findall('.//categories/category')}
    currencies = {x.get('id') for x in shop.findall('.//currencies/currency')}
    warnings = []
    selected = []
    for path, group in by_path.items():
        variants = choose(group, path, warnings)
        for picked_original in variants:
            picked = copy.deepcopy(picked_original)
            check_offer_fields(picked)
            if picked.findtext('categoryId') not in categories:
                raise FeedError('Unrecognized category in source: '+path)
            if picked.findtext('currencyId') not in currencies:
                raise FeedError('Unrecognized currency in source: '+path)
            uid = ROOT_PRODUCT_PATH.fullmatch(path).group(1)
            edition = picked.get('id') or ''
            if not edition.isdigit():
                raise FeedError('Invalid edition ID: ' + path)
            query = parse_qs(urlsplit(picked.findtext('url')).query)
            if query.get('editionuid') != [edition] and (len(group) > 1 or 'editionuid' in query):
                raise FeedError('Edition URL does not match offer ID: ' + path)
            picked.set('id', edition if len(variants) > 1 else uid)
            if len(variants) == 1:
                picked.attrib.pop('group_id', None)
            else:
                picked.set('group_id', uid)
            # Products can be ordered subject to confirmation; never transmit
            # placeholder stock quantities or override explicit unavailability.
            availability = picked.get('available')
            if availability not in (None, 'true', 'false'):
                raise FeedError('Invalid availability: ' + path)
            count = picked.find('count')
            if count is not None:
                if not (count.text or '').strip().isdigit():
                    raise FeedError('Invalid source stock: ' + path)
                if int(count.text) == 0:
                    availability = 'false'
                picked.remove(count)
            picked.set('available', availability or 'true')
            selected.append(picked)
    ids=[o.get('id') for o in selected]
    if len(ids) != len(set(ids)):
        raise FeedError('Duplicate offer IDs')
    for o in list(offers_el):
        offers_el.remove(o)
    for o in selected:
        offers_el.append(o)
    from zoneinfo import ZoneInfo
    src_root.set('date', datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds'))
    output = ET.tostring(src_root, encoding='utf-8', xml_declaration=True)
    # Self-check the generated document. Fails before touching output.
    check = xmlroot(output)
    final = check.findall('.//offers/offer')
    if (len(final) != len(selected)
            or len(final) != len(set(o.get('id') for o in final))
            or len(final) != len(set(o.findtext('url') for o in final))
            or {_url_path(o.findtext('url')) for o in final} != valid_paths):
        raise FeedError('Post-generation count/ID/URL mismatch')
    return output, {'inputOffers': len(original), 'inputProducts': len(by_path), 'outputOffers': len(selected), 'treeOffers': sum('/catalog/tree/' in o.findtext('url','') for o in selected), 'warnings': warnings}


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.feed-',suffix='.xml',dir=path.parent,delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(content)
            f.flush()
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', help='Source Tilda YML URL or local XML')
    parser.add_argument('--sitemap',help='Public product sitemap URL or local XML')
    parser.add_argument('--output',default='exports/yandex/catalog.yml')
    args=parser.parse_args()
    try:
        xml=load_bytes(args.source,SOURCE_URL)
        sitemap=load_bytes(args.sitemap,SITEMAP_URL)
        result,stats=convert(xml,sitemap,expected_min=44)
        atomic_write(args.output,result)
    except (FeedError, OSError, ValueError) as e:
        print('FAILED: existing feed left untouched: '+str(e),file=sys.stderr)
        return 1
    print('OK: '+str(Path(args.output).resolve()))
    print('STATISTICS: '+repr(stats))
    return 0


if __name__=='__main__':
    sys.exit(main())
