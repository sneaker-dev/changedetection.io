#!/usr/bin/python3

import pytest
from backend import changedetection_app
from backend import store
import os

# https://github.com/pallets/flask/blob/1.1.2/examples/tutorial/tests/test_auth.py
# Much better boilerplate than the docs
# https://www.python-boilerplate.com/py3+flask+pytest/

global app


def cleanup(datastore_path):
    # Unlink test output files
    files = ['output.txt',
             'url-watches.json',
             'notification.txt',
             'count.txt',
             'endpoint-content.txt']
    for file in files:
        try:
            os.unlink("{}/{}".format(datastore_path, file))
            x = 1
        except FileNotFoundError:
            pass

@pytest.fixture(scope='session')
def app(request):
    """Create application for the tests."""
    datastore_path = "./test-datastore"

    try:
        os.mkdir(datastore_path)
    except FileExistsError:
        pass

    # Enable a BASE_URL for notifications to work (so we can look for diff/ etc URLs)
    os.environ["BASE_URL"] = "http://mysite.com/"
    cleanup(datastore_path)


    app_config = {'datastore_path': datastore_path}
    cleanup(app_config['datastore_path'])
    datastore = store.ChangeDetectionStore(datastore_path=app_config['datastore_path'], include_default_watches=False)
    app = changedetection_app(app_config, datastore)
    app.config['STOP_THREADS'] = True

    def teardown():
        datastore.stop_thread = True
        app.config.exit.set()
        cleanup(app_config['datastore_path'])

       
    request.addfinalizer(teardown)
    yield app
