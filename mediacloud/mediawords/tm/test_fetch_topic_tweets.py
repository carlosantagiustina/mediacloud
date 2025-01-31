"""test fetch_topic_tweets."""

import datetime
import os
import random
import re
import unittest

from mediawords.db import DatabaseHandler
from mediawords.test.test_database import TestDatabaseWithSchemaTestCase
import mediawords.test.db
import mediawords.test.db.create
import mediawords.tm.fetch_topic_tweets as ftt
import mediawords.util.paths
import mediawords.util.twitter
from mediawords.util.log import create_logger
from mediawords.util.parse_json import decode_json

logger = create_logger(__name__)

# this is an estimate of the number of tweets per day included in the ch-posts-date.json files
# this should not be edited other than to provide a better estimate
MAX_MOCK_TWEETS_PER_DAY = 400

# number of mocked tweets to return for each day -- edit this up to MAX_MOCK_TWEETS_PER_DAY to change the size
# of the testing set
MOCK_TWEETS_PER_DAY = 25

# ratios of tweets to urls and users.  these can be edited to derive the desired ratios for testing
MOCK_TWEETS_PER_URL = 4
MOCK_TWEETS_PER_USER = 20

# max number of days difference between start and end dates for test topic -- this can edited for the desired
# number of days for the local test
LOCAL_DATE_RANGE = 4

# these should not be edited
NUM_MOCK_URLS = int((LOCAL_DATE_RANGE * MOCK_TWEETS_PER_DAY) / MOCK_TWEETS_PER_URL)
NUM_MOCK_USERS = int((LOCAL_DATE_RANGE * MOCK_TWEETS_PER_DAY) / MOCK_TWEETS_PER_USER)

# arbitrary tests for tweets / users so that we don't have to use fixtures
MIN_TEST_CH_POSTS = 500
MIN_TEST_TWEET_LENGTH = 10
MIN_TEST_TWITTER_USER_LENGTH = 3

# test crimson hexagon monitor id
TEST_MONITOR_ID = 4667493813


def mock_fetch_meta_tweets_from_ch(query: str, day: datetime.datetime) -> dict:
    """
    Return a mock ch response to the posts end point.

    Generate the mock response by sending back data from a consistent but semirandom selection of
    ch-posts-2016-01-0[12345].json.
    """
    assert MOCK_TWEETS_PER_DAY <= MAX_MOCK_TWEETS_PER_DAY

    test_path = mediawords.util.paths.mc_root_path() + '/mediacloud/test-data/ch/'
    filename = test_path + "ch-posts-" + day.strftime('%Y-%m-%d') + '.json'
    with open(filename, 'r', encoding='utf-8') as fh:
        json = fh.read()

    data = dict(decode_json(json))

    assert 'posts' in data
    assert len(data['posts']) >= MOCK_TWEETS_PER_DAY

    data['posts'] = data['posts'][0:MOCK_TWEETS_PER_DAY]

    # replace tweets with the epoch of the start date so that we can infer the date of each tweet in
    # tweet_urler_lookup below
    i = 0
    for ch_post in data['posts']:
        ch_post['url'] = re.sub(r'status/(\d+)/', '/status/' + str(i), ch_post['url'])
        i += 1

    meta_tweets = data['posts']
    for mt in meta_tweets:
        mt['tweet_id'] = mediawords.tm.fetch_topic_tweets.get_tweet_id_from_url(mt['url'])

    return meta_tweets


def mock_fetch_100_tweets(ids: list) -> list:
    """Return mocked test tweets."""
    num_errors = (3 if (len(ids) > 10) else 0)

    # simulate twitter not being able to find some ids, which is typical
    for i in range(num_errors):
        ids.pop()

    tweets = []
    for tweet_id in ids:
        # restrict url and user ids to desired number
        # include randomness so that the urls and users are not nearly collated
        url_id = int(random.randint(1, int(tweet_id))) % NUM_MOCK_URLS
        user_id = int(random.randint(1, int(tweet_id))) % NUM_MOCK_USERS

        test_url = "http://test.host/tweet_url?id=" + str(url_id)

        # all we use is id, text, and created_by, so just test for those
        tweets.append(
            {
                'id': tweet_id,
                'text': "sample tweet for id id",
                'created_at': '2018-12-13',
                'user': {'screen_name': "user-" + str(user_id)},
                'entities': {'urls': [{'expanded_url': test_url}]}
            })

    return tweets


