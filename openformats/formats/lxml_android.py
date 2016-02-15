import itertools

from copy import deepcopy
from lxml import etree

from ..exceptions import ParseError, RuleError
from ..handlers import Handler
from ..strings import OpenString


class LxmlAndroidHandler(Handler):
    name = "lxml_Android"
    extension = "xml"

    def parse(self, content):
        # find starting tag
        start = content.index('<resources')

        template = content[:start]
        self.starting_line_number = template.count('\n')
        stringset = []

        self.root = etree.fromstring(content[start:].encode('UTF-8'))

        self.last_comment = ""
        self._order = itertools.count()

        for element in self.root:
            if self._should_ignore(element):
                self.last_comment = ""
                continue
            elif element.tag == etree.Comment:
                self.last_comment = element.text
            elif element.tag == "string":
                string = self._handle_string_tag(element)
                if string is not None:
                    stringset.append(string)
                    self.last_comment = ""
            elif element.tag == "string-array":
                at_least_one = False
                for string in self._handle_string_array_tag(element):
                    if string is not None:
                        stringset.append(string)
                        at_least_one = True
                if at_least_one:
                    self.last_comment = ""
            elif element.tag == "plurals":
                string = self._handle_plurals_tag(element)
                if string is not None:
                    stringset.append(string)
                self.last_comment = ""

        return template + etree.tostring(self.root), stringset

    def _handle_string_tag(self, element):
        try:
            name = element.attrib['name']
        except KeyError:
            raise ParseError(
                "'string' tag on line {} does not have a 'name' "
                "attribute".format(self.starting_line_number +
                                   element.sourceline)
            )
        text = self._extract_inner(element)
        if not text.strip():
            return None
        context = element.attrib.get('product', "")
        string = OpenString(name, text, context=context,
                            order=next(self._order),
                            developer_comment=self.last_comment)
        new_element = self._copy_element(element)
        new_element.text = string.template_replacement
        self.root.replace(element, new_element)
        return string

    def _handle_string_array_tag(self, array_element):
        new_array_element = deepcopy(array_element)
        try:
            name = new_array_element.attrib['name']
        except KeyError:
            raise ParseError(
                "'string-array' tag on line {} does not have a 'name' "
                "attribute".format(self.starting_line_number +
                                   new_array_element.sourceline)
            )
        context = new_array_element.attrib.get('product', "")
        position_count = itertools.count()
        for item_element in new_array_element:
            if item_element.tag != "item":
                raise ParseError(
                    "'{}' element inside 'string-array' tag on line {} is not "
                    "'item'".format(item_element.tag,
                                    self.starting_line_number +
                                    item_element.sourceline)
                )
            text = self._extract_inner(item_element)
            if text.strip():
                string = OpenString("{}[{}]".format(name,
                                                    next(position_count)),
                                    text, context=context,
                                    order=next(self._order),
                                    developer_comment=self.last_comment)
                new_item_element = self._copy_element(item_element)
                new_item_element.text = string.template_replacement
                new_array_element.replace(item_element, new_item_element)
                yield string
        self.root.replace(array_element, new_array_element)

    def _handle_plurals_tag(self, plurals_element):
        new_plurals_element = deepcopy(plurals_element)
        try:
            name = new_plurals_element.attrib['name']
        except KeyError:
            raise ParseError(
                "'plurals' tag on line {} does not have a 'name' attribute".
                format(self.starting_line_number +
                       new_plurals_element.sourceline)
            )
        context = new_plurals_element.attrib.get('product', "")
        strings = {}
        for item_element in new_plurals_element:
            if item_element.tag != "item":
                raise ParseError(
                    "'{}' element inside 'plurals' tag on line {} is not "
                    "'item'".format(item_element.tag,
                                    self.starting_line_number +
                                    item_element.sourceline)
                )
            try:
                quantity = item_element.attrib['quantity']
            except KeyError:
                raise ParseError(
                    "Plural 'item' tag on line {} does not have a 'quantity' "
                    "attribute".format(self.starting_line_number +
                                       item_element.sourceline)
                )
            try:
                rule = self.get_rule_number(quantity)
            except RuleError:
                raise ParseError(
                    "'quantity' attribute in 'item' tag on line {} has an "
                    "invalid value '{}'".format(self.starting_line_number +
                                                item_element.sourceline,
                                                quantity)
                )
            text = self._extract_inner(item_element)
            if not text.strip():
                return None
            strings[rule] = text

        if not strings:
            return None

        string = OpenString(name, strings, context=context,
                            order=next(self._order),
                            developer_comment=self.last_comment)

        for item in new_plurals_element:
            if self.get_rule_number(item.attrib['quantity']) == 5:
                new_item = self._copy_element(item)
                new_item.text = string.template_replacement
                new_plurals_element.replace(item, new_item)
            else:
                new_plurals_element.remove(item)
        self.root.replace(plurals_element, new_plurals_element)

        return string

    @staticmethod
    def _should_ignore(element):
        return not element.attrib.get('translatable', True)

    @staticmethod
    def _extract_inner(element):
        string = etree.tostring(element)
        start = string.index('>') + 1
        end = len(string) - string[::-1].index('<') - 1
        return string[start:end]

    @staticmethod
    def _copy_element(element):
        new_element = deepcopy(element)
        for item in new_element:
            new_element.remove(item)
        return new_element
