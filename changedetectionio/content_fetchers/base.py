import os
from abc import abstractmethod
from loguru import logger

from changedetectionio.content_fetchers import BrowserStepsStepException


def manage_user_agent(headers, current_ua=''):
    """
    Basic setting of user-agent

    NOTE!!!!!! The service that does the actual Chrome fetching should handle any anti-robot techniques
    THERE ARE MANY WAYS THAT IT CAN BE DETECTED AS A ROBOT!!
    This does not take care of
    - Scraping of 'navigator' (platform, productSub, vendor, oscpu etc etc) browser object (navigator.appVersion) etc
    - TCP/IP fingerprint JA3 etc
    - Graphic rendering fingerprinting
    - Your IP being obviously in a pool of bad actors
    - Too many requests
    - Scraping of SCH-UA browser replies (thanks google!!)
    - Scraping of ServiceWorker, new window calls etc

    See https://filipvitas.medium.com/how-to-set-user-agent-header-with-puppeteer-js-and-not-fail-28c7a02165da
    Puppeteer requests https://github.com/dgtlmoon/pyppeteerstealth

    :param page:
    :param headers:
    :return:
    """
    # Ask it what the user agent is, if its obviously ChromeHeadless, switch it to the default
    ua_in_custom_headers = next((v for k, v in headers.items() if k.lower() == "user-agent"), None)
    if ua_in_custom_headers:
        return ua_in_custom_headers

    if not ua_in_custom_headers and current_ua:
        current_ua = current_ua.replace('HeadlessChrome', 'Chrome')
        return current_ua

    return None


class Fetcher():
    browser_connection_is_custom = None
    browser_connection_url = None
    browser_steps = None
    browser_steps_screenshot_path = None
    content = None
    error = None
    fetcher_description = "No description"
    headers = {}
    instock_data = None
    instock_data_js = ""
    status_code = None
    webdriver_js_execute_code = None
    xpath_data = None
    xpath_element_js = ""

    # Will be needed in the future by the VisualSelector, always get this where possible.
    screenshot = False
    system_http_proxy = os.getenv('HTTP_PROXY')
    system_https_proxy = os.getenv('HTTPS_PROXY')

    # Time ONTOP of the system defined env minimum time
    render_extract_delay = 0

    def __init__(self):
        from pkg_resources import resource_string
        # The code that scrapes elements and makes a list of elements/size/position to click on in the VisualSelector
        self.xpath_element_js = resource_string(__name__, "res/xpath_element_scraper.js").decode('utf-8')
        self.instock_data_js = resource_string(__name__, "res/stock-not-in-stock.js").decode('utf-8')

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
            ignore_status_codes=False,
            current_include_filters=None,
            is_binary=False):
        # Should set self.error, self.status_code and self.content
        pass

    @abstractmethod
    def quit(self):
        return

    @abstractmethod
    def get_last_status_code(self):
        return self.status_code

    @abstractmethod
    def screenshot_step(self, step_n):
        return None

    @abstractmethod
    # Return true/false if this checker is ready to run, in the case it needs todo some special config check etc
    def is_ready(self):
        return True

    def get_all_headers(self):
        """
        Get all headers but ensure all keys are lowercase
        :return:
        """
        return {k.lower(): v for k, v in self.headers.items()}

    def browser_steps_get_valid_steps(self):
        if self.browser_steps is not None and len(self.browser_steps):
            valid_steps = filter(
                lambda s: (s['operation'] and len(s['operation']) and s['operation'] != 'Choose one' and s['operation'] != 'Goto site'),
                self.browser_steps)

            return valid_steps

        return None

    def iterate_browser_steps(self):
        from changedetectionio.blueprint.browser_steps.browser_steps import steppable_browser_interface
        from playwright._impl._errors import TimeoutError, Error
        from jinja2 import Environment
        jinja2_env = Environment(extensions=['jinja2_time.TimeExtension'])

        step_n = 0

        if self.browser_steps is not None and len(self.browser_steps):
            interface = steppable_browser_interface()
            interface.page = self.page
            valid_steps = self.browser_steps_get_valid_steps()

            for step in valid_steps:
                step_n += 1
                logger.debug(f">> Iterating check - browser Step n {step_n} - {step['operation']}...")
                self.screenshot_step("before-" + str(step_n))
                self.save_step_html("before-" + str(step_n))
                try:
                    optional_value = step['optional_value']
                    selector = step['selector']
                    # Support for jinja2 template in step values, with date module added
                    if '{%' in step['optional_value'] or '{{' in step['optional_value']:
                        optional_value = str(jinja2_env.from_string(step['optional_value']).render())
                    if '{%' in step['selector'] or '{{' in step['selector']:
                        selector = str(jinja2_env.from_string(step['selector']).render())

                    getattr(interface, "call_action")(action_name=step['operation'],
                                                      selector=selector,
                                                      optional_value=optional_value)
                    self.screenshot_step(step_n)
                    self.save_step_html(step_n)
                except (Error, TimeoutError) as e:
                    logger.debug(str(e))
                    # Stop processing here
                    raise BrowserStepsStepException(step_n=step_n, original_e=e)

    # It's always good to reset these
    def delete_browser_steps_screenshots(self):
        import glob
        if self.browser_steps_screenshot_path is not None:
            dest = os.path.join(self.browser_steps_screenshot_path, 'step_*.jpeg')
            files = glob.glob(dest)
            for f in files:
                if os.path.isfile(f):
                    os.unlink(f)

    def save_step_html(self, param):
        pass
