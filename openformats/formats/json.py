from __future__ import absolute_import

import json
from collections import OrderedDict

from ..handlers import Handler
from ..strings import OpenString


class JsonHandler(Handler):
    name = "json"
    extension = "json"

    def parse(self, content):
        parsed = json.loads(content, object_pairs_hook=OrderedDict)
        assert isinstance(parsed, dict)

        template = OrderedDict()

        self.stringset = []

        self._extract_dict(parsed, nest=None, template=template)

        return json.dumps(template, indent=2), self.stringset

    def _extract_dict(self, parsed, nest, template):
        for key, value in parsed.iteritems():
            if nest is None:
                string_key = key
            else:
                string_key = "{}.{}".format(nest, key)
            if isinstance(value, unicode):
                string = OpenString(string_key, value)
                template[key] = string.template_replacement
                self.stringset.append(string)
            elif isinstance(value, dict):
                self._extract_dict(value,
                                   nest=string_key,
                                   template=template.setdefault(key,
                                                                OrderedDict()))
            elif isinstance(value, list):
                self._extract_list(value, nest=string_key,
                                   template=template.setdefault(key, []))

    def _extract_list(self, parsed, nest, template):
        for i, list_item in enumerate(parsed):
            string_key = "{}..{}..".format(nest, i)
            if isinstance(list_item, unicode):
                string = OpenString(string_key, list_item)
                self.stringset.append(string)
                template.append(string.template_replacement)
            elif isinstance(list_item, dict):
                template.append(OrderedDict())
                self._extract_dict(list_item, nest="{}..{}..".format(nest, i),
                                   template=template[-1])
            elif isinstance(list_item, list):
                template.append([])
                self._extract_list(list_item, nest="{}..{}..".format(nest, i),
                                   template=template[-1])

    def compile(self, template, stringset):
        parsed = json.loads(template, object_pairs_hook=OrderedDict)
        self.stringset = stringset
        self.stringset_index = 0

        self._intract(parsed)

        return json.dumps(parsed, indent=2)

    def _intract(self, parsed):
        if isinstance(parsed, dict):
            iterator = parsed.iteritems()
        elif isinstance(parsed, list):
            iterator = enumerate(parsed)

        to_delete = []
        for key, value in iterator:
            next_string = self._get_next_string()
            if isinstance(value, unicode):
                if (next_string and
                        next_string.template_replacement == value):
                    parsed[key] = next_string.string
                    self.stringset_index += 1
                else:
                    to_delete.append(key)
            else:
                all_deleted = self._intract(value)
                if all_deleted:
                    to_delete.append(key)

        all_deleted = len(to_delete) == len(parsed)

        for key in to_delete[::-1]:
            del parsed[key]

        return all_deleted

    def _get_next_string(self):
        try:
            return self.stringset[self.stringset_index]
        except IndexError:
            return None
