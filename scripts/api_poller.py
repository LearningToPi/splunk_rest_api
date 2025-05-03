'''
Main script to execute API queries specified in the configuration file
'''
# pylint: disable=W0718
import sys
import json
import re
from threading import Thread, Lock
from time import time, sleep
from logging_handler import create_logger, INFO, CRITICAL
import requests

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class PollingService:
    ''' Polling service '''
    def __init__(self, settings:dict):
        self._settings = settings
        self._console_lock = Lock()
        self._poll_thread = None
        self._stop_polling = False

        # create logging file
        if settings.get('log_file', None) is not None:
            self._logger = create_logger(console_level=settings.get('log_console_level', CRITICAL),
                                         file_level=settings.get('log_level', INFO),
                                         log_file=settings.get('log_file', ''),
                                         log_file_retention_days=settings.get('log_retention_days', 5),
                                         name="PollingService")
        else:
            self._logger = create_logger(settings.get('log_console_level', CRITICAL), name="PollingService")

    def __del__(self):
        self.stop()

    def start(self):
        ''' start the polling '''
        self._logger.info("Starting API polling service...")
        self._poll_thread = Thread(target=self._polling_thread)
        self._poll_thread.start()

    def stop(self):
        ''' Stop polling '''
        if isinstance(self._poll_thread, Thread) and self._poll_thread.is_alive():
            self._stop_polling = True
            self._poll_thread.join()

    def _write_data(self, endpoint:dict, api:dict, data:dict|list, error:None|Exception=None):
        ''' Write the data to the console for Splunk to pickup '''
        err_message = False if error is None else f"{error.__class__.__name__}: {error}"
        with self._console_lock:
            print(json.dumps({'endpoint': endpoint.get('hostname'), 'name': api.get('name'), 'api': api.get('path'), 'error': err_message, 'data': filter_data(data, api.get('filter', None))}))
            print()

    def _polling_thread(self):
        ''' Background thread for polling '''
        while True:
            try:
                if self._stop_polling:
                    return
                # loop through all endpoints
                for endpoint in self._settings.get('endpoints', []):
                    # loop through all the API's
                    for api in endpoint.get('rest_api', []):
                        if api.get('last_poll', 0) < time() - api.get('interval', endpoint.get('interval', self._settings.get('interval', 300))):
                            # update the poll interval so we don't immediately attempt to poll again
                            api['last_poll'] = time()
                            self._logger.info(f"Polling endpoint: {endpoint.get('hostname')}, api path: {api.get('path')}")
                            Thread(target=self._poll_api, kwargs={'endpoint': endpoint, 'api': api}).start()

                # sleep for 1 second then restart polling check
                sleep(1)

            except Exception as e:
                Thread(target=self._write_data, kwargs={'endpoint': endpoint, 'api': api, 'error': e}).start()
                self._logger.error(f"{endpoint}: {api}: {e.__class__.__name__}: {e}")

    def _poll_api(self, endpoint:dict, api: dict):
        ''' Execute a poll against an API as a thread '''
        try:
            api['last_poll'] = time()
            retry_count = api.get('retry', endpoint.get('retry', 3))
            protocol = api.get('protocol', endpoint.get('protocol', 'https'))
            hostname = api.get('hostname', endpoint.get('hostname', 'localhost'))
            port = api.get('port', endpoint.get('port', 80 if protocol == 'http' else 443))
            headers = endpoint.get('headers', {})
            headers.update(api.get('headers',{}))
            verify = api.get('verify', endpoint.get('verify', self._settings.get('verify', True)))
            timeout = api.get('timeout', endpoint.get('timeout', self._settings.get('timeout', 3)))
            # add bearer token to header if present
            if api.get('bearer_token', endpoint.get('bearer_token', None)) is not None:
                headers['Authorization'] = f"Bearer {api.get('bearer_token', endpoint.get('bearer_token', None))}"
            while retry_count > 0:
                self._logger.debug(f"API Poll ({retry_count} tries remaining): {api.get('method', 'get').upper()} {protocol}://{hostname}:{port}/{api.get('path', '').lstrip('/')}")
                response = requests.request(method=api.get('method', 'get'),
                                            url=f"{protocol}://{hostname}:{port}/{api.get('path', '').lstrip('/')}",
                                            headers=headers,
                                            data=json.dumps(variable_swap(api.get('body'))),
                                            verify=verify,
                                            timeout=timeout)
                # check for success
                if response.status_code in api.get('status_code', endpoint.get('status_code', [200])):
                    self._logger.info(f"API Poll: {response.status_code} OK {api.get('method', 'get').upper()} {protocol}://{hostname}:{port}/{api.get('path', '').lstrip('/')}")
                    Thread(target=self._write_data, kwargs={'endpoint': endpoint, 'api': api, 'data': response.json()}).start()
                    # break out
                    return
                self._logger.warning(f"API Poll ({retry_count} tries remaining): {response.status_code} FAILED {api.get('method', 'get').upper()} {protocol}://{hostname}:{port}/{api.get('path', '').lstrip('/')}: {response.content}")
                retry_count -= 1
        except Exception as e:
            Thread(target=self._write_data, kwargs={'endpoint': endpoint, 'api': api, 'error': e}).start()
            self._logger.error(f"{endpoint}: {api}: {e.__class__.__name__}: {e}")
        self._logger.error(f"API Poll all attemps failed: {response.status_code} FAILED {api.get('method', 'get').upper()} {protocol}://{hostname}:{port}/{api.get('path', '').lstrip('/')}: {response.content}")


