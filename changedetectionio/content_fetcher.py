from abc import ABC, abstractmethod
import chardet
import os
from selenium import webdriver
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.common.proxy import Proxy as SeleniumProxy
from selenium.common.exceptions import WebDriverException
import requests
import time
import urllib3.exceptions


class EmptyReply(Exception):
    def __init__(self, status_code, url):
        # Set this so we can use it in other parts of the app
        self.status_code = status_code
        self.url = url
        return

    pass

class Fetcher():
    error = None
    status_code = None
    content = None
    headers = None

    fetcher_description ="No description"

    @abstractmethod
    def get_error(self):
        return self.error

    @abstractmethod
    def run(self,
            url,
            timeout,
            request_headers,
            request_body,
            request_method,
            ignore_status_codes=False):
        # Should set self.error, self.status_code and self.content
        pass

    @abstractmethod
    def quit(self):
        return

    @abstractmethod
    def screenshot(self):
        return

    @abstractmethod
    def get_last_status_code(self):
        return self.status_code

    @abstractmethod
    # Return true/false if this checker is ready to run, in the case it needs todo some special config check etc
    def is_ready(self):
        return True

#   Maybe for the future, each fetcher provides its own diff output, could be used for text, image
#   the current one would return javascript output (as we use JS to generate the diff)
#
#   Returns tuple(mime_type, stream)
#    @abstractmethod
#    def return_diff(self, stream_a, stream_b):
#        return

def available_fetchers():
        import inspect
        from changedetectionio import content_fetcher
        p=[]
        for name, obj in inspect.getmembers(content_fetcher):
            if inspect.isclass(obj):
                # @todo html_ is maybe better as fetcher_ or something
                # In this case, make sure to edit the default one in store.py and fetch_site_status.py
                if "html_" in name:
                    t=tuple([name,obj.fetcher_description])
                    p.append(t)

        return p

class html_webdriver(Fetcher):
    if os.getenv("WEBDRIVER_URL"):
        fetcher_description = "WebDriver Chrome/Javascript via '{}'".format(os.getenv("WEBDRIVER_URL"))
    else:
        fetcher_description = "WebDriver Chrome/Javascript"

    command_executor = ''

    # Configs for Proxy setup
    # In the ENV vars, is prefixed with "webdriver_", so it is for example "webdriver_sslProxy"
    selenium_proxy_settings_mappings = ['proxyType', 'ftpProxy', 'httpProxy', 'noProxy',
                                        'proxyAutoconfigUrl', 'sslProxy', 'autodetect',
                                        'socksProxy', 'socksVersion', 'socksUsername', 'socksPassword']



    proxy=None

    def __init__(self):
        # .strip('"') is going to save someone a lot of time when they accidently wrap the env value
        self.command_executor = os.getenv("WEBDRIVER_URL", 'http://browser-chrome:4444/wd/hub').strip('"')

        # If any proxy settings are enabled, then we should setup the proxy object
        proxy_args = {}
        for k in self.selenium_proxy_settings_mappings:
            v = os.getenv('webdriver_' + k, False)
            if v:
                proxy_args[k] = v.strip('"')

        if proxy_args:
            self.proxy = SeleniumProxy(raw=proxy_args)

    def run(self,
            url,
            timeout,
            request_headers,
            request_body,
            request_method,
            ignore_status_codes=False):

        # request_body, request_method unused for now, until some magic in the future happens.

        # check env for WEBDRIVER_URL
        self.driver = webdriver.Remote(
            command_executor=self.command_executor,
            desired_capabilities=DesiredCapabilities.CHROME,
            proxy=self.proxy)

        try:
            self.driver.get(url)
        except WebDriverException as e:
            # Be sure we close the session window
            self.quit()
            raise

        # @todo - how to check this? is it possible?
        self.status_code = 200
        # @todo somehow we should try to get this working for WebDriver
        # raise EmptyReply(url=url, status_code=r.status_code)

        # @todo - dom wait loaded?
        time.sleep(int(os.getenv("WEBDRIVER_DELAY_BEFORE_CONTENT_READY", 5)))
        self.content = self.driver.page_source
        self.headers = {}

    def screenshot(self):
        return self.driver.get_screenshot_as_png()

    # Does the connection to the webdriver work? run a test connection.
    def is_ready(self):
        from selenium import webdriver
        from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
        from selenium.common.exceptions import WebDriverException

        self.driver = webdriver.Remote(
            command_executor=self.command_executor,
            desired_capabilities=DesiredCapabilities.CHROME)

        # driver.quit() seems to cause better exceptions
        self.quit()
        return True

    def quit(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                print("Exception in chrome shutdown/quit" + str(e))

# "html_requests" is listed as the default fetcher in store.py!
class html_requests(Fetcher):
    fetcher_description = "Basic fast Plaintext/HTTP Client"

    def run(self,
            url,
            timeout,
            request_headers,
            request_body,
            request_method,
            ignore_status_codes=False):

        r = requests.request(method=request_method,
                         data=request_body,
                         url=url,
                         headers=request_headers,
                         timeout=timeout,
                         verify=False)

        # If the response did not tell us what encoding format to expect, Then use chardet to override what `requests` thinks.
        # For example - some sites don't tell us it's utf-8, but return utf-8 content
        # This seems to not occur when using webdriver/selenium, it seems to detect the text encoding more reliably.
        # https://github.com/psf/requests/issues/1604 good info about requests encoding detection
        if not r.headers.get('content-type') or not 'charset=' in r.headers.get('content-type'):
            encoding = chardet.detect(r.content)['encoding']
            if encoding:
                r.encoding = encoding

        # @todo test this
        # @todo maybe you really want to test zero-byte return pages?
        if (not ignore_status_codes and not r) or not r.content or not len(r.content):
            raise EmptyReply(url=url, status_code=r.status_code)

        self.status_code = r.status_code
        self.content = r.text
        self.headers = r.headers

