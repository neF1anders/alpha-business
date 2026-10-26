"""News -> event cluster rule: one banner per scheduled release, other news only merge exact reprints."""
from datetime import datetime, timedelta, timezone
import unittest

from retrieval.domain.headlines import event_cluster_id

DECISION_DAY = datetime(2026, 10, 23, 10, 30, tzinfo=timezone.utc)


class EventClusterTests(unittest.TestCase):
    def test_scheduled_release_articles_share_the_moscow_day(self):
        decision = event_cluster_id('Банк России принял решение сохранить ключевую ставку на уровне 14,00%', DECISION_DAY)
        short = event_cluster_id('ЦБ сохранил ключевую ставку на уровне 14%', DECISION_DAY + timedelta(hours=2))
        comment = event_cluster_id('ЦБ не спешит // Пауза в снижении ключевой ставки может затянуться',
                                   DECISION_DAY + timedelta(hours=10))
        self.assertEqual(decision, 'event:policy_rate:2026-10-23')
        statement = event_cluster_id('Заявление Председателя Банка России Эльвиры Набиуллиной по итогам заседания '
                                     'Совета директоров Банка России 23 октября 2026 года', DECISION_DAY)
        self.assertEqual({decision, short, comment, statement}, {decision})
        # 23:30 UTC is already the next day in Moscow: a later release is another event.
        self.assertEqual(event_cluster_id('Ключевая ставка: реакция рынка', DECISION_DAY + timedelta(hours=13)),
                         'event:policy_rate:2026-10-24')
        self.assertEqual(event_cluster_id('Росстат зафиксировал недельное снижение потребительских цен', DECISION_DAY),
                         'event:inflation:2026-10-23')

    def test_other_news_merge_only_equal_headlines(self):
        gazprom = event_cluster_id('«Газпром» не выплатит дивиденды за 2025 год', DECISION_DAY)
        mechel = event_cluster_id('«Мечел» не выплатит дивиденды за 2025 год', DECISION_DAY)
        self.assertNotEqual(gazprom, mechel)
        self.assertEqual(event_cluster_id('«ГАЗПРОМ» не выплатит дивиденды за 2025 год!', DECISION_DAY + timedelta(hours=1)),
                         gazprom)
        self.assertTrue(gazprom.startswith('headline:'))


if __name__ == '__main__':
    unittest.main()
