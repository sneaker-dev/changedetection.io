from .. import difference_detection_processor
from ..exceptions import ProcessorException
from . import Restock
from loguru import logger

import urllib3
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
name = 'Re-stock & Price detection for single product pages'
description = 'Detects if the product goes back to in-stock'

class UnableToExtractRestockData(Exception):
    def __init__(self, status_code):
        # Set this so we can use it in other parts of the app
        self.status_code = status_code
        return

class MoreThanOnePriceFound(Exception):
    def __init__(self):
        return

def _search_prop_by_value(matches, value):
    for properties in matches:
        for prop in properties:
            if value in prop[0]:
                return prop[1]  # Yield the desired value and exit the function

# should return Restock()
# add casting?
def get_itemprop_availability(html_content) -> Restock:
    """
    Kind of funny/cool way to find price/availability in one many different possibilities.
    Use 'extruct' to find any possible RDFa/microdata/json-ld data, make a JSON string from the output then search it.
    """
    from jsonpath_ng import parse

    import re
    now = time.time()
    import extruct
    logger.trace(f"Imported extruct module in {time.time() - now:.3f}s")

    now = time.time()

    # Extruct is very slow, I'm wondering if some ML is going to be faster (800ms on my i7), 'rdfa' seems to be the heaviest.
    syntaxes = ['dublincore', 'json-ld', 'microdata', 'microformat', 'opengraph']
    try:
        data = extruct.extract(html_content, syntaxes=syntaxes)
    except Exception as e:
        logger.warning(f"Unable to extract data, document parsing with extruct failed with {type(e).__name__} - {str(e)}")
        return Restock()

    logger.trace(f"Extruct basic extract of all metadata done in {time.time() - now:.3f}s")

    # First phase, dead simple scanning of anything that looks useful
    value = Restock()
    if data:
        logger.debug(f"Using jsonpath to find price/availability/etc")
        price_parse = parse('$..(price|Price)')
        pricecurrency_parse = parse('$..(pricecurrency|currency|priceCurrency )')
        availability_parse = parse('$..(availability|Availability)')

        price_result = price_parse.find(data)
        if price_result:
            # Right now, we just support single product items, maybe we will store the whole actual metadata seperately in teh future and
            # parse that for the UI?
            prices_found = set(str(item.value).replace('$', '') for item in price_result)
            if len(price_result) > 1 and len(prices_found) > 1:
                # See of all prices are different, in the case that one product has many embedded data types with the same price
                # One might have $121.95 and another 121.95 etc
                logger.warning(f"More than one price found {prices_found}, throwing exception, cant use this plugin.")
                raise MoreThanOnePriceFound()

            value['price'] = price_result[0].value

        pricecurrency_result = pricecurrency_parse.find(data)
        if pricecurrency_result:
            value['currency'] = pricecurrency_result[0].value

        availability_result = availability_parse.find(data)
        if availability_result:
            value['availability'] = availability_result[0].value

        if value.get('availability'):
            value['availability'] = re.sub(r'(?i)^(https|http)://schema.org/', '',
                                           value.get('availability').strip(' "\'').lower()) if value.get('availability') else None

        # Second, go dig OpenGraph which is something that jsonpath_ng cant do because of the tuples and double-dots (:)
        if not value.get('price') or value.get('availability'):
            logger.debug(f"Alternatively digging through OpenGraph properties for restock/price info..")
            jsonpath_expr = parse('$..properties')

            for match in jsonpath_expr.find(data):
                if not value.get('price'):
                    value['price'] = _search_prop_by_value([match.value], "price:amount")
                if not value.get('availability'):
                    value['availability'] = _search_prop_by_value([match.value], "product:availability")
                if not value.get('currency'):
                    value['currency'] = _search_prop_by_value([match.value], "price:currency")
    logger.trace(f"Processed with Extruct in {time.time()-now:.3f}s")

    return value


