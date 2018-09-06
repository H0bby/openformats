# -*- coding: utf-8 -*-

from __future__ import absolute_import

import json
import re
from itertools import count
import pyparsing

from ..exceptions import ParseError
from ..handlers import Handler
from ..strings import OpenString
from ..transcribers import Transcriber
from ..utils.json import DumbJson


class JsonHandler(Handler):
    """
    Responsible for KEYVALUEJSON files that support plurals as per ICU's
    message format.

    Not the full spec of message format is supported. Particularly,
    the following features are *not* supported:
      - the `offset` feature
      - the explicit count rule, e.g. `=0`, `=1`
    """

    name = "KEYVALUEJSON"
    extension = "json"

    PLURAL_ARG = 'plural'
    PLURAL_KEYS_STR = ' '.join(Handler._RULES_ATOI.keys())

    def parse(self, content, **kwargs):
        # Validate that content is JSON
        self.validate_content(content)

        self.transcriber = Transcriber(content)
        source = self.transcriber.source
        self.stringset = []
        self.existing_keys = set()

        try:
            parsed = DumbJson(source)
        except ValueError as e:
            raise ParseError(e.message)
        self._order = count()
        self._extract(parsed)
        self.transcriber.copy_until(len(source))

        return self.transcriber.get_destination(), self.stringset

    def _extract(self, parsed, nest=None):
        if parsed.type == dict:
            for key, key_position, value, value_position in parsed:
                key = self._escape_key(key)
                if nest is not None:
                    key = u"{}.{}".format(nest, key)

                # 'key' should be unique
                if key in self.existing_keys:
                    # Need this for line number
                    self.transcriber.copy_until(key_position)
                    raise ParseError(u"Duplicate string key ('{}') in line {}".
                                     format(key, self.transcriber.line_number))
                self.existing_keys.add(key)

                if isinstance(value, (str, unicode)):
                    if not value.strip():
                        continue

                    # First attempt to parse this as a special node,
                    # e.g. a pluralized string.
                    # If it cannot be parsed that way (returns None),
                    # parse it like a regular string.
                    openstring = self._parse_special(
                        key, value, value_position
                    )
                    if not openstring:
                        openstring = self._get_regular_string(
                            key, value, value_position
                        )
                    if openstring:
                        self.stringset.append(openstring)

                elif isinstance(value, DumbJson):
                    self._extract(value, key)

                else:
                    # Ignore other JSON types (bools, nulls, numbers)
                    pass

        elif parsed.type == list:
            for index, (item, item_position) in enumerate(parsed):
                if nest is None:
                    key = u"..{}..".format(index)
                else:
                    key = u"{}..{}..".format(nest, index)
                if isinstance(item, (str, unicode)):
                    if not item.strip():
                        continue

                    openstring = self._parse_special(key, item, item_position)
                    if not openstring:
                        openstring = self._get_regular_string(
                            key, item, item_position
                        )
                    if openstring:
                        self.stringset.append(openstring)

                elif isinstance(item, DumbJson):
                    self._extract(item, key)
                else:
                    # Ignore other JSON types (bools, nulls, numbers)
                    pass
        else:
            raise ParseError("Invalid JSON")

    def _parse_special(self, key, value, value_position):
        """
        Parse a string that follows a subset of the the ICU message format
        and return an OpenString object.

        For the time being, only the plurals format is supported.
        If `value` doesn't match the proper format, it will return None.
        This method will also update the transcriber accordingly.

        Note: if we want to support more ICU features in the future,
        this would probably have to be refactored.

        :param key: the string key
        :param value: the serialized string that has all the content,
            formatted like this (whitespace irrelevant):
            { item_count, plural,
                one { You have {file_count} file. }
                other { You have {file_count} files. }
            }
        :return: an OpenString or None
        """
        matches = re.match(
            ur'\s*{\s*([A-Za-z-_\d]+)\s*,\s*([A-Za-z_]+)\s*,\s*(.*)}\s*', value
        )
        if not matches:
            return None

        keyword, argument, serialized_strings = matches.groups()

        if argument == self.PLURAL_ARG:
            return self._parse_pluralized_string(
                key, keyword, value, value_position,
                serialized_strings
            )

        return None

    def _get_regular_string(self, key, value, value_position):
        """
        Return a new OpenString based on the given key and value
        and update the transcriber accordingly.

        :param key: the string key
        :param value: the translation string
        :return: an OpenString or None
        """
        openstring = OpenString(key, value, order=next(self._order))
        self.transcriber.copy_until(value_position)
        self.transcriber.add(openstring.template_replacement)
        self.transcriber.skip(len(value))

        return openstring

    def _parse_pluralized_string(self, key, keyword, value, value_position,
                                 serialized_strings):
        """
        Parse `serialized_strings` in order to find and return all included
        pluralized strings.

        :param key: the string key
        :param keyword: the message key, e.g. `item_count` in:
            '{ item_count, plural, one { {cnt} tip } other { {cnt} tips } }'
        :param serialized_strings: the plurals in the form of multiple
            occurrences of the following (whitespace irrelevant):
            '<plurality_rule_str> { <content> }',
            e.g. 'one { I ate {count} apple. } other { I ate {count} apples. }'
        :return: A pluralized OpenString instance or None
        """
        # The official plurals format supports defining an integer instead
        # of the name of the plural rule, using a syntax like "=1" or "=2"
        # We do not support this at the moment, but we want to have these
        # strings be handled as non pluralized.
        equality_item = (
            pyparsing.Literal('=') + pyparsing.Word(pyparsing.alphanums) +
            pyparsing.nestedExpr('{', '}')
        )
        equality_matches = pyparsing.originalTextFor(equality_item)\
            .searchString(serialized_strings)

        # If any match is found using this syntax, do not parse this
        # as pluralized
        if len(equality_matches) > 0:
            return None

        # Each item should be like '<proper_plurality_rule_str> {<content>}'
        # Nested braces ({}) inside <content> are allowed.
        #
        # Note:
        # Be sure to ignore single quotes ('), otherwise strings that include
        # one quote in one plural and another one in another plural, will be
        # parsed as pluralized but with less rules than they actually have.
        # (matching will actually include content from multiple rules combined,
        # instead of separating the content per rule). This seems like a
        # pyparsing bug. Any other character that could be a potential
        # separator doesn't seem cause any problem.
        valid_plural_item = (
            pyparsing.oneOf(self.PLURAL_KEYS_STR) +
            pyparsing.nestedExpr('{', '}', ignoreExpr=pyparsing.Literal("'"))
        )

        # We need to make sure that the plural rules are valid.
        # Therefore, we also match any <alphanumeric> {<content>} string
        # and see if there are differences compared to the valid results
        # we got above.
        any_plural_item = (
            pyparsing.Word(pyparsing.alphanums) +
            pyparsing.nestedExpr('{', '}', ignoreExpr=pyparsing.Literal("'"))
        )

        all_matches = pyparsing.originalTextFor(any_plural_item).searchString(
            serialized_strings
        )
        self._validate_plural_content_format(
            key, value, serialized_strings, all_matches
        )

        # Create a list of serialized plural items, e.g.:
        # ['one { I ate {count} apple. }']
        valid_matches = pyparsing.originalTextFor(valid_plural_item)\
            .searchString(serialized_strings)

        # Make sure the plurality rules are valid
        # If not, an error will be raised
        if len(valid_matches) != len(all_matches):
            self._handle_invalid_plural_format(
                serialized_strings, any_plural_item, key, value
            )

        # Create a list of tuples [(plurality_str, content_with_braces)]
        all_strings_list = [
            self._parse_plural_content(match[0])
            for match in valid_matches
        ]

        # Convert it to a dict like { 'one': '{...}', 'other': '{...}' }
        # And then to a dict like { 1: '...', 5: '...' }
        all_strings_dict = dict(all_strings_list)
        all_strings_dict = {
            self.get_rule_number(plurality_str): content[1:-1]
            for plurality_str, content in all_strings_dict.iteritems()
        }

        openstring = OpenString(
            key, all_strings_dict, pluralized=True, order=next(self._order)
        )

        # ICU's message format contains an arbitrary string at the beginning.
        # We need to include that in the template, because otherwise we won't
        # have enough information to recreate it in the compilation phase.
        # e.g. in { item_count, plural, other {You have {file_count} files.} }
        # `item_count` is a string set by the user, it's not a standard.
        # We'll keep everything up to the comma that follows the 'plural'
        # argument.
        current_pos = value.index(keyword) + len(keyword)
        current_pos = value.index(self.PLURAL_ARG, current_pos)\
            + len(self.PLURAL_ARG)
        current_pos = value.index(',', current_pos) + len(',')

        # We want to preserve the original document as much as possible,
        # so we'll add any whitespace between the comma and the
        # first plurality rule, e.g. 'one'
        current_pos = value.index(all_strings_list[0][0], current_pos)

        # Also include whitespace between the last two closing braces
        second_last_closing_brace = value.rfind('}', 0, value.rfind('}')) + 1
        string_to_replace = value[current_pos:second_last_closing_brace]

        self.transcriber.copy_until(value_position + current_pos)
        self.transcriber.add(openstring.template_replacement)
        self.transcriber.skip(len(string_to_replace))

        return openstring

    def _validate_plural_content_format(self, key, value, serialized_strings,
                                        all_matches):
        """
        Make sure the serialized content is properly formatted
        as one or more pluralized strings.
        :param key: the string key
        :param value: the whole value of the key, e.g.
            { item_count, plural, zero {...} one {...} other {...}}
        :param serialized_strings: the part of the value that holds the
            string information only, e.g.
            zero {...} one {...} other {...}
        :param all_matches: a pyparsing element that matches all strings
            formatted like '<alphanumeric> {...}'

        :raise: ParseError
        """
        # Replace all matches with spaces in the given string.
        remaining_str = serialized_strings
        for match in all_matches:
            remaining_str = remaining_str.replace(match[0], '')

        # Then make sure all whitespace is removed as well
        # Special characters may be present with double backslashes,
        # e.g. \\n
        remaining_str = remaining_str.replace('\\n', '\n')\
            .replace('\\t', '\t')\
            .strip()

        if len(remaining_str) > 0:
            raise ParseError(
                'Invalid format of pluralized entry '
                'with key: "{}", serialized translations: "{}". '
                'Could not parse the following chunk: "{}". '
                'There are some invalid braces ("{{", "}}") '
                'in the translations.'.format(
                    key, serialized_strings, remaining_str
                )
            )

    def _handle_invalid_plural_format(self, serialized_strings,
                                      any_plural_item, key, value):
        """
        Raise a descriptive ParseError exception when the serialized
        translation string of a plural string is not properly formatted.

        :param serialized_strings:
        :param any_plural_item: a forgiving pyparsing element that matches all
            strings formatted like '<alphanumeric> {...}'

        :raise: ParseError
        """
        all_matches = any_plural_item.searchString(serialized_strings)
        all_keys = [match[0] for match in all_matches]

        invalid_rules = [
            rule for rule in all_keys
            if rule not in Handler._RULES_ATOI.keys()
        ]
        raise ParseError(
            'Invalid plural rule(s): {} in pluralized entry '
            'with key: {}, value: "{}". '
            'Allowed values are: {}'.format(
                ', '.join(invalid_rules),
                key, value,
                ', '.join(Handler._RULES_ATOI.keys())
            )
        )

    @staticmethod
    def _parse_plural_content(string):
        # Find the content inside the brackets
        opening_brace_index = string.index('{')
        content = string[opening_brace_index:]

        # Find the plurality type (zero, one, etc)
        plurality = string[:opening_brace_index].strip()

        return plurality, content

    @staticmethod
    def _escape_key(key):
        key = key.replace(DumbJson.BACKSLASH,
                          u''.join([DumbJson.BACKSLASH, DumbJson.BACKSLASH]))
        key = key.replace(u".", u''.join([DumbJson.BACKSLASH, '.']))
        return key

    def compile(self, template, stringset, **kwargs):
        # Lets play on the template first, we need it to not include the hashes
        # that aren't in the stringset. For that we will create a new stringset
        # which will have the hashes themselves as strings and compile against
        # that. The compilation process will remove any string sections that
        # are absent from the stringset. Next we will call `_clean_empties`
        # from the template to clear out any `...,  ,...` or `...{ ,...`
        # sequences left. The result will be used as the actual template for
        # the compilation process

        stringset = list(stringset)

        fake_stringset = [
            OpenString(openstring.key,
                       openstring.template_replacement,
                       order=openstring.order,
                       pluralized=openstring.pluralized)
            for openstring in stringset
        ]
        new_template = self._replace_translations(
            template, fake_stringset, False
        )
        new_template = self._clean_empties(new_template)

        return self._replace_translations(new_template, stringset, True)

    def _replace_translations(self, template, stringset, is_real_stringset):
        self.transcriber = Transcriber(template)
        template = self.transcriber.source

        self.stringset = stringset
        self.stringset_index = 0

        parsed = DumbJson(template)
        self._insert(parsed, is_real_stringset)

        self.transcriber.copy_until(len(template))
        return self.transcriber.get_destination()

    def _insert(self, parsed, is_real_stringset):
        if parsed.type == dict:
            return self._insert_from_dict(parsed, is_real_stringset)
        elif parsed.type == list:
            return self._insert_from_list(parsed, is_real_stringset)

    def _insert_item(self, value, value_position, is_real_stringset):
        at_least_one = False

        if isinstance(value, (str, unicode)):
            string = self._get_next_string()
            string_exists = string is not None

            templ_replacement = string.template_replacement \
                if string_exists else None

            # Pluralized string
            if string_exists and string.pluralized \
                    and templ_replacement in value:
                at_least_one = True
                self._insert_plural_string(
                    value, value_position, string, is_real_stringset
                )

            # Regular string
            elif (string_exists and value == templ_replacement):
                at_least_one = True
                self._insert_regular_string(
                    value, value_position, string, is_real_stringset
                )

            else:
                # Anything else: just remove the current section
                self._copy_until_and_remove_section(
                    value_position + len(value) + 1
                )

        elif isinstance(value, DumbJson):
            items_still_left = self._insert(value, is_real_stringset)

            if not items_still_left:
                self._copy_until_and_remove_section(value.end + 1)
            else:
                at_least_one = True

        else:
            # 'value' is a python value allowed by JSON (integer,
            # boolean, null), skip it
            at_least_one = True

        return at_least_one

    def _insert_from_dict(self, parsed, is_real_stringset):
        at_least_one = False

        for key, key_position, value, value_position in parsed:

            self.transcriber.copy_until(key_position - 1)
            self.transcriber.mark_section_start()

            tmp_at_least_one = self._insert_item(
                value, value_position, is_real_stringset
            )

            if tmp_at_least_one:
                at_least_one = True

        return at_least_one

    def _insert_from_list(self, parsed, is_real_stringset):
        at_least_one = False

        for value, value_position in parsed:
            self.transcriber.copy_until(value_position - 1)
            self.transcriber.mark_section_start()

            tmp_at_least_one = self._insert_item(
                value, value_position, is_real_stringset
            )

            if tmp_at_least_one:
                at_least_one = True

        return at_least_one

    def _insert_plural_string(self, value, value_position, string,
                              is_real_stringset):
        templ_replacement = string.template_replacement
        replacement_pos = value.find(templ_replacement)

        if is_real_stringset:
            replacement = self.serialize_pluralized_string(
                string, delimiter=' '
            )
        else:
            replacement = templ_replacement

        self.transcriber.copy_until(
            value_position + replacement_pos
        )
        self.transcriber.add(replacement)

        self.transcriber.skip(len(templ_replacement))
        self.transcriber.copy(
            len(value) - replacement_pos - len(templ_replacement)
        )
        self.stringset_index += 1

    def _insert_regular_string(self, value, value_position, string,
                               is_real_stringset):
        self.transcriber.copy_until(value_position)
        self.transcriber.add(string.string)
        self.transcriber.skip(len(value))
        self.stringset_index += 1

    def _copy_until_and_remove_section(self, pos):
        """
        Copy characters to the transcriber until the given position,
        then end the current section and remove it altogether.
        """
        self.transcriber.copy_until(pos)
        self.transcriber.mark_section_end()
        self.transcriber.remove_section()

    def validate_content(self, content):
        """Validate that a given string is valid JSON format.

        :param str content: the content to parse
        :raise ParseError: if the content is not valid JSON format
        """
        try:
            json.loads(content)
        except ValueError as e:
            raise ParseError(e.message)

    @classmethod
    def serialize_pluralized_string(cls, pluralized_string, delimiter=' '):
        """
        Serialize the given pluralized_string into a suitable format
        for adding it to the document in the compilation phase.

        This essentially concatenates the plural rule strings and translations
        for each rule into one string.

        For example:
        ' ' delimiter => 'one { {cnt} chip. } other { {cnt} chips. }'
        '\n' delimiter => 'one { {cnt} chip. }\nother { {cnt} chips. }'

        :param pluralized_string: an OpenString that is pluralized
        :param delimiter: a string to use for separating entries
        :return: a string
        """
        plural_list = [
            u'{} {{{}}}'.format(
                Handler.get_rule_string(rule),
                translation
            )
            for rule, translation in pluralized_string.string.iteritems()
        ]
        return delimiter.join(plural_list)

    def _clean_empties(self, compiled):
        """ If sections were removed, clean leftover commas, brackets etc.

            Eg:
                '{"a": "b", ,"c": "d"}' -> '{"a": "b", "c": "d"}'
                '{, "a": "b", "c": "d"}' -> '{"a": "b", "c": "d"}'
                '["a", , "b"]' -> '["a", "b"]'
        """
        while True:
            # First key-value of a dict was removed
            match = re.search(r'{\s*,', compiled)
            if match:
                compiled = u"{}{{{}".format(compiled[:match.start()],
                                            compiled[match.end():])
                continue

            # Last key-value of a dict was removed
            match = re.search(r',\s*}', compiled)
            if match:
                compiled = u"{}}}{}".format(compiled[:match.start()],
                                            compiled[match.end():])
                continue

            # First item of a list was removed
            match = re.search(r'\[\s*,', compiled)
            if match:
                compiled = u"{}[{}".format(compiled[:match.start()],
                                           compiled[match.end():])
                continue

            # Last item of a list was removed
            match = re.search(r',\s*\]', compiled)
            if match:
                compiled = u"{}]{}".format(compiled[:match.start()],
                                           compiled[match.end():])
                continue

            # Intermediate key-value of a dict or list was removed
            match = re.search(r',\s*,', compiled)
            if match:
                compiled = u"{},{}".format(compiled[:match.start()],
                                           compiled[match.end():])
                continue

            # No substitutions happened, break
            break

        return compiled

    def _get_next_string(self):
        try:
            return self.stringset[self.stringset_index]
        except IndexError:
            return None

    @classmethod
    def escape(cls, string):
        return u''.join(cls._escape_generator(string))
        # btw, this seems equivalent to
        # return json.dumps(string, ensure_ascii=False)[1:-1]

    @staticmethod
    def _escape_generator(string):
        for symbol in string:
            if symbol == DumbJson.DOUBLE_QUOTES:
                yield DumbJson.BACKSLASH
                yield DumbJson.DOUBLE_QUOTES
            elif symbol == DumbJson.BACKSLASH:
                yield DumbJson.BACKSLASH
                yield DumbJson.BACKSLASH
            elif symbol == DumbJson.BACKSPACE:
                yield DumbJson.BACKSLASH
                yield u'b'
            elif symbol == DumbJson.FORMFEED:
                yield DumbJson.BACKSLASH
                yield u'f'
            elif symbol == DumbJson.NEWLINE:
                yield DumbJson.BACKSLASH
                yield u'n'
            elif symbol == DumbJson.CARRIAGE_RETURN:
                yield DumbJson.BACKSLASH
                yield u'r'
            elif symbol == DumbJson.TAB:
                yield DumbJson.BACKSLASH
                yield u't'
            else:
                yield symbol

    @classmethod
    def unescape(cls, string):
        return u''.join(cls._unescape_generator(string))
        # btw, this seems equivalent to
        # return json.loads(u'"{}"'.format(string))

    @staticmethod
    def _unescape_generator(string):
        # I don't like this aldschool approach, but we may have to rewind a bit
        ptr = 0
        while True:
            if ptr >= len(string):
                break

            symbol = string[ptr]

            if symbol != DumbJson.BACKSLASH:
                yield symbol
                ptr += 1
                continue

            try:
                next_symbol = string[ptr + 1]
            except IndexError:
                yield DumbJson.BACKSLASH
                ptr += 1
                continue

            if next_symbol in (DumbJson.DOUBLE_QUOTES, DumbJson.FORWARD_SLASH,
                               DumbJson.BACKSLASH):
                yield next_symbol
                ptr += 2
            elif next_symbol == u'b':
                yield DumbJson.BACKSPACE
                ptr += 2
            elif next_symbol == u'f':
                yield DumbJson.FORMFEED
                ptr += 2
            elif next_symbol == u'n':
                yield DumbJson.NEWLINE
                ptr += 2
            elif next_symbol == u'r':
                yield DumbJson.CARRIAGE_RETURN
                ptr += 2
            elif next_symbol == u't':
                yield DumbJson.TAB
                ptr += 2
            elif next_symbol == u'u':
                unicode_escaped = string[ptr:ptr + 6]
                try:
                    unescaped = unicode_escaped.decode('unicode-escape')
                except Exception:
                    yield DumbJson.BACKSLASH
                    yield u'u'
                    ptr += 2
                    continue
                if len(unescaped) != 1:
                    yield DumbJson.BACKSLASH
                    yield u'u'
                    ptr += 2
                    continue
                yield unescaped
                ptr += 6

            else:
                yield symbol
                ptr += 1