def get_test_date_range() -> tuple:
    """Return either 2016-01-01 - 2016-01-01 + LOCAL_DATE_RANGE - 1 for local tests."""
    end_date = datetime.datetime(year=2016, month=1, day=1) + datetime.timedelta(days=LOCAL_DATE_RANGE)
    return ('2016-01-01', end_date.strftime('%Y-%m-%d'))


def validate_topic_tweets(db: DatabaseHandler, topic_tweet_day: dict) -> None:
    """Validate that the topic tweets belonging to the given topic_tweet_day have all of the current data."""
    topic_tweets = db.query(
        "select * from topic_tweets where topic_tweet_days_id = %(a)s",
        {'a': topic_tweet_day['topic_tweet_days_id']}
    ).hashes()

    # fetch_topic_tweets should have set num_tweets to the total number of tweets
    assert len(topic_tweets) > 0
    assert len(topic_tweets) == topic_tweet_day['num_tweets']

    for topic_tweet in topic_tweets:
        tweet_data = dict(decode_json(topic_tweet['data']))

        # random field that should be coming from twitter
        assert 'assignedCategoryId' in tweet_data

        expected_date = datetime.datetime.strptime(tweet_data['tweet']['created_at'], '%Y-%m-%d')
        got_date = datetime.datetime.strptime(topic_tweet['publish_date'], '%Y-%m-%d 00:00:00')
        assert got_date == expected_date

        assert topic_tweet['content'] == tweet_data['tweet']['text']


def validate_topic_tweet_urls(db: DatabaseHandler, topic: dict) -> None:
    """Validate that topic_tweet_urls match what's in the tweet JSON data as saved in topic_tweets."""
    topic_tweets = db.query(
        """
        select *
            from topic_tweets tt
                join topic_tweet_days ttd using (topic_tweet_days_id)
            where
                ttd.topics_id = %(a)s
        """,
        {'a': topic['topics_id']}).hashes()

    expected_num_urls = 0
    for topic_tweet in topic_tweets:
        data = dict(decode_json(topic_tweet['data']))
        expected_num_urls += len(data['tweet']['entities']['urls'])

    # first sanity check to make sure we got some urls
    num_urls = db.query("select count(*) from topic_tweet_urls").flat()[0]
    assert num_urls == expected_num_urls

    total_json_urls = 0
    for topic_tweet in topic_tweets:

        ch_post = dict(decode_json(topic_tweet['data']))
        expected_urls = [x['expanded_url'] for x in ch_post['tweet']['entities']['urls']]
        total_json_urls += len(expected_urls)

        for expected_url in expected_urls:
            got_url = db.query("select * from topic_tweet_urls where url = %(a)s", {'a': expected_url}).hash()
            assert got_url is not None

    assert total_json_urls == num_urls


def test_tweet_matches_pattern() -> None:
    """Test _post_matches_pattern()."""
    assert not ftt._tweet_matches_pattern({'topics_id': 1, 'pattern': 'foo'}, {'tweet': {'text': 'bar'}})
    assert ftt._tweet_matches_pattern({'topics_id': 1, 'pattern': 'foo'}, {'tweet': {'text': 'foo bar'}})
    assert ftt._tweet_matches_pattern({'topics_id': 1, 'pattern': 'foo'}, {'tweet': {'text': 'bar foo'}})
    assert not ftt._tweet_matches_pattern({'topics_id': 1, 'pattern': 'foo'}, {})


