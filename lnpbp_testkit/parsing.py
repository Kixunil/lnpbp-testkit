from pathlib import Path
import configparser
import itertools
from typing import Iterable

def parse_simple_config_lines(lines: Iterable) -> configparser.SectionProxy:
    config = configparser.ConfigParser()
    # See https://stackoverflow.com/questions/2819696/parsing-properties-file-in-python/25493615#comment39631896_8555776
    config.read_file(itertools.chain(["[config]"], lines))
    return config["config"]

def parse_simple_config(path: Path) -> configparser.SectionProxy:
    with open(path, "r") as config_file:
        return parse_simple_config_lines(config_file)

def port_from_host_port(host_port: str) -> int:
    split = host_port.split(':')
    if len(split) != 2:
        raise Exception("Invalid input %s, should be host:port" % host_port)
    return int(split[1])

def port_from_uri(uri: str) -> int:
    split = uri.split(':')
    if len(split) < 3:
        raise Exception("Invalid input %s, should be scheme:host:port" % uri)
    return int(split[2].split('/')[0])
