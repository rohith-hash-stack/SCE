# Change propagation from git history: sanity run

Descriptive only (a small sanity set): no significance test.

| tier | arm | tasks | callers found | tasks with every caller found | extra functions named | added lines kept | removed lines gone | new code parses |
|---|---|---|---|---|---|---|---|---|
| all | FILE | 20 | 8 / 36 | 4 | 22 | 0.56 | 0.56 | 8 / 8 |
| all | PRISM | 20 | 33 / 36 | 17 | 10 | 0.55 | 0.68 | 32 / 33 |
| all | PRISM_BREAKS | 20 | 32 / 36 | 16 | 5 | 0.55 | 0.67 | 31 / 32 |
| easy | FILE | 7 | 5 / 8 | 4 | 12 | 0.3 | 0.3 | 5 / 5 |
| easy | PRISM | 7 | 6 / 8 | 5 | 0 | 0.5 | 0.58 | 6 / 6 |
| easy | PRISM_BREAKS | 7 | 6 / 8 | 5 | 0 | 0.67 | 0.58 | 6 / 6 |
| medium | FILE | 7 | 0 / 7 | 0 | 6 | None | None | 0 / 0 |
| medium | PRISM | 7 | 6 / 7 | 6 | 5 | 0.21 | 0.74 | 6 / 6 |
| medium | PRISM_BREAKS | 7 | 5 / 7 | 5 | 3 | 0.05 | 0.69 | 5 / 5 |
| hard | FILE | 6 | 3 / 21 | 0 | 4 | 1.0 | 1.0 | 3 / 3 |
| hard | PRISM | 6 | 21 / 21 | 6 | 5 | 0.68 | 0.68 | 20 / 21 |
| hard | PRISM_BREAKS | 6 | 21 / 21 | 6 | 2 | 0.65 | 0.7 | 20 / 21 |

Per task:

| task | tier | repo | anchor | changed callers | FILE found | PRISM found | PRISM_BREAKS found |
|---|---|---|---|---|---|---|---|
| scrapy-forward-001 | easy | scrapy | `scrapy.contrib.feedexport.FeedExporter._get_exporter` | 1 | 0 | 0 | 0 |
| scrapy-forward-002 | easy | scrapy | `scrapy.commands.genspider.Command._genspider` | 1 | 1 | 1 | 1 |
| scrapy-forward-003 | easy | scrapy | `scrapy.trunk.scrapy.contrib.spiders.CrawlSpider._parse_wrapper` | 1 | 1 | 0 | 0 |
| scrapy-forward-004 | easy | scrapy | `scrapy.extensions.feedexport.FeedExporter._get_uri_params` | 2 | 2 | 2 | 2 |
| scrapy-forward-005 | easy | scrapy | `scrapy.spiders.sitemap.SitemapSpider._get_urls_from_sitemapindex` | 1 | 1 | 1 | 1 |
| scrapy-forward-006 | easy | scrapy | `scrapy.core.spidermw.SpiderMiddlewareManager.process_spider_output` | 1 | 0 | 1 | 1 |
| scrapy-forward-007 | easy | scrapy | `scrapy.mail.MailSender._sendmail` | 1 | 0 | 1 | 1 |
| scrapy-forward-008 | medium | scrapy | `scrapy.http.request.Request.replace` | 1 | 0 | 1 | 0 |
| scrapy-forward-009 | medium | scrapy | `scrapy.core.scheduler.Scheduler.next_request` | 1 | 0 | 1 | 1 |
| scrapy-forward-010 | medium | scrapy | `scrapy.utils.python.isbinarytext` | 1 | 0 | 1 | 1 |
| scrapy-forward-011 | medium | scrapy | `scrapy.shell.Shell.start` | 1 | 0 | 1 | 1 |
| scrapy-forward-012 | medium | scrapy | `scrapy.trunk.scrapy.contrib.pbcluster.master.manager.Broker.status_as_dict` | 1 | 0 | 1 | 1 |
| scrapy-forward-013 | medium | scrapy | `scrapy.core.downloader.middleware.DownloaderMiddlewareManager.download` | 1 | 0 | 1 | 1 |
| scrapy-forward-014 | medium | scrapy | `scrapy.settings.BaseSettings.getwithbase` | 1 | 0 | 0 | 0 |
| scrapy-forward-015 | hard | scrapy | `scrapy.core.engine.ExecutionEngine.next_request` | 6 | 1 | 6 | 6 |
| scrapy-forward-016 | hard | scrapy | `scrapyd.utils.get_spider_queues` | 2 | 0 | 2 | 2 |
| scrapy-forward-017 | hard | scrapy | `scrapy.utils.reactor.is_asyncio_reactor_installed` | 3 | 0 | 3 | 3 |
| scrapy-forward-018 | hard | scrapy | `scrapy.spider.manager.SpiderManager.asdict` | 5 | 1 | 5 | 5 |
| scrapy-forward-019 | hard | scrapy | `scrapy.utils.reactor.listen_tcp` | 2 | 0 | 2 | 2 |
| scrapy-forward-020 | hard | scrapy | `scrapy.core.engine.ExecutionEngine.start_async` | 3 | 1 | 3 | 3 |
