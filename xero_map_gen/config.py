"""
Configuration.

Classes for parsing configuration files and command line arguments.

wishlist:
- config auto loaded from static locations on disk like:
    - ROOT: /etc/xmg/
    - USER: ~/.xmg.json
    - LOCAL: ./.xmg.json
- local override with --config-file cli arg

"""

import logging
import os
import pprint
import re
from argparse import SUPPRESS
from builtins import super
from copy import copy

from six import integer_types, string_types, text_type
from traitlets import (Any, Bool, Float, Integer, List, TraitType, Type,
                       Unicode, Union, getmembers, validate)
from traitlets.config.configurable import Configurable
from traitlets.config.loader import (ArgumentError, Config, ConfigFileNotFound,
                                     JSONFileConfigLoader,
                                     KVArgParseConfigLoader, LazyConfigValue,
                                     PyFileConfigLoader)

from . import DESCRIPTION, PKG_NAME
from .helper import TraitValidation
from .log import PKG_LOGGER, ROOT_LOGGER, log_level_quiet, setup_logging


class ConfigException(UserWarning):
    pass

class RichKVArgParseConfigLoader(KVArgParseConfigLoader):
    """ Extension of KVArgParseConfigLoader to handle complex argument parsing. """

    def __init__(self, aliases=None, flags=None, *super_args, **super_kwargs):
        """
        Create Rich KVArgParseConfigLoader object.

        Parameters
        ----------
        aliases : dict
            A dict of aliases for configurable traits.
            Keys are the short aliases, Values are a dict which contains the
            resolved trait and any extra arguments that should be passed to
            `argparse.add_argument`.
            Of the form:
            ```python
            {
                'alias' : {
                    'trait': 'Configurable.trait'
                    'add_args': ['extra_arguments'],
                    'add_kwargs': {
                        'keyword': 'extra_keyword_argument',
                    }
                }
            }`
            ```
        flags : dict
            A dict of flags, keyed by str name. Values are a dict which contains
            the flag value, and any extra arguments that should be passed to
            `argparse.add_argument`. The flag values can be Config objects,
            dicts, or "key=value" strings.  If Config or dict, when the flag
            is triggered, The flag is loaded as `self.config.update(m)`.
            Of the form:
            ```python
            {
                'flag' : {
                    'value': Config(trait='trait_value'),
                    'add_args': ['extra_arguments'],
                    'add_kwargs': {
                        'keyword': 'extra_keyword_argument',
                    }
                }
            }
            ```
        """
        self.alias_extensions = {}
        if aliases:
            super_aliases = {}
            for alias, values in aliases.items():
                assert 'trait' in values, "alias values must contain a trait"
                super_aliases[alias] = values.pop('trait')
                if values:
                    self.alias_extensions[alias] = values
            super_kwargs.update(aliases=super_aliases)

        self.flag_extensions = {}
        if flags:
            super_flags = {}
            for flag, values in flags.items():
                assert 'value' in values, "flag value dict must contain a value"
                super_flags[flag] = values.pop('value')
                if values:
                    self.flag_extensions[alias] = values
            super_kwargs.update(flags=super_flags)

        super().__init__(*super_args, **super_kwargs)

    def _add_arguments(self, aliases=None, flags=None):
        """ Override _add_arguments with alias and flag extensions. """
        self.alias_flags = {}
        # print aliases, flags
        if aliases is None:
            aliases = self.aliases
        if flags is None:
            flags = self.flags
        for key,value in aliases.items():
            add_args = self.alias_extensions.get(key, {}).get('add_args', [])
            if not add_args:
                add_args = ['-'+key] if len(key) is 1 else ['--'+key]
            add_kwargs = {
                'dest': value,
                'type': text_type
            }
            add_kwargs.update(
                self.alias_extensions.get(key, {}).get('add_kwargs', {})
            )
            if key in flags:
                # flags
                add_kwargs['nargs'] = '?'
            self.parser.add_argument(*add_args, **add_kwargs)
        for key, (value, help) in flags.items():
            if key in self.aliases:
                #
                self.alias_flags[self.aliases[key]] = value
                continue
            add_args = self.flag_extensions.get(key, {}).get('add_args', [])
            if not add_args:
                add_args = ['-'+key] if len(key) is 1 else ['--'+key]
            add_kwargs = {
                'dest': '_flags',
                'action': 'append_const',
                'const': value,
                'help': help
            }
            add_kwargs.update(
                self.flag_extensions.get(key, {}).get('add_kwargs', {})
            )
            self.parser.add_argument(*add_args, **add_kwargs)

