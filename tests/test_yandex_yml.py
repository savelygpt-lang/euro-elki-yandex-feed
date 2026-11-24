import unittest
from unittest.mock import patch, MagicMock
import copy
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.parse import urlsplit

from scripts import build_yandex_yml as feed

ROOT = Path(__file__).resolve().parent / 'fixtures' / 'yandex_feed'


class YandexYMLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'source.xml').read_bytes()
        cls.sitemap = (ROOT / 'sitemap.xml').read_bytes()

    def make(self, source=None, sitemap=None):
        result, report = feed.convert(self.source if source is None else source,
                                      self.sitemap if sitemap is None else sitemap)
        return ET.fromstring(result), report

    def test_all_unique_active_product_urls(self):
        root, report = self.make()
        offers = root.findall('.//offers/offer')
        self.assertEqual(len(offers), 54)
        self.assertEqual(report['inputOffers'], 294)
        self.assertEqual(report['treeOffers'], 33)
        self.assertEqual(len({o.get('id') for o in offers}), 54)
        self.assertEqual(len({o.findtext('url') for o in offers}), 54)
        self.assertEqual(len({urlsplit(o.findtext('url')).path for o in offers}), 44)
        self.assertEqual({urlsplit(o.findtext('url')).path for o in offers},
                         feed.sitemap_product_paths(self.sitemap))

    def test_each_tree_has_matching_height_and_root_name(self):
        root, _ = self.make()
        trees = [o for o in root.findall('.//offer') if '/catalog/tree/' in o.findtext('url','')]
        self.assertEqual(len(trees), 33)
        self.assertTrue(all(feed.title_range(o.findtext('name')) == feed.offer_height(o) for o in trees))
        self.assertTrue(all(o.findtext('price') and o.get('available')=='true' for o in trees))

    def test_price_comes_from_exact_upstream_edition(self):
        original = ET.fromstring(self.source)
        original_prices = {o.get('id'): o.findtext('price') for o in original.findall('.//offers/offer')}
        root, _ = self.make()
        for offer in root.findall('.//offers/offer'):
            edition_uid = (offer.findtext('url','').split('?editionuid=')[-1]
                           if '?editionuid=' in offer.findtext('url','') else None)
            if edition_uid:
                self.assertEqual(offer.findtext('price'), original_prices[edition_uid])

    def test_placeholders_removed_without_rewriting_prices(self):
        root,_=self.make()
        for o in root.findall('.//offers/offer'):
            self.assertIsNone(o.find('count'))
            self.assertEqual(o.get('available'), 'true')
            self.assertTrue(o.findtext('picture','').startswith('https://'))
            self.assertIn(o.findtext('currencyId'), ('RUR','RUB'))

    def test_all_accessory_and_bundle_variants_identified(self):
        root, report=self.make()
        stands=[o for o in root.findall('.//offer') if o.get('group_id')=='293103610592']
        self.assertEqual(len(stands), 3)
        self.assertEqual({o.findtext('price') for o in stands}, {'4290.00','4990.00','6490.00'})
        self.assertTrue(all('размер ' in o.findtext('name') for o in stands))
        bundles=[o for o in root.findall('.//offer') if o.get('group_id')=='927489765342']
        self.assertEqual(len(bundles), 9)
        self.assertIn('0,8–1 м', bundles[0].findtext('name'))
        self.assertEqual(bundles[0].findtext('price'),'6990.00')
        self.assertEqual(len(report['warnings']),2)

    def test_tilda_root_accessory_and_set_paths(self):
        def paths(data):
            return data.replace(b'/catalog/accessories/tproduct/', b'/tproduct/').replace(b'/catalog/sets/tproduct/', b'/tproduct/')
        root, report = self.make(source=paths(self.source), sitemap=paths(self.sitemap))
        offers=root.findall('.//offers/offer')
        self.assertEqual(len(offers),54)
        self.assertEqual(report['inputProducts'],44)
        self.assertEqual(sum(urlsplit(o.findtext('url')).path.startswith('/tproduct/') for o in offers),21)

    def test_explicit_unavailability_and_zero_stock_preserved(self):
        src=ET.fromstring(self.source)
        target=next(o for o in src.findall('.//offer') if '293103610592-' in o.findtext('url',''))
        eid=target.get('id')
        target.set('available','false')
        root,_=self.make(source=ET.tostring(src,encoding='utf-8'))
        self.assertEqual(next(o for o in root.findall('.//offer') if o.get('id')==eid).get('available'),'false')
        target.attrib.pop('available')
        target.find('count').text='0'
        root,_=self.make(source=ET.tostring(src,encoding='utf-8'))
        self.assertEqual(next(o for o in root.findall('.//offer') if o.get('id')==eid).get('available'),'false')

    def test_wrong_edition_url_fails(self):
        src=ET.fromstring(self.source)
        target=next(o for o in src.findall('.//offer') if '293103610592-' in o.findtext('url',''))
        target.find('url').text=target.findtext('url').split('?')[0]+'?editionuid=999'
        with self.assertRaisesRegex(feed.FeedError,'Edition URL'):
            self.make(source=ET.tostring(src,encoding='utf-8'))

    def test_missing_selected_height_rejects_all(self):
        src=ET.fromstring(self.source)
        offers=src.find('.//offers')
        target=next(o for o in offers.findall('offer') if
                    o.findtext('name')=='Пихта Фразера 1.75 - 2 метра'
                    and feed.offer_height(o)==(feed.Decimal('1.75'),feed.Decimal('2')))
        offers.remove(target)
        with self.assertRaisesRegex(feed.FeedError,'variant mismatch'):
            self.make(source=ET.tostring(src,encoding='utf-8'))

    def test_price_zero_rejects(self):
        src=ET.fromstring(self.source)
        target=next(o for o in src.findall('.//offers/offer') if
                    o.findtext('name')=='Пихта Фразера 1.75 - 2 метра'
                    and feed.offer_height(o)==(feed.Decimal('1.75'),feed.Decimal('2')))
        target.find('price').text='0'
        with self.assertRaisesRegex(feed.FeedError,'invalid price'):
            self.make(source=ET.tostring(src,encoding='utf-8'))

    def test_missing_product_root_fails_sitemap_validation(self):
        src=ET.fromstring(self.source)
        offers=src.find('.//offers')
        p='/catalog/tree/tproduct/815466556572-'
        for o in list(offers):
            if p in o.findtext('url',''):
                offers.remove(o)
        with self.assertRaisesRegex(feed.FeedError,'sitemap disagree'):
            self.make(source=ET.tostring(src,encoding='utf-8'))

    def test_source_retries_transient_gateway_error(self):
        response=MagicMock()
        response.__enter__.return_value=response
        response.geturl.return_value=feed.SOURCE_URL
        response.status=200
        response.read.return_value=b'<xml/>'
        error=feed.HTTPError(feed.SOURCE_URL,504,'Gateway Timeout',None,None)
        with patch.object(feed,'urlopen',side_effect=[error,response]) as call, patch.object(feed.time,'sleep') as sleep:
            self.assertEqual(feed.load_bytes(None,feed.SOURCE_URL),b'<xml/>')
            self.assertEqual(call.call_count,2)
            sleep.assert_called_once_with(3)

    def test_source_permanent_error_is_not_retried(self):
        error=feed.HTTPError(feed.SOURCE_URL,404,'Not Found',None,None)
        with patch.object(feed,'urlopen',side_effect=error) as call, patch.object(feed.time,'sleep') as sleep:
            with self.assertRaisesRegex(feed.FeedError,'404'):
                feed.load_bytes(None,feed.SOURCE_URL)
            self.assertEqual(call.call_count,1)
            sleep.assert_not_called()

    def test_broken_xml_dtd_rejected(self):
        with self.assertRaisesRegex(feed.FeedError,'DTD'):
            self.make(source=b'<!DOCTYPE yml_catalog><yml_catalog/>')

    def test_atomic_output_last_good_remains(self):
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'feed.xml'
            feed.atomic_write(dest,b'OLD_VALID_FEED')
            invalid=ET.fromstring(self.source)
            invalid.find('.//offers/offer/price').text='0'
            with self.assertRaises(feed.FeedError):
                data,_=feed.convert(ET.tostring(invalid,encoding='utf-8'),self.sitemap)
                feed.atomic_write(dest,data)
            self.assertEqual(dest.read_bytes(),b'OLD_VALID_FEED')


if __name__ == '__main__':
    unittest.main()
