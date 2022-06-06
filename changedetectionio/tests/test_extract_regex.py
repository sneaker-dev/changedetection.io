#!/usr/bin/python3

import time
from flask import url_for
from .util import live_server_setup

from ..html_tools import *


def set_original_response():
    test_return_data = """<html>
       <body>
     Some initial text</br>
     <p>Which is across multiple lines</p>
     </br>
     So let's see what happens.  </br>
     <div id="sametext">Some text thats the same</div>
     <div id="changetext">Some text that will change</div>
     </body>
     </html>
    """

    with open("test-datastore/endpoint-content.txt", "w") as f:
        f.write(test_return_data)
    return None


def set_modified_response():
    test_return_data = """<html>
       <body>
     Some initial text</br>
     <p>which has this one new line</p>
     </br>
     So let's see what happens.  </br>
     <div id="sametext">Some text thats the same</div>
     <div id="changetext">Some text that did change ( 1000 online <br/> 80 guests)</div>
     </body>
     </html>
    """

    with open("test-datastore/endpoint-content.txt", "w") as f:
        f.write(test_return_data)

    return None


def test_check_filter_and_regex_extract(client, live_server):
    sleep_time_for_fetch_thread = 3

    live_server_setup(live_server)
    css_filter = "#changetext"

    set_original_response()

    # Give the endpoint time to spin up
    time.sleep(1)

    # Add our URL to the import page
    test_url = url_for('test_endpoint', _external=True)
    res = client.post(
        url_for("import_page"),
        data={"urls": test_url},
        follow_redirects=True
    )
    assert b"1 Imported" in res.data

    # Trigger a check
    client.get(url_for("form_watch_checknow"), follow_redirects=True)

    # Give the thread time to pick it up
    time.sleep(sleep_time_for_fetch_thread)

    # Goto the edit page, add our ignore text
    # Add our URL to the import page
    res = client.post(
        url_for("edit_page", uuid="first"),
        data={"css_filter": css_filter,
              'extract_text': '\d+ online\n\d+ guests',
              "url": test_url,
              "tag": "",
              "headers": "",
              'fetch_backend': "html_requests"
              },
        follow_redirects=True
    )

    assert b"Updated watch." in res.data

    # Check it saved
    res = client.get(
        url_for("edit_page", uuid="first"),
    )
    assert b'\d+ online' in res.data

    # Trigger a check
#    client.get(url_for("form_watch_checknow"), follow_redirects=True)

    # Give the thread time to pick it up
    time.sleep(sleep_time_for_fetch_thread)

    #  Make a change
    set_modified_response()

    # Trigger a check
    client.get(url_for("form_watch_checknow"), follow_redirects=True)
    # Give the thread time to pick it up
    time.sleep(sleep_time_for_fetch_thread)

    # It should have 'unviewed' still
    # Because it should be looking at only that 'sametext' id
    res = client.get(url_for("index"))
    assert b'unviewed' in res.data

    # Check HTML conversion detected and workd
    res = client.get(
        url_for("preview_page", uuid="first"),
        follow_redirects=True
    )

    # Class will be blank for now because the frontend didnt apply the diff
    assert b'<div class="">1000 online' in res.data
    
    # Both regexs should be here
    assert b'<div class="">80 guests' in res.data

    # Should not be here
    assert b'Some text that did change' not in res.data