class RichConfigurable(Configurable):
    # TODO: extra methods for auto generating add_args
    @classmethod
    def trait_argparse_aliases(cls):
        traits = dict([memb for memb in getmembers(cls) if
                     isinstance(memb[1], TraitType)])
        aliases = {}
        for key, trait_obj in traits.items():
            if key in ['config', 'parent']:
                continue
            alias_dict = {}
            trait_meta = copy(trait_obj.metadata)
            alias_key = trait_meta.pop('switch', re.sub('_', '-', key))
            alias_dict['add_kwargs'] = trait_meta
            # if hasattr(trait_obj, 'default_value'):
            if trait_obj.default_value != '':
                alias_dict['add_kwargs'].update({
                    'default':text_type(trait_obj.default_value)
                })
            alias_dict['trait'] = "%s.%s" % (cls.__name__, key)
            alias_dict['section'] = cls.__name__.lower()

            aliases[alias_key] = alias_dict
        return aliases

class RichConfig(Config):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sources = {}

    def merge_source(self, name, other):
        self._sources[name] = other
        self.merge(other)

class XeroApiConfig(RichConfigurable):
    rsa_key_path = Unicode(
        help='The path to the Xero API RSA key file',
        switch="xero-key-path", metavar='PATH'
    )
    consumer_key = Unicode(
        help='The Xero API Consumer Key',
        switch="xero-consumer-key", metavar='KEY'
    )

    @validate('rsa_key_path')
    def _valid_rsa_key_path(self, proposal):
        TraitValidation.path_exists(proposal['rsa_key_path'])
        return proposal['rsa_key_path']

    @validate('consumer_key')
    def _valid_consumer_key(self, proposal):
        TraitValidation.not_falsey(
            proposal['consumer_key'],
            "%s.%s" % (self.__class__, 'consumer_key')
        )

class LogConfig(RichConfigurable):
    stream_log_level = Unicode(
        "WARNING",
        help="Set custom message output level",
        switch="verbosity", metavar='LEVEL'
    )
    file_log_level = Unicode(
        "DEBUG",
        help=SUPPRESS,
        metavar='LEVEL'
    )
    log_dir = Unicode(
        help="Directory containing log files",
        metavar='PATH'
    )
    log_path = Unicode(
        '%s.log' % PKG_NAME,
        help=SUPPRESS,
        metavar='PATH'
    )

class BaseConfig(RichConfigurable):
    contact_limit = Integer(
        help="Limit the number of contacts downloaded from the API",
        metavar='LIMIT'
    )

    config_dir = Unicode(
        help="Directory containing config files",
        metavar="PATH"
    )

    config_path = Unicode(
        help="Load extra config from file relative to config_dir if provided",
        metavar='PATH'
    )

    data_dir = Unicode(
        help="Directory to dump data",
        metavar='PATH'
    )

    dump_path = Unicode(
        default_value="contacts.csv",
        help="Location where CSV data is dumped relative to data_dir if provided",
        metavar='PATH'
    )

class FilterConfig(RichConfigurable):
    contact_groups = Unicode(
        help="Filter by Xero contact group names separated by '|'",
        switch="filter-contact-groups", metavar='"GROUP1|GROUP2"'
    )

    @validate('contact_groups')
    def _valid_contact_groups(self, proposal):
        TraitValidation.not_falsey(
            proposal['contact_groups'],
            "%s.%s" % (self.__class__, 'contact_groups')
        )

    states = Unicode(
        help="Filter by main address state. Separate states with '|'",
        switch="filter-states", metavar='"STATE1|STATE2"'
    )

    @validate('states')
    def _valid_states(self, proposal):
        TraitValidation.not_falsey(
        proposal['states'],
        "%s.%s" % (self.__class__, 'states')
        )

    countries = Unicode(
        help="Filter by main address country. Separate countries with '|'",
        switch="filter-countries", metavar='"COUNTRY1|COUNTRY2"'
    )

    @validate('countries')
    def _valid_countries(self, proposal):
        TraitValidation.not_falsey(
        proposal['countries'],
        "%s.%s" % (self.__class__, 'countries')
        )

def get_argparse_loader():
    # TODO: argparse loader args
    aliases = {}
    for config_class in [
        XeroApiConfig,
        LogConfig,
        BaseConfig,
        FilterConfig,
    ]:
        aliases.update(config_class.trait_argparse_aliases())
    return RichKVArgParseConfigLoader(
        # TODO: generate alias argparse data from Configurable object directly
        aliases=aliases,
        flags={
            'debug': {
                'value': ({'LogConfig': {'stream_log_level':'DEBUG'}}, 'display debug messages'),
                'add_args': ['-d', '--debug'],
                'section': 'logging',
            },
            'verbose': {
                'value': ({'LogConfig': {'stream_log_level':'INFO'}}, 'display extra information messages'),
                'add_args': ['-v', '--verbose'],
                'section': 'logging',
            },
            'quiet': {
                'value': ({'LogConfig': {'stream_log_level':'ERROR'}}, 'suppress warning messages'),
                'add_args': ['-q', '--quiet'],
                'section': 'logging',
            },
        },
        description=DESCRIPTION,
    )

def config_quiet(config):
    return
    # return log_level_quiet(config.LogConfig.get('stream_log_level', logging.WARNING))

