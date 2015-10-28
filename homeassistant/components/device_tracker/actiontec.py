"""
homeassistant.components.device_tracker.actiontec
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Device tracker platform that supports scanning an Actiontec MI424WR
(Verizon FIOS) router for device presence.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/device_tracker.actiontec.html
"""
import logging
from datetime import timedelta
from collections import namedtuple
import re
import threading
import telnetlib

import homeassistant.util.dt as dt_util
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers import validate_config
from homeassistant.util import Throttle, convert
from homeassistant.components.device_tracker import DOMAIN

# Return cached results if last scan was less then this time ago
MIN_TIME_BETWEEN_SCANS = timedelta(seconds=5)

_LOGGER = logging.getLogger(__name__)

_LEASES_REGEX = re.compile(
    r'(?P<ip>([0-9]{1,3}[\.]){3}[0-9]{1,3})' +
    r'\smac:\s(?P<mac>([0-9a-f]{2}[:-]){5}([0-9a-f]{2}))' +
    r'\svalid\sfor:\s(?P<timevalid>(-?\d+))' +
    r'\ssec')


# pylint: disable=unused-argument
def get_scanner(hass, config):
    """ Validates config and returns an Actiontec scanner. """
    if not validate_config(config,
                           {DOMAIN: [CONF_HOST, CONF_USERNAME, CONF_PASSWORD]},
                           _LOGGER):
        return None
    scanner = ActiontecDeviceScanner(config[DOMAIN])
    return scanner if scanner.success_init else None

Device = namedtuple("Device", ["mac", "ip", "last_update"])


class ActiontecDeviceScanner(object):
    """
    This class queries a an actiontec router for connected devices.
    Adapted from DD-WRT scanner.
    """

    def __init__(self, config):
        self.host = config[CONF_HOST]
        self.username = config[CONF_USERNAME]
        self.password = config[CONF_PASSWORD]
        self.lock = threading.Lock()
        self.last_results = []
        # Test the router is accessible
        data = self.get_actiontec_data()
        self.success_init = data is not None
        _LOGGER.info("actiontec scanner initialized")

    def scan_devices(self):
        """
        Scans for new devices and return a list containing found device ids.
        """

        self._update_info()
        return [client.mac for client in self.last_results]

    def get_device_name(self, device):
        """ Returns the name of the given device or None if we don't know. """
        if not self.last_results:
            return None
        for client in self.last_results:
            if client.mac == device:
                return client.ip
        return None

    @Throttle(MIN_TIME_BETWEEN_SCANS)
    def _update_info(self):
        """
        Ensures the information from the Actiontec MI424WR router is up
        to date. Returns boolean if scanning successful.
        """
        _LOGGER.info("Scanning")
        if not self.success_init:
            return False

        with self.lock:
            exclude_targets = set()
            exclude_target_list = []
            now = dt_util.now()
            actiontec_data = self.get_actiontec_data()
            if not actiontec_data:
                return False
            self.last_results = []
            for client in exclude_target_list:
                if client in actiontec_data:
                    actiontec_data.pop(client)
            for name, data in actiontec_data.items():
                if data['timevalid'] > 0:
                    device = Device(data['mac'], name, now)
                    self.last_results.append(device)
            self.last_results.extend(exclude_targets)
            _LOGGER.info("actiontec scan successful")
            return True

    def get_actiontec_data(self):
        """ Retrieve data from Actiontec MI424WR and return parsed result. """
        try:
            telnet = telnetlib.Telnet(self.host)
            telnet.read_until(b'Username: ')
            telnet.write((self.username + '\n').encode('ascii'))
            telnet.read_until(b'Password: ')
            telnet.write((self.password + '\n').encode('ascii'))
            prompt = telnet.read_until(
                b'Wireless Broadband Router> ').split(b'\n')[-1]
            telnet.write('firewall mac_cache_dump\n'.encode('ascii'))
            telnet.write('\n'.encode('ascii'))
            telnet.read_until(prompt)
            leases_result = telnet.read_until(prompt).split(b'\n')[1:-1]
            telnet.write('exit\n'.encode('ascii'))
        except EOFError:
            _LOGGER.exception("Unexpected response from router")
            return
        except ConnectionRefusedError:
            _LOGGER.exception("Connection refused by router," +
                              " is telnet enabled?")
            return None

        devices = {}
        for lease in leases_result:
            match = _LEASES_REGEX.search(lease.decode('utf-8'))
            if match is not None:
                devices[match.group('ip')] = {
                    'ip': match.group('ip'),
                    'mac': match.group('mac').upper(),
                    'timevalid': int(match.group('timevalid'))
                    }
        return devices
