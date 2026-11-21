import unittest
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
        self.assertEqual(len(offers), 44)
        self.assertEqual(report['inputOffers'], 294)
        self.assertEqual(report['treeOffers'], 33)
        self.assertEqual(len({o.get('id') for o in offers}), 44)
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

    def test_accessory_and_bundle_defaults_identified(self):
        root, report=self.make()
        stand=next(o for o in root.findall('.//offer') if o.get('id')=='293103610592')
        self.assertIn('размер L',stand.findtext('name'))
        self.assertEqual(stand.findtext('price'),'4290.00')
        bundle=next(o for o in root.findall('.//offer') if o.get('id')=='927489765342')
        self.assertIn('0,8–1 м',bundle.findtext('name'))
        self.assertEqual(bundle.findtext('price'),'6990.00')
        self.assertEqual(len(report['warnings']),2)

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
