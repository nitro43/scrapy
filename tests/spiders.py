"""
Some spiders used for testing and benchmarking
"""
import asyncio
import time
from urllib.parse import urlencode

from twisted.internet import defer

from scrapy import signals
from scrapy.exceptions import StopDownload
from scrapy.http import Request
from scrapy.item import Item
from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import Spider
from scrapy.spiders.crawl import CrawlSpider, Rule
from scrapy.utils.test import get_from_asyncio_queue


class MockServerSpider(Spider):
    def __init__(self, mockserver=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mockserver = mockserver


class MetaSpider(MockServerSpider):

    name = 'meta'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.meta = {}

    def closed(self, reason):
        self.meta['close_reason'] = reason


class FollowAllSpider(MetaSpider):

    name = 'follow'
    link_extractor = LinkExtractor()

    def __init__(self, total=10, show=20, order="rand", maxlatency=0.0, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.urls_visited = []
        self.times = []
        qargs = {'total': total, 'show': show, 'order': order, 'maxlatency': maxlatency}
        url = self.mockserver.url("/follow?%s" % urlencode(qargs, doseq=1))
        self.start_urls = [url]

    def parse(self, response):
        self.urls_visited.append(response.url)
        self.times.append(time.time())
        for link in self.link_extractor.extract_links(response):
            yield Request(link.url, callback=self.parse)


class DelaySpider(MetaSpider):

    name = 'delay'

    def __init__(self, n=1, b=0, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.n = n
        self.b = b
        self.t1 = self.t2 = self.t2_err = 0

    def start_requests(self):
        self.t1 = time.time()
        url = self.mockserver.url("/delay?n=%s&b=%s" % (self.n, self.b))
        yield Request(url, callback=self.parse, errback=self.errback)

    def parse(self, response):
        self.t2 = time.time()

    def errback(self, failure):
        self.t2_err = time.time()


class SimpleSpider(MetaSpider):

    name = 'simple'

    def __init__(self, url="http://localhost:8998", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = [url]

    def parse(self, response):
        self.logger.info("Got response %d" % response.status)


class AsyncDefSpider(SimpleSpider):

    name = 'asyncdef'

    async def parse(self, response):
        await defer.succeed(42)
        self.logger.info("Got response %d" % response.status)


class AsyncDefAsyncioSpider(SimpleSpider):

    name = 'asyncdef_asyncio'

    async def parse(self, response):
        await asyncio.sleep(0.2)
        status = await get_from_asyncio_queue(response.status)
        self.logger.info("Got response %d" % status)


class AsyncDefAsyncioReturnSpider(SimpleSpider):

    name = 'asyncdef_asyncio_return'

    async def parse(self, response):
        await asyncio.sleep(0.2)
        status = await get_from_asyncio_queue(response.status)
        self.logger.info("Got response %d" % status)
        return [{'id': 1}, {'id': 2}]


class AsyncDefAsyncioReturnSingleElementSpider(SimpleSpider):

    name = "asyncdef_asyncio_return_single_element"

    async def parse(self, response):
        await asyncio.sleep(0.1)
        status = await get_from_asyncio_queue(response.status)
        self.logger.info("Got response %d" % status)
        return {"foo": 42}


class AsyncDefAsyncioReqsReturnSpider(SimpleSpider):

    name = 'asyncdef_asyncio_reqs_return'

    async def parse(self, response):
        await asyncio.sleep(0.2)
        req_id = response.meta.get('req_id', 0)
        status = await get_from_asyncio_queue(response.status)
        self.logger.info("Got response %d, req_id %d" % (status, req_id))
        if req_id > 0:
            return
        reqs = []
        for i in range(1, 3):
            req = Request(self.start_urls[0], dont_filter=True, meta={'req_id': i})
            reqs.append(req)
        return reqs


class ItemSpider(FollowAllSpider):

    name = 'item'

    def parse(self, response):
        for request in super().parse(response):
            yield request
            yield Item()
            yield {}


class DefaultError(Exception):
    pass


class ErrorSpider(FollowAllSpider):

    name = 'error'
    exception_cls = DefaultError

    def raise_exception(self):
        raise self.exception_cls('Expected exception')

    def parse(self, response):
        for request in super().parse(response):
            yield request
            self.raise_exception()


class YieldingRequestsSpider(FollowAllSpider):
    number_of_start_requests = 10

    def start_requests(self):
        for s in range(self.number_of_start_requests):
            qargs = {'total': 10, 'seed': s}
            url = self.mockserver.url("/follow?%s") % urlencode(qargs, doseq=1)
            yield Request(url, meta={'seed': s})


class BrokenStartRequestsSpider(FollowAllSpider):
    fail_before_yield = True
    fail_yielding = True

    def start_requests(self):
        if self.fail_before_yield:
            1 / 0

        for r in super().start_requests():
            yield r
            if self.fail_yielding:
                2 / 0


class SingleRequestSpider(MetaSpider):

    seed = None
    callback_func = None
    errback_func = None

    def start_requests(self):
        if isinstance(self.seed, Request):
            yield self.seed.replace(callback=self.parse, errback=self.on_error)
        else:
            yield Request(self.seed, callback=self.parse, errback=self.on_error)

    def parse(self, response):
        self.meta.setdefault('responses', []).append(response)
        if callable(self.callback_func):
            return self.callback_func(response)
        if 'next' in response.meta:
            return response.meta['next']

    def on_error(self, failure):
        self.meta['failure'] = failure
        if callable(self.errback_func):
            return self.errback_func(failure)


class DuplicateStartRequestsSpider(MockServerSpider):
    dont_filter = True
    name = 'duplicatestartrequests'
    distinct_urls = 2
    dupe_factor = 3

    def start_requests(self):
        for i in range(0, self.distinct_urls):
            for j in range(0, self.dupe_factor):
                url = self.mockserver.url("/echo?headers=1&body=test%d" % i)
                yield Request(url, dont_filter=self.dont_filter)

    def __init__(self, url="http://localhost:8998", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.visited = 0

    def parse(self, response):
        self.visited += 1


class CrawlSpiderWithParseMethod(MockServerSpider, CrawlSpider):
    """
    A CrawlSpider which overrides the 'parse' method
    """
    name = 'crawl_spider_with_parse_method'
    custom_settings = {
        'RETRY_HTTP_CODES': [],  # no need to retry
    }
    rules = (
        Rule(LinkExtractor(), callback='parse', follow=True),
    )

    def start_requests(self):
        test_body = b"""
        <html>
            <head><title>Page title<title></head>
            <body>
                <p><a href="/status?n=200">Item 200</a></p>  <!-- callback -->
                <p><a href="/status?n=201">Item 201</a></p>  <!-- callback -->
            </body>
        </html>
        """
        url = self.mockserver.url("/alpayload")
        yield Request(url, method="POST", body=test_body)

    def parse(self, response, foo=None):
        self.logger.info('[parse] status %i (foo: %s)', response.status, foo)
        yield Request(self.mockserver.url("/status?n=202"), self.parse, cb_kwargs={"foo": "bar"})


class CrawlSpiderWithErrback(CrawlSpiderWithParseMethod):
    name = 'crawl_spider_with_errback'
    rules = (
        Rule(LinkExtractor(), callback='parse', errback='errback', follow=True),
    )

    def start_requests(self):
        test_body = b"""
        <html>
            <head><title>Page title<title></head>
            <body>
                <p><a href="/status?n=200">Item 200</a></p>  <!-- callback -->
                <p><a href="/status?n=201">Item 201</a></p>  <!-- callback -->
                <p><a href="/status?n=404">Item 404</a></p>  <!-- errback -->
                <p><a href="/status?n=500">Item 500</a></p>  <!-- errback -->
                <p><a href="/status?n=501">Item 501</a></p>  <!-- errback -->
            </body>
        </html>
        """
        url = self.mockserver.url("/alpayload")
        yield Request(url, method="POST", body=test_body)

    def errback(self, failure):
        self.logger.info('[errback] status %i', failure.value.response.status)


class BytesReceivedCallbackSpider(MetaSpider):

    full_response_length = 2**18

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.bytes_received, signals.bytes_received)
        return spider

    def start_requests(self):
        body = b"a" * self.full_response_length
        url = self.mockserver.url("/alpayload")
        yield Request(url, method="POST", body=body, errback=self.errback)

    def parse(self, response):
        self.meta["response"] = response

    def errback(self, failure):
        self.meta["failure"] = failure

    def bytes_received(self, data, request, spider):
        self.meta["bytes_received"] = data
        raise StopDownload(fail=False)


class BytesReceivedErrbackSpider(BytesReceivedCallbackSpider):

    def bytes_received(self, data, request, spider):
        self.meta["bytes_received"] = data
        raise StopDownload(fail=True)