def variable_swap(data:dict|list|None) -> dict|list|None:
    ''' Replace pre-defined variables with system generated content '''
    if data is None:
        return None
    if isinstance(data, list):
        # run the variable swap on each item in the list
        return [variable_swap(x) for x in data]
    if isinstance(data, dict):
        # run the variable swap on key value
        return {key:variable_swap(value) for key, value in data.items()}
    if isinstance(data, str):
        return _var_swap_str(data)


def _var_swap_str(data:str):
    ''' Swap a variable with system generated content '''
    if data.startswith('%%') and data.endswith('%%'):
        if data.startswith('%%timestamp'):
            var_re = re.search(r'%%(?P<function>timestamp)(?P<action>[+-])?(?P<diff>[0-9]+)?', data)
            if var_re:
                if var_re.groupdict()['action'] == '+' and var_re.groupdict()['diff'].isdigit():
                    return int(time() + int(var_re.groupdict()['diff']))
                if var_re.groupdict()['action'] == '-' and var_re.groupdict()['diff'].isdigit():
                    return int(time() - int(var_re.groupdict()['diff']))
                return int(time())
    return data


def filter_data(data:dict|list, data_filter:dict|None) -> dict|list:
    ''' Filter the json data returned from an API '''
    if data_filter is None:
        # no filter needed
        return data
    if isinstance(data, list):
        # filter a list
        if data_filter.get('index', None) is None:
            return filter_data(data, data_filter.get('filter', None))
        if data_filter.get('index', 0) >= 0 and data_filter.get('index', 0) < len(data):
            return filter_data(data[data_filter.get('index', 0)], data_filter.get('filter', None))
        return [filter_data(x, data_filter.get('filter')) for x in data]
    if 'keys' in data_filter and isinstance(data, dict):
        # filter a dict to return specific keys
        return {key: filter_data(value, data_filter.get(key, None)) for key, value in data.items() if key in data_filter.get('keys')}
    # no filter found, return as is
    return data


def main():
    ''' Start the polling service '''
    if len(sys.argv) < 2:
        raise ValueError("Expecting a json formatted settings file as a parameter.\n\nExiting.")
    # read settings file
    with open(sys.argv[1], 'r', encoding='utf-8') as input_file:
        settings = json.load(input_file)
    polling_service = PollingService(settings)
    polling_service.start()


if __name__ == "__main__":
    main()
