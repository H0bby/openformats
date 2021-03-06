# -*- coding: utf-8 -*-
from __future__ import absolute_import

import six
import yaml

from openformats.formats.yaml.constants import (YAML_DICT_ID, YAML_LIST_ID,
                                                YAML_STRING_ID)


"""
YAML representers
"""


def unicode_representer(dumper, data):
    tag = getattr(data, 'tag', YAML_STRING_ID)
    return yaml.ScalarNode(tag=tag, value=data)


def folded_unicode_representer(dumper, data):
    return dumper.represent_scalar(YAML_STRING_ID, data, style='>')


def literal_unicode_representer(dumper, data):
    return dumper.represent_scalar(YAML_STRING_ID, data, style='|')


def double_quoted_unicode_representer(dumper, data):
    tag = getattr(data, 'tag', YAML_STRING_ID)
    return dumper.represent_scalar(tag, data, style='"')


def single_quoted_unicode_representer(dumper, data):
    tag = getattr(data, 'tag', YAML_STRING_ID)
    return dumper.represent_scalar(tag, data, style="'")


def block_list_representer(dumper, data):
    return dumper.represent_sequence(YAML_LIST_ID, data, flow_style=False)


def flow_list_representer(dumper, data):
    return dumper.represent_sequence(YAML_LIST_ID, data, flow_style=True)


def ordered_dict_representer(dumper, data):
    return dumper.represent_dict(six.iteritems(data))


def block_style_ordered_dict_representer(dumper, data):
    return dumper.represent_mapping(YAML_DICT_ID, six.iteritems(data),
                                    flow_style=False)


def flow_style_ordered_dict_representer(dumper, data):
    return dumper.represent_mapping(YAML_DICT_ID, six.iteritems(data),
                                    flow_style=True)