class ChromeI18nHandler(JsonHandler):
    """Responsible for CHROME files, based on the JsonHandler."""

    name = "CHROME"
    STRING_KEY = "message"

    def compile(self, template, stringset, **kwargs):
        """Compile a template back to a stringset.

        :param str template: the template string
        :param stringset: generator that holds a list of OpenString objects
        :return: the compiled template
        :rtype: str
        """
        stringset = list(stringset)
        return self._replace_translations(
            template, stringset, is_real_stringset=True
        )

    def _get_regular_string(self, key, value, value_position):
        """
        Return a new OpenString based on the given key and value
        and update the transcriber accordingly.

        :param key: the string key
        :param value: the translation string
        :return: an OpenString or None
        """
        # We should only parse keys with a specific value ("message"). All
        # others should be added in the template
        # Key's format is parent_name.key_name (ex. test.message,
        # test.description etc)
        if not key.endswith(self.STRING_KEY):
            return None
        # Check if the given key has a description field
        description = self._get_description(key)
        # Create an OpenString object with the description as the developer
        # comment
        openstring = OpenString(
            key, value, order=next(self._order), developer_comment=description
        )
        self.transcriber.copy_until(value_position)
        self.transcriber.add(openstring.template_replacement)
        self.transcriber.skip(len(value))

        return openstring

    def _get_description(self, key):
        """Return the 'description' child for a given key

        :param str key: the key to search against
        :return: the description string
        :rtype: str
        """
        key_split = key.split('.')
        try:
            return self.json_dict[key_split[0]]['description']
        except KeyError:
            return ''

    def _copy_until_and_remove_section(self, pos):
        """
        Copy characters to the transcriber until the given position,
        then end the current section.
        """
        self.transcriber.copy_until(pos)
        self.transcriber.mark_section_end()
        # Unlike the JSON format, do not remove the remaining section of the
        # template

    def validate_content(self, content):
        """Validate that a given string is valid Chromei18n file.

        :param str content: the content to parse
        :raise ParseError: if the content is not valid JSON format
        """
        try:
            # Save the JSON dict for later use
            self.json_dict = json.loads(content)
        except ValueError as e:
            raise ParseError(e.message)