def config_runtime_exception(exc, config):
    ROOT_LOGGER.critical(exc.args[0])
    if not config_quiet(config):
        if 'argparse_loader' in config:
            config.argparse_loader.parser.print_usage()
    exit()

# def load_cli_config(argv=None, has_extra_config=None):
def load_cli_config(argv=None, config=None):
    if config is None:
        config = Config()
    config.argparse_loader = get_argparse_loader()
    try:
        cli_config = config.argparse_loader.load_config(argv)
    except ArgumentError as exc:
        config_runtime_exception(exc, config)
    setup_logging(**cli_config.LogConfig)
    ROOT_LOGGER.info("cli config is \n%s", pprint.pformat(cli_config))
    return cli_config

def load_single_file_config(config_path, config):
    extension_loaders = {
        '.py': PyFileConfigLoader,
        '.json': JSONFileConfigLoader
    }
    _, extension = os.path.splitext(config_path)
    loader_class = extension_loaders.get(extension, None)
    if not loader_class:
        raise ConfigException(
            "invalid config file extension (must be in %s) in file %s" % (
                str(extension_loaders.keys()),
                config_path
            )
        )
    loader = loader_class(config_path, path=config_path)
    return loader.load_config()

# TODO: move to helpers
def expand_relative_path(path, dir):
    path = os.path.expandvars(path)
    path = os.path.normpath(path)
    path = os.path.expanduser(path)
    if not path.startswith('/') and dir:
        dir = os.path.expandvars(dir)
        dir = os.path.normpath(dir)
        dir = os.path.expanduser(dir)
        path = os.path.join(dir, path)
    return os.path.abspath(path)

def validate_config_path(config_path, config=None):
    """
    Return an expanded config path relative to config_dir if provided in config.BaseConfig
    """
    if not config_path:
        return
    if config is None:
        config = Config()
    config_dir = config.BaseConfig.get('config_dir')
    config_path = expand_relative_path(config_path, config_dir)
    if not os.path.exists(config_path):
        raise ConfigFileNotFound(
            "config_path %s does not exist under config_dir %s" % (
                config_path, config_dir
            )
        )
    return config_path

def load_file_config(config=None):
    if config is None:
        config = Config()

    file_config = Config()
    config_paths = list()
    config_dir = None
    if 'config_dir' in config.BaseConfig:
        config_dir = config.BaseConfig.config_dir

    def maybe_add_config_path(config_path):
        config_path = validate_config_path(config_path, config)
        if config_path and config_path not in config_paths:
            config_paths.append(config_path)

    # TODO: generate config file list and config_path from config
    # Config files might be sourced from $CWD or $HOME or /etc/

    maybe_add_config_path(config.BaseConfig.get('config_path'))

    for config_path in config_paths:
        new_config = load_single_file_config(config_path, config)
        ROOT_LOGGER.info("merging file config \n%s", pprint.pformat(new_config))
        file_config.merge(new_config)

    return file_config

def validate_config(config):
    if not all([
        config.XeroApiConfig.get('rsa_key_path'),
        config.XeroApiConfig.get('consumer_key')
    ]):
        raise ConfigException(
            "To connect to the Xero API, you must either specify a Xero API consumer key or a config file containing such a key"
        )


# def load_config(argv=None, extra_config_paths=None, config_path=None):
def load_config(argv=None, proto_config=None):
    """
    Successively merge config files from different sources, overriding the previous.

    Config merge order:
    - proto config          (config provided initially to load_config)
    - local config          (config file specified after analysing proto and cli config)
    - cli config            (command line arguments)
    """
    if proto_config is None:
        proto_config = Config()
    config = RichConfig()
    config.merge_source('proto', proto_config)
    setup_logging(**config.LogConfig)
    cli_config = load_cli_config(argv, config)
    if 'config_path' in cli_config.BaseConfig:
        config.BaseConfig.config_path = cli_config.BaseConfig.config_path
    if 'config_dir' in cli_config.BaseConfig:
        config.BaseConfig.config_dir = cli_config.BaseConfig.config_dir
    if 'stream_log_level' in cli_config.LogConfig:
        config.LogConfig.stream_log_level = cli_config.LogConfig.stream_log_level
    # TODO: replace this wiht custom traitlet subclass "immediate" where settings are applied as soon as they are loaded
    # TODO: implement partial merge like this
    # config.partial_merge(
    #     cli_config, [
    #         ('BaseConfig', ['config_path', 'config_dir']),
    #         ('LogConfig', ['stream_log_level']),
    #     ]
    # )
    file_config = load_file_config(config)
    config.merge_source('file', file_config)
    config.merge_source('cli', cli_config)
    try:
        validate_config(config)
    except ConfigException as exc:
        config_runtime_exception(exc, config)
    ROOT_LOGGER.warning("config is \n%s", pprint.pformat(config))
    return config
