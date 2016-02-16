import itertools

from copy import deepcopy
from lxml import etree

from ..exceptions import ParseError, RuleError
from ..handlers import Handler
from ..strings import OpenString
from ..transcribers import LxmlTranscriber


class LxmlAndroidHandler(Handler):
    name = "lxml_Android"
    extension = "xml"

    def parse(self, content):
        # find starting tag
        resources_tag_position = content.index('<resources')

        template = content[:resources_tag_position]
        self.starting_line_number = template.count('\n')
        stringset = []

        self.transcriber = LxmlTranscriber(
            content[resources_tag_position:].encode("UTF-8")
        )

        self.last_comment = ""
        self._order = itertools.count()

        for element in self.transcriber:
            if self._should_ignore(element):
                self.last_comment = ""
                continue
            elif element.tag == etree.Comment:
                self.last_comment = element.extract_inner()
            elif element.tag == "string":
                string = self._handle_string(element)
                if string is not None:
                    stringset.append(string)
                    self.last_comment = ""
            elif element.tag == "string-array":
                at_least_one = False
                for string in self._handle_string_array(element):
                    if string is not None:
                        stringset.append(string)
                        at_least_one = True
                if at_least_one:
                    self.last_comment = ""
            elif element.tag == "plurals":
                string = self._handle_plurals(element)
                if string is not None:
                    stringset.append(string)
                    self.last_comment = ""

        template += self.transcriber.get_destination()
        return template, stringset

    def _handle_string(self, string_element):
        try:
            name = string_element.attrib['name']
        except KeyError:
            raise ParseError(
                "'string' tag on line {} does not have a 'name' "
                "attribute".format(self.starting_line_number +
                                   string_element.sourceline)
            )
        text = string_element.extract_inner()
        if not text.strip():
            return None
        context = string_element.attrib.get('product', "")
        string = OpenString(name, text, context=context,
                            order=next(self._order),
                            developer_comment=self.last_comment)
        string_element.replace_inner(string.template_replacement)
        return string

    def _handle_string_array(self, array_element):
        try:
            name = array_element.attrib['name']
        except KeyError:
            raise ParseError(
                "'string-array' tag on line {} does not have a 'name' "
                "attribute".format(self.starting_line_number +
                                   array_element.sourceline)
            )
        context = array_element.attrib.get('product', "")
        position_count = itertools.count()
        for item in array_element:
            if item.tag != "item":
                raise ParseError(
                    "'{}' element inside 'string-array' tag on line {} is not "
                    "'item'".format(item.tag,
                                    self.starting_line_number +
                                    item.sourceline)
                )
            text = item.extract_inner()
            if not text.strip():
                continue

            string = OpenString("{}[{}]".format(name, next(position_count)),
                                text, context=context, order=next(self._order),
                                developer_comment=self.last_comment)
            item.replace_inner(string.template_replacement)
            yield string

    def _handle_plurals(self, plurals_element):
        try:
            name = plurals_element.attrib['name']
        except KeyError:
            raise ParseError(
                "'plurals' tag on line {} does not have a 'name' attribute".
                format(self.starting_line_number + plurals_element.sourceline)
            )
        context = plurals_element.attrib.get('product', "")
        strings = {}
        for item in plurals_element:
            if item.tag != "item":
                raise ParseError(
                    "'{}' element inside 'plurals' tag on line {} is not "
                    "'item'".format(item.tag,
                                    self.starting_line_number +
                                    item.sourceline)
                )
            try:
                quantity = item.attrib['quantity']
            except KeyError:
                raise ParseError(
                    "Plural 'item' tag on line {} does not have a 'quantity' "
                    "attribute".format(self.starting_line_number +
                                       item.sourceline)
                )
            try:
                rule = self.get_rule_number(quantity)
            except RuleError:
                raise ParseError(
                    "'quantity' attribute in 'item' tag on line {} has an "
                    "invalid value '{}'".format(self.starting_line_number +
                                                item.sourceline,
                                                quantity)
                )
            text = item.extract_inner()
            if not text.strip():
                return None
            strings[rule] = text

        if not strings:
            return None

        string = OpenString(name, strings, context=context,
                            order=next(self._order),
                            developer_comment=self.last_comment)

        # Now that we have the hash from the string, lets make another pass to
        # replace the <item>s; we will only keep the first item
        iterator = iter(plurals_element)
        first = next(iterator)
        first.replace_inner(string.template_replacement)
        for item in iterator:  # Drop the rest
            item.drop()

        return string

    @staticmethod
    def _should_ignore(element):
        return not element.attrib.get('translatable', True)

    def compile(self, template, stringset):
        resources_tag_position = template.index('<resources')
        compiled = template[:resources_tag_position]
        self.transcriber = LxmlTranscriber(template[resources_tag_position:])

        self.stringset = stringset
        self.stringset_index = 0

        for element in self.transcriber:
            if element.tag == "string":
                self._compile_string(element)
            elif element.tag == "string-array":
                self._compile_string_array(element)
            elif element.tag == "plurals":
                self._compile_plurals(element)

        compiled += self.transcriber.get_destination()
        return compiled

    def _compile_string(self, string_element):
        next_string = self._get_next_string()

        if (next_string is not None and
                next_string.template_replacement ==
                string_element.extract_inner()):
            # Found one to replace
            self.stringset_index += 1
            string_element.replace_inner(next_string.string)
        else:
            # Didn't find it, must remove
            string_element.drop()

    def _compile_string_array(self, array_element):
        at_least_one = False
        for item in array_element:
            next_string = self._get_next_string()
            if (next_string is not None and
                    next_string.template_replacement == item.extract_inner()):
                # Found one to replace
                self.stringset_index += 1
                at_least_one = True
                item.replace_inner(next_string.string)
            else:
                # Didn't find it, must remove
                item.drop()

        if not at_least_one:
            # Didn't make any replacements, must drop the whole string-array
            array_element.drop()

    def _compile_plurals(self, plurals_element):
        # We expect a single <item> tag here
        item = next(iter(plurals_element))

        next_string = self._get_next_string()
        if (next_string is not None and
                next_string.template_replacement == item.extract_inner()):
            # Found one to replace
            self.stringset_index += 1

            # We must first drop the existing <item> in the template
            for _item in plurals_element:
                _item.drop()

            for rule, string in sorted(next_string.string.items(),
                                       key=lambda i: i[0]):
                new_item = deepcopy(item)
                new_item.attrib['quantity'] = self.get_rule_string(rule)
                new_item.replace_inner(string)
                plurals_element.append(new_item)
        else:
            # Didn't find it, must remove
            plurals_element.drop()

    def _get_next_string(self):
        try:
            return self.stringset[self.stringset_index]
        except IndexError:
            return None
