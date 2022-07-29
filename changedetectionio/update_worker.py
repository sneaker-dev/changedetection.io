import threading
import queue
import time

from changedetectionio import content_fetcher
from changedetectionio.html_tools import FilterNotFoundInResponse

# A single update worker
#
# Requests for checking on a single site(watch) from a queue of watches
# (another process inserts watches into the queue that are time-ready for checking)


class update_worker(threading.Thread):
    current_uuid = None

    def __init__(self, q, notification_q, app, datastore, *args, **kwargs):
        self.q = q
        self.app = app
        self.notification_q = notification_q
        self.datastore = datastore
        super().__init__(*args, **kwargs)

    def send_filter_failure_notification(self, uuid):

        threshold = self.datastore.data['settings']['application'].get('filter_failure_notification_threshold_attempts')
        watch = self.datastore.data['watching'].get(uuid, False)

        n_object = {'notification_title': 'Changedetection.io - Alert - CSS/xPath filter was not present in the page',
                    'notification_body': "Your configured CSS/xPath filter of '{}' for {{watch_url}} did not appear on the page after {} attempts, did the page change layout?\n\nLink: {{base_url}}/edit/{{watch_uuid}}\n\nThanks - Your omniscient changedetection.io installation :)\n".format(
                        watch['css_filter'],
                        threshold),
                    'notification_format': 'text'}

        if len(watch['notification_urls']):
            n_object['notification_urls'] = watch['notification_urls']

        elif len(self.datastore.data['settings']['application']['notification_urls']):
            n_object['notification_urls'] = self.datastore.data['settings']['application']['notification_urls']

        # Only prepare to notify if the rules above matched
        if 'notification_urls' in n_object:
            n_object.update({
                'watch_url': watch['url'],
                'uuid': uuid
            })
            self.notification_q.put(n_object)
            print("Sent filter not found notification for {}".format(uuid))

    def run(self):
        from changedetectionio import fetch_site_status

        update_handler = fetch_site_status.perform_site_check(datastore=self.datastore)

        while not self.app.config.exit.is_set():

            try:
                uuid = self.q.get(block=False)
            except queue.Empty:
                pass

            else:
                self.current_uuid = uuid

                if uuid in list(self.datastore.data['watching'].keys()):

                    changed_detected = False
                    contents = b''
                    screenshot = False
                    update_obj= {}
                    xpath_data = False
                    process_changedetection_results = True

                    now = time.time()

                    try:
                        changed_detected, update_obj, contents, screenshot, xpath_data = update_handler.run(uuid)
                        # Re #342
                        # In Python 3, all strings are sequences of Unicode characters. There is a bytes type that holds raw bytes.
                        # We then convert/.decode('utf-8') for the notification etc
                        if not isinstance(contents, (bytes, bytearray)):
                            raise Exception("Error - returned data from the fetch handler SHOULD be bytes")
                    except PermissionError as e:
                        self.app.logger.error("File permission error updating", uuid, str(e))
                        process_changedetection_results = False
                    except content_fetcher.ReplyWithContentButNoText as e:
                        # Totally fine, it's by choice - just continue on, nothing more to care about
                        # Page had elements/content but no renderable text
                        # Backend (not filters) gave zero output
                        self.datastore.update_watch(uuid=uuid, update_obj={'last_error': "Got HTML content but no text found."})
                        process_changedetection_results = False

                    except FilterNotFoundInResponse as e:
                        err_text = "Warning, filter '{}' not found".format(str(e))
                        self.datastore.update_watch(uuid=uuid, update_obj={'last_error': err_text,
                                                                           # So that we get a trigger when the content is added again
                                                                           'previous_md5': ''})

                        # Only when enabled, send the notification
                        if self.datastore.data['watching'][uuid].get('filter_failure_notification_send', False):
                            c = self.datastore.data['watching'][uuid].get('consecutive_filter_failures', 5)
                            c += 1
                            # Send notification if we reached the threshold?
                            threshold = self.datastore.data['settings']['application'].get('filter_failure_notification_threshold_attempts',
                                                                                           0)
                            print("Filter for {} not found, consecutive_filter_failures: {}".format(uuid, c))
                            if threshold > 0 and c >= threshold:
                                self.send_filter_failure_notification(uuid)
                                c = 0
                            self.datastore.update_watch(uuid=uuid, update_obj={'consecutive_filter_failures': c})

                        process_changedetection_results = True

                    except content_fetcher.EmptyReply as e:
                        # Some kind of custom to-str handler in the exception handler that does this?
                        err_text = "EmptyReply - try increasing 'Wait seconds before extracting text', Status Code {}".format(e.status_code)
                        self.datastore.update_watch(uuid=uuid, update_obj={'last_error': err_text,
                                                                           'last_check_status': e.status_code})
                    except content_fetcher.ScreenshotUnavailable as e:
                        err_text = "Screenshot unavailable, page did not render fully in the expected time - try increasing 'Wait seconds before extracting text'"
                        self.datastore.update_watch(uuid=uuid, update_obj={'last_error': err_text,
                                                                           'last_check_status': e.status_code})
                        process_changedetection_results = False
                    except content_fetcher.PageUnloadable as e:
                        err_text = "Page request from server didnt respond correctly"
                        self.datastore.update_watch(uuid=uuid, update_obj={'last_error': err_text,
                                                                           'last_check_status': e.status_code})
                    except Exception as e:
                        self.app.logger.error("Exception reached processing watch UUID: %s - %s", uuid, str(e))
                        self.datastore.update_watch(uuid=uuid, update_obj={'last_error': str(e)})
                        # Other serious error
                        process_changedetection_results = False
                    else:
                        # Mark that we never had any failures
                        update_obj['consecutive_filter_failures'] = 0

                    # Different exceptions mean that we may or may not want to bump the snapshot, trigger notifications etc
                    if process_changedetection_results:
                        try:
                            watch = self.datastore.data['watching'][uuid]
                            fname = "" # Saved history text filename

                            # For the FIRST time we check a site, or a change detected, save the snapshot.
                            if changed_detected or not watch['last_checked']:
                                # A change was detected
                                fname = watch.save_history_text(contents=contents, timestamp=str(round(time.time())))

                            self.datastore.update_watch(uuid=uuid, update_obj=update_obj)

                            # A change was detected
                            if changed_detected:
                                n_object = {}
                                print (">> Change detected in UUID {} - {}".format(uuid, watch['url']))

                                # Notifications should only trigger on the second time (first time, we gather the initial snapshot)
                                if watch.history_n >= 2:
                                    # Atleast 2, means there really was a change
                                    self.datastore.update_watch(uuid=uuid, update_obj={'last_changed': round(now)})

                                    watch_history = watch.history
                                    dates = list(watch_history.keys())
                                    # Theoretically it's possible that this could be just 1 long,
                                    # - In the case that the timestamp key was not unique
                                    if len(dates) == 1:
                                        raise ValueError(
                                            "History index had 2 or more, but only 1 date loaded, timestamps were not unique? maybe two of the same timestamps got written, needs more delay?"
                                        )
                                    prev_fname = watch_history[dates[-2]]

                                    # Did it have any notification alerts to hit?
                                    if len(watch['notification_urls']):
                                        print(">>> Notifications queued for UUID from watch {}".format(uuid))
                                        n_object['notification_urls'] = watch['notification_urls']
                                        n_object['notification_title'] = watch['notification_title']
                                        n_object['notification_body'] = watch['notification_body']
                                        n_object['notification_format'] = watch['notification_format']

                                    # No? maybe theres a global setting, queue them all
                                    elif len(self.datastore.data['settings']['application']['notification_urls']):
                                        print(">>> Watch notification URLs were empty, using GLOBAL notifications for UUID: {}".format(uuid))
                                        n_object['notification_urls'] = self.datastore.data['settings']['application']['notification_urls']
                                        n_object['notification_title'] = self.datastore.data['settings']['application']['notification_title']
                                        n_object['notification_body'] = self.datastore.data['settings']['application']['notification_body']
                                        n_object['notification_format'] = self.datastore.data['settings']['application']['notification_format']
                                    else:
                                        print(">>> NO notifications queued, watch and global notification URLs were empty.")

                                    # Only prepare to notify if the rules above matched
                                    if 'notification_urls' in n_object:
                                        # HTML needs linebreak, but MarkDown and Text can use a linefeed
                                        if n_object['notification_format'] == 'HTML':
                                            line_feed_sep = "</br>"
                                        else:
                                            line_feed_sep = "\n"

                                        from changedetectionio import diff
                                        n_object.update({
                                            'watch_url': watch['url'],
                                            'uuid': uuid,
                                            'current_snapshot': contents.decode('utf-8'),
                                            'diff': diff.render_diff(prev_fname, fname, line_feed_sep=line_feed_sep),
                                            'diff_full': diff.render_diff(prev_fname, fname, True, line_feed_sep=line_feed_sep)
                                        })

                                        self.notification_q.put(n_object)

                        except Exception as e:
                            # Catch everything possible here, so that if a worker crashes, we don't lose it until restart!
                            print("!!!! Exception in update_worker !!!\n", e)
                            self.app.logger.error("Exception reached processing watch UUID: %s - %s", uuid, str(e))
                            self.datastore.update_watch(uuid=uuid, update_obj={'last_error': str(e)})


                        # Always record that we atleast tried
                        self.datastore.update_watch(uuid=uuid, update_obj={'fetch_time': round(time.time() - now, 3),
                                                                           'last_checked': round(time.time())})

                        # Always save the screenshot if it's available
                        if screenshot:
                            self.datastore.save_screenshot(watch_uuid=uuid, screenshot=screenshot)
                        if xpath_data:
                            self.datastore.save_xpath_data(watch_uuid=uuid, data=xpath_data)


                self.current_uuid = None  # Done
                self.q.task_done()

                # Give the CPU time to interrupt
                time.sleep(0.1)

            self.app.config.exit.wait(1)