class TestFetchTopicTweets(TestDatabaseWithSchemaTestCase):
    """Run database tests."""

    def test_fetch_topic_tweets(self) -> None:
        """Run fetch_topic_tweet tests with test database."""
        db = self.db()
        topic = mediawords.test.db.create.create_test_topic(db, 'test')

        topic = db.update_by_id('topics', topic['topics_id'], {'pattern': '.*'})

        test_dates = get_test_date_range()
        topic['start_date'] = test_dates[0]
        topic['end_date'] = test_dates[1]
        db.update_by_id('topics', topic['topics_id'], topic)

        tsq = {
            'topics_id': topic['topics_id'],
            'platform': 'twitter',
            'source': 'crimson_hexagon',
            'query': 123456
        }
        db.create('topic_seed_queries', tsq)

        db.update_by_id('topics', topic['topics_id'], {'platform': 'twitter'})

        mediawords.tm.fetch_topic_tweets.fetch_meta_tweets_from_ch = mock_fetch_meta_tweets_from_ch
        mediawords.tm.fetch_topic_tweets.fetch_100_tweets = mock_fetch_100_tweets
        ftt.fetch_topic_tweets(db, topic['topics_id'])

        topic_tweet_days = db.query("select * from topic_tweet_days").hashes()
        assert len(topic_tweet_days) == LOCAL_DATE_RANGE + 1

        start_date = datetime.datetime.strptime(topic['start_date'], '%Y-%m-%d')
        test_days = [start_date + datetime.timedelta(days=x) for x in range(0, LOCAL_DATE_RANGE)]
        for d in test_days:
            topic_tweet_day = db.query(
                "select * from topic_tweet_days where topics_id = %(a)s and day = %(b)s",
                {'a': topic['topics_id'], 'b': d}
            ).hash()
            assert topic_tweet_day is not None

            validate_topic_tweets(db, topic_tweet_day)

        validate_topic_tweet_urls(db, topic)

    def _test_remote_integration(self, source, query, day) -> None:
        """Run santity test on remote apis."""
        db = self.db()

        topic = mediawords.test.db.create.create_test_topic(db, "test_remote_integration")

        tsq = {
            'topics_id': topic['topics_id'],
            'platform': 'twitter',
            'source': source,
            'query': query
        }
        db.create('topic_seed_queries', tsq)

        topic['platform'] = 'twitter'
        topic['pattern'] = '.*'
        topic['start_date'] = day
        topic['end_date'] = day
        db.update_by_id('topics', topic['topics_id'], topic)

        # only fetch 200 tweets to make test quicker
        max_tweets = 200
        ftt.fetch_topic_tweets(db, topic['topics_id'], max_tweets)

        # ttd_day = datetime.datetime(year=2016, month=1, day=1)

        # meta_tweets = ftt.fetch_meta_tweets(db, topic, ttd_day)
        # ttd = ftt._add_topic_tweet_single_day(db, topic, len(meta_tweets), ttd_day)

        # max_tweets = 100
        # ftt._fetch_tweets_for_day(db, ttd, meta_tweets, max_tweets=max_tweets)

        got_tts = db.query("select * from topic_tweets").hashes()

        # for old ch monitors, lots of the tweets may be deleted
        assert len(got_tts) > max_tweets / 10

        assert len(got_tts[0]['content']) > MIN_TEST_TWEET_LENGTH
        assert len(got_tts[0]['twitter_user']) > MIN_TEST_TWITTER_USER_LENGTH

    @unittest.skipUnless(os.environ.get('MC_REMOTE_TESTS', False), "remote tests")
    def test_ch_remote_integration(self) -> None:
        """Test ch remote integration."""
        self._test_remote_integration('crimson_hexagon', TEST_MONITOR_ID, '2016-01-01')

    @unittest.skipUnless(os.environ.get('MC_REMOTE_TESTS', False), "remote tests")
    def test_archive_remote_integration(self) -> None:
        """Test archive.org remote integration."""
        self._test_remote_integration('archive_org', 'harvard', '2019-01-01')