def is_between(number, lower=None, upper=None):
    """
    Check if a number is between two values.

    Parameters:
    number (float): The number to check.
    lower (float or None): The lower bound (inclusive). If None, no lower bound.
    upper (float or None): The upper bound (inclusive). If None, no upper bound.

    Returns:
    bool: True if the number is between the lower and upper bounds, False otherwise.
    """
    return (lower is None or lower <= number) and (upper is None or number <= upper)


class perform_site_check(difference_detection_processor):
    screenshot = None
    xpath_data = None

    def run_changedetection(self, watch, skip_when_checksum_same=True):
        import hashlib

        from concurrent.futures import ProcessPoolExecutor
        from functools import partial
        if not watch:
            raise Exception("Watch no longer exists.")

        # Unset any existing notification error
        update_obj = {'last_notification_error': False, 'last_error': False, 'restock':  Restock()}

        self.screenshot = self.fetcher.screenshot
        self.xpath_data = self.fetcher.xpath_data

        # Track the content type
        update_obj['content_type'] = self.fetcher.headers.get('Content-Type', '')
        update_obj["last_check_status"] = self.fetcher.get_last_status_code()

        # Which restock settings to compare against?
        restock_settings = watch.get('restock_settings', {})

        # See if any tags have 'activate for individual watches in this tag/group?' enabled and use the first we find
        for tag_uuid in watch.get('tags'):
            tag = self.datastore.data['settings']['application']['tags'].get(tag_uuid, {})
            if tag.get('overrides_watch'):
                restock_settings = tag.get('restock_settings', {})
                logger.info(f"Watch {watch.get('uuid')} - Tag '{tag.get('title')}' selected for restock settings override")
                break


        itemprop_availability = {}
        try:
            with ProcessPoolExecutor() as executor:
                # Use functools.partial to create a callable with arguments
                # anything using bs4/lxml etc is quite "leaky"
                future = executor.submit(partial(get_itemprop_availability, self.fetcher.content))
                itemprop_availability = future.result()
        except MoreThanOnePriceFound as e:
            # Add the real data
            raise ProcessorException(message="Cannot run, more than one price detected, this plugin is only for product pages with ONE product, try the content-change detection mode.",
                                     url=watch.get('url'),
                                     status_code=self.fetcher.get_last_status_code(),
                                     screenshot=self.fetcher.screenshot,
                                     xpath_data=self.fetcher.xpath_data
                                     )

        # Something valid in get_itemprop_availability() by scraping metadata ?
        if itemprop_availability.get('price') or itemprop_availability.get('availability'):
            # Store for other usage
            update_obj['restock'] = itemprop_availability

            if itemprop_availability.get('availability'):
                # @todo: Configurable?
                if any(substring.lower() in itemprop_availability['availability'].lower() for substring in [
                    'instock',
                    'instoreonly',
                    'limitedavailability',
                    'onlineonly',
                    'presale']
                       ):
                    update_obj['restock']['in_stock'] = True
                else:
                    update_obj['restock']['in_stock'] = False

        # Main detection method
        fetched_md5 = None

        # store original price if not set
        if itemprop_availability and itemprop_availability.get('price') and not itemprop_availability.get('original_price'):
            itemprop_availability['original_price'] = itemprop_availability.get('price')
            update_obj['restock']["original_price"] = itemprop_availability.get('price')

        if not self.fetcher.instock_data and not itemprop_availability.get('availability'):
            raise ProcessorException(
                message=f"Unable to extract restock data for this page unfortunately. (Got code {self.fetcher.get_last_status_code()} from server), no embedded stock information was found and nothing interesting in the text, try using this watch with Chrome.",
                url=watch.get('url'),
                status_code=self.fetcher.get_last_status_code(),
                screenshot=self.fetcher.screenshot,
                xpath_data=self.fetcher.xpath_data
                )

        # Nothing automatic in microdata found, revert to scraping the page
        if self.fetcher.instock_data and itemprop_availability.get('availability') is None:
            # 'Possibly in stock' comes from stock-not-in-stock.js when no string found above the fold.
            # Careful! this does not really come from chrome/js when the watch is set to plaintext
            update_obj['restock']["in_stock"] = True if self.fetcher.instock_data == 'Possibly in stock' else False
            logger.debug(f"Watch UUID {watch.get('uuid')} restock check returned '{self.fetcher.instock_data}' from JS scraper.")

        # What we store in the snapshot
        price = update_obj.get('restock').get('price') if update_obj.get('restock').get('price') else ""
        snapshot_content = f"In Stock: {update_obj.get('restock').get('in_stock')} - Price: {price}"

        # Main detection method
        fetched_md5 = hashlib.md5(snapshot_content.encode('utf-8')).hexdigest()

        # The main thing that all this at the moment comes down to :)
        changed_detected = False
        logger.debug(f"Watch UUID {watch.get('uuid')} restock check - Previous MD5: {watch.get('previous_md5')}, Fetched MD5 {fetched_md5}")

        # out of stock -> back in stock only?
        if watch.get('restock') and watch['restock'].get('in_stock') != update_obj['restock'].get('in_stock'):
            # Yes if we only care about it going to instock, AND we are in stock
            if restock_settings.get('in_stock_processing') == 'in_stock_only' and update_obj['restock']['in_stock']:
                changed_detected = True

            if restock_settings.get('in_stock_processing') == 'all_changes':
                # All cases
                changed_detected = True

        if restock_settings.get('follow_price_changes') and watch.get('restock') and update_obj.get('restock') and update_obj['restock'].get('price'):
            price = float(update_obj['restock'].get('price'))
            # Default to current price if no previous price found
            if watch['restock'].get('original_price'):
                previous_price = float(watch['restock'].get('original_price'))
                # It was different, but negate it further down
                if price != previous_price:
                    changed_detected = True

            # Minimum/maximum price limit
            if update_obj.get('restock') and update_obj['restock'].get('price'):
                logger.debug(
                    f"{watch.get('uuid')} - Change was detected, 'price_change_max' is '{restock_settings.get('price_change_max', '')}' 'price_change_min' is '{restock_settings.get('price_change_min', '')}', price from website is '{update_obj['restock'].get('price', '')}'.")
                if update_obj['restock'].get('price'):
                    min_limit = float(restock_settings.get('price_change_min')) if restock_settings.get('price_change_min') else None
                    max_limit = float(restock_settings.get('price_change_max')) if restock_settings.get('price_change_max') else None

                    price = float(update_obj['restock'].get('price'))
                    logger.debug(f"{watch.get('uuid')} after float conversion - Min limit: '{min_limit}' Max limit: '{max_limit}' Price: '{price}'")
                    if min_limit or max_limit:
                        if is_between(number=price, lower=min_limit, upper=max_limit):
                            # Price was between min/max limit, so there was nothing todo in any case
                            logger.trace(f"{watch.get('uuid')} {price} is between {min_limit} and {max_limit}, nothing to check, forcing changed_detected = False (was {changed_detected})")
                            changed_detected = False
                        else:
                            logger.trace(f"{watch.get('uuid')} {price} is between {min_limit} and {max_limit}, continuing normal comparison")

                    # Price comparison by %
                    if watch['restock'].get('original_price') and changed_detected and restock_settings.get('price_change_threshold_percent'):
                        previous_price = float(watch['restock'].get('original_price'))
                        pc = float(restock_settings.get('price_change_threshold_percent'))
                        change = abs((price - previous_price) / previous_price * 100)
                        if change and change <= pc:
                            logger.debug(f"{watch.get('uuid')} Override change-detected to FALSE because % threshold ({pc}%) was {change:.3f}%")
                            changed_detected = False
                        else:
                            logger.debug(f"{watch.get('uuid')} Price change was {change:.3f}% , (threshold {pc}%)")

        # Always record the new checksum
        update_obj["previous_md5"] = fetched_md5

        return changed_detected, update_obj, snapshot_content.encode('utf-8').strip()
