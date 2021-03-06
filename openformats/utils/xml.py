import re
from itertools import count

import six

from openformats.utils.compat import ensure_unicode

from ..transcribers import Transcriber


def escape(string, inline_tags, escape_text):
    """ Escape xml strings without escaping special tags.

    For example: Android format need to generally escape double quote chars,
    except if they belong to an inline tag which must be skipped.

    :param str string: string to be escaped
    :param list inline_tags: inline tags that need special handling, for
        example 'a' represents <a></a> tag
    :param lambda escape_text: lambda function to escape text
    :return: escaped string
    :rtype: unicode
    """

    # Lets temporarily make this into an xml tag
    wrapped_text = u"<x>{}</x>".format(string)  # btw, `<x>` is allowed
    transcriber = Transcriber(wrapped_text)
    # Re-assign source because of newlines
    wrapped_text = transcriber.source
    try:
        root = NewDumbXml(wrapped_text)
        _escape_tag(root, transcriber, inline_tags, escape_text)
    except DumbXmlSyntaxError:
        # If opening-closing tags do not match within text, revert to the
        # old behaviour
        return escape_text(string)
    # Discard the temporary outer `<x>` tags
    return transcriber.get_destination()[3:-4]


def _escape_tag(root, transcriber, inline_tags, escape_text):
    if root.text is None:
        # This is a single tag, eg <br />
        if root.tag in inline_tags:
            transcriber.copy_until(root.tail_position)
        else:
            transcriber.add(escape_text(
                transcriber.source[root.position:root.tail_position]
            ))
            transcriber.skip_until(root.tail_position)

    else:
        # Opening tag
        if root.tag in inline_tags:
            # root: <out> first <in> middle </in> last </out> tail
            # ptr:       ^
            transcriber.copy_until(root.text_position)
        else:
            # Lets escape the opening tag
            transcriber.add(escape_text(
                transcriber.source[root.position:root.text_position]
            ))
            # root: <out> first <in> middle </in> last </out> tail
            # ptr:       ^
            transcriber.skip_until(root.text_position)

        # Content
        transcriber.add(escape_text(root.text))
        # root: <out> first <in> middle </in> last </out> tail
        # ptr:              ^
        transcriber.skip(len(root.text))

        # Inner tags
        for child in root:
            _escape_tag(child, transcriber, inline_tags, escape_text)

        # Closing tag
        if root.tag in inline_tags:
            # root: <out> first <in> middle </in> last </out> tail
            # ptr:                                           ^
            transcriber.copy_until(root.tail_position)
        else:
            # Lets escape the closing tag
            transcriber.add(escape_text(
                transcriber.source[root.content_end:root.tail_position]
            ))
            # root: <out> first <in> middle </in> last </out> tail
            # ptr:                                           ^
            transcriber.skip_until(root.tail_position)

    # Tail
    transcriber.add(escape_text(root.tail))
    # root: <out> first <in> middle </in> last </out> tail
    # ptr:                                                ^
    transcriber.skip_until(root.end)


class DumbXml(object):
    """
        Describes and XML tag with its contents.

        The fact that the content starts with a '<' and ends with a '>' is
        assumed.

        You can iterate over the contents with the `find` method, which
        takes a tag name, a list of tag names or nothing as arguments and
        yields
    """

    OPENING_TAG_PAT = r'^\s*\<(?P<name>[^\s\n\>]+)(?P<attrs>[^\>]*)\>'
    ATTR_PAT = r'\b(?P<key>[^=]+)="(?P<value>[^"]+)"'
    COMMENT = "!--"
    SINGLE_TAG_PAT = r'/\s*\>$'

    def __init__(self, content):
        """
            Does some parsing and sets the following attributes to `self`:

            * content: The content of the tag, including the opening/closing
                tags
            * name: The name of the tag
            * attrs: A dictionary of all the attributes of the tag with their
                values
            * inner_offset: the place of the character where the inner content
                of the tag starts, aka the length of the opening tag
            * inner: the inner content of the tag
        """

        # Fix regex encoding
        self.opening_tag_pat = re.compile(ensure_unicode(self.OPENING_TAG_PAT),
                                          re.DOTALL)
        self.attr_pat = re.compile(ensure_unicode(self.ATTR_PAT))
        self.single_tag_pat = re.compile(ensure_unicode(self.SINGLE_TAG_PAT))

        self.content = content

        if self.content[:4] == "<!--":
            # Special case for comment
            self.inner_offset = 4
            self.name = self.COMMENT
            self.attrs = {}
            self.inner = self.content[4:self.content.index("-->")]
            return

        opening_match = self.opening_tag_pat.search(content)
        self.inner_offset = opening_match.end()
        self.name = opening_match.groupdict()['name']
        attrs = opening_match.groupdict()['attrs']
        self.attrs = {}
        for match in self.attr_pat.finditer(attrs):
            self.attrs[match.groupdict()['key']] = match.groupdict()['value']

        closing_start, closing_end = self.find_closing(0)

        self.inner = self.content[opening_match.end():closing_start]

    def find(self, tags=[]):
        if isinstance(tags, (six.binary_type, six.text_type)):
            tags = [tags]

        if not tags:
            pat = re.compile(ensure_unicode(r'\<'), re.DOTALL)
        else:
            pat = re.compile(
                ensure_unicode(r'\<(?:{})'.
                               format('|'.join((re.escape(tag)
                                                for tag in tags)))),
                re.DOTALL
            )

        for match in pat.finditer(self.content):
            if match.start() == 0 or self._is_within_comment(match):
                continue
            closing_start, closing_end = self.find_closing(match.start())
            found = DumbXml(self.content[match.start():closing_end])
            if not tags or found.name in tags:
                offset = match.start()
                yield found, offset

    def find_closing(self, start):
        # assume start is on a '<'

        if self.content[start:start + 4] == "<!--":
            # Special case for comment
            closing_start = self.content[start:].index("-->")
            return start + closing_start, start + closing_start + 3

        opening_match = self.opening_tag_pat.search(self.content[start:])

        if self.single_tag_pat.search(opening_match.group()):
            # Single tag, eg `<foo a="b" />`
            return start + opening_match.end(), start + opening_match.end()

        tag_name = opening_match.groupdict()['name']
        tag_pat = re.compile(
            ensure_unicode(
                r'\<(?:(?:{tag_name})|(?:/{tag_name}\>))'.
                format(tag_name=re.escape(tag_name))
            )
        )
        match_generator = tag_pat.finditer(self.content[start:])
        first_match = next(match_generator)
        assert first_match and first_match.start() == 0 and\
            first_match.group()[1] != '/'
        count = 1
        for match in match_generator:
            matched = match.group()
            if matched[1] == '/' or matched == "-->":
                # closing tag
                count -= 1
            else:
                count += 1

            if count == 0:
                return start + match.start(), start + match.end()

    def _is_within_comment(self, match):
        # Previous opening comment
        try:
            opening = match.start() -\
                self.content[match.start()::-1].index("--!<")
        except ValueError:
            opening = None
        # Previous closing comment
        try:
            closing = match.start() -\
                self.content[match.start()::-1].index(">--")
        except ValueError:
            closing = None

        if opening is not None:
            if closing is not None:
                if closing > opening:
                    return False
                else:
                    return True
            else:
                return True
        else:
            return False


if __name__ == "__main__":
    document = DumbXml(
        '<resources><string name="foo">hello world</string></resources>'
    )
    strings = document.find('string')
    string, _ = next(strings)
    print("{}: {}".format(string.attrs['name'], string.inner))


class DumbXmlSyntaxError(Exception):
    pass


class NewDumbXml(object):
    """ A utility to help process an XML string. The main focuses are:

        1. Return the exact contents of whatever's encountered, don't unescape
           anything
        2. Return the positions of stuff encountered, relative to the root
           string.

        To initialize, simply pass an XML string:

            >>> dumb_xml = DumbXml('<a key="value">b</a> tail here')


        If you want, you can pass an extra argument to identify an embedded XML
        tag within the outer one, like this:

            >>> source = '<a><b>hello world</b></a>'
            >>> inner = DumbXml(source, start=3)

        If you do this, the positions retrieved will be relative to the outer
        string:

            >>> assert inner.text_position == 6

        The properties you can retrieve from a DumbXml instance are (for the
        example, consider this string: '<a key="value">b</a> tail here'):

        - `dumb_xml.position`: The starting position of the xml tag (in our
          example: `0`)

        - `dumb_xml.tag`: the name of the tag (in our example: `'a'`)

            - If this is a namespaced tag, this utility does not bother with it
              and will happily return something like: `'xliff:g'`.

            - In case of a comment, this will return '!--' which is equal to
              DumbXml.COMMENT

        - `dumb_xml.attributes`: The attributes of the tag, if any, as list of
          tuples in `(key_position, key, value_position, value)` format
          (in our example: `[(3, 'key', 8, "value")]`)

        - `dumb_xml.attrib`: The attributes of the tag, if any, in dict format
          (in our example: `{'key': "value"}`)

        - `dumb_xml.text_position`: The starting point of the tag's contents
          (in our example: `15`)

            - For single tags (eg '<br />'), `text` and `text_position` will be
              None; for empty tags (eg, '<li></li>'), `text` will be `''` and
              `text_position` will be the expected value

        - `dumb_xml.text`: The text content of the tag (in our example: `'b'`)

            - If the contents begin with text but also have other xml tags, we
              follow lxml's approach: `text` is what's contained between the
              start of the tag's content and the start of the first inner tag.
              To get the rest of the text contents, you have to access `tail`
              on the inner tags (see below).

        - `for inner_tag in dumb_xml`: Iterating over a DumbXml object will
          generate all contained tags

            - The positions returned by the inner tags' properties will be
              relative to the root string.

        - `dumb_xml.content`: If the tag contains children tags, `content` is
          the whole contents in string form, otherwise it's the same as `text`
          (in our example `'b'`)

        - `dumb_xml.content_end`: the position where the contents of the tag
          end (in our example, `16`)

        - `dumb_xml.tail`: The text contained between the end of this tag and
          either the start of the next one or the end of the source string (in
          our example: `' tail here'`)

        - `dumb_xml.tail_position`: The starting position of tail

        - `dumb_xml.end`: the end of the tail (should be the start of the next
          tag, if there is one)

        Two finding methods are supported, `find_children` and
        `find_descendants`, which accept a tag name or list of tag names as
        argument. If the argument is left None, all children and descendants
        will be yielded.
    """

    BACKSLASH = u"\\"
    FORWARD_SLASH = u"/"
    SINGLE_QUOTE = u"'"
    DOUBLE_QUOTES = u'"'
    LESS_THAN = u"<"
    GREATER_THAN = u">"
    NEWLINE = u"\n"
    EQUAL_SIGN = u"="

    class NOT_CACHED:
        "Special value for None because for some properties, None is valid"

    COMMENT = '!--'

    def __init__(self, source, start=0):
        self.source = source
        self.start = start
        self._position = self._tag = self._attrib = self._attrib_string =\
            self._attributes = self._text_position = self._text =\
            self._content_end = self._tail_position = self._tail =\
            self.NOT_CACHED

        # Start with tag because if this is a comment, it will mess up with the
        # retrieving of other attributes
        self.tag

    @property
    def position(self):
        """ The starting position of the tag.

            <atag>Some text</atag>
            ^
        """

        if self._position is not self.NOT_CACHED:
            return self._position

        self._position = self._find_next_lt(self.start)
        return self._position

    @property
    def tag(self):
        """ The name of the tag.

            <atag>Some text</atag>
             ^^^^
        """

        if self._tag is not self.NOT_CACHED:
            return self._tag

        start = self.position
        end = start + len("<!--")
        if self.source[start:end] == "<!--":
            self._tag = self.COMMENT
            self._process_comment()
            return self._tag

        for ptr in six.moves.xrange(self.position + 1, len(self.source)):
            candidate = self.source[ptr]
            if (candidate in (self.FORWARD_SLASH, self.GREATER_THAN) or
                    candidate.isspace()):
                self._tag = self.source[self.position + 1:ptr]
                return self._tag
        raise DumbXmlSyntaxError(u"Opening tag not closed on line {}".
                                 format(self._find_line_number()))

    @property
    def attributes(self):
        if self._attributes is not self.NOT_CACHED:
            return self._attributes

        start = self.position + 1 + len(self.tag)
        self._attributes = []
        (status,
         key_position, key, value_position, value, quote_type) = [None] * 6

        for ptr in count(start):
            try:
                candidate = self.source[ptr]
            except IndexError:
                raise DumbXmlSyntaxError(
                    u"Opening tag '{}' not closed on line {}".
                    format(self.tag, self._find_line_number())
                )

            # `status` lifecycle should be:
            #   None -> IN_KEY -> AFTER_KEY -> IN_VALUE -> None -> ...
            if status is None:
                if candidate in (self.FORWARD_SLASH, self.GREATER_THAN):
                    # <a .... /> or <a .... >
                    #         ^             ^
                    break
                elif candidate.isspace():
                    # <a   b="c">
                    #    ^
                    continue
                else:
                    # <a  b="c" ...>
                    #     ^
                    status, key_position, key = "IN_KEY", ptr, [candidate]
            elif status == "IN_KEY":
                if candidate == self.EQUAL_SIGN:
                    # <a b="c" ... >
                    #     ^
                    status = "AFTER_KEY"
                else:
                    # <a bcd="ef" ... >
                    #     ^
                    key.append(candidate)
            elif status == "AFTER_KEY":
                if candidate in (self.SINGLE_QUOTE, self.DOUBLE_QUOTES):
                    # <a b="cde" ... >
                    #      ^
                    (status, quote_type, value_position,
                     value) = "IN_VALUE", candidate, ptr + 1, []
            elif status == "IN_VALUE":
                if candidate == quote_type:
                    # <a b="c" ... >
                    #        ^
                    status = None
                    self._attributes.append((key_position, u''.join(key),
                                             value_position, u''.join(value)))
                else:
                    # <a b="cde" ... >
                    #        ^
                    value.append(candidate)

        self._attrib_string = self.source[start:ptr]

        return self._attributes

    @property
    def attrib(self):
        if self._attrib is not self.NOT_CACHED:
            return self._attrib

        self._attrib = {key: value for _, key, _, value in self.attributes}

        return self._attrib

    @property
    def text_position(self):
        """ The start position of the text.

            <atag>Some text</atag>
                  ^
        """

        if self._text_position is not self.NOT_CACHED:
            return self._text_position

        if self._attrib_string is self.NOT_CACHED:
            self.attrib  # This will generate self._attrib_string

        ptr = self.position + 1 + len(self.tag) + len(self._attrib_string)
        candidate = self.source[ptr]
        # Based on how we calculated '_attrib_string', this should either be
        # '/' or '>'
        if candidate == self.FORWARD_SLASH:
            # This is a "single-tag", eg '<br />'
            self._text_position = None
            start = ptr + 1
            for ptr in six.moves.xrange(start, len(self.source)):
                candidate = self.source[ptr]
                if candidate.isspace():
                    continue
                elif candidate == self.GREATER_THAN:
                    self._tail_position = ptr + 1
                    return self._text_position
                else:
                    raise DumbXmlSyntaxError(
                        u"Opening tag '{}' not closed on line {}".
                        format(self.tag, self._find_line_number())
                    )
            raise DumbXmlSyntaxError(
                u"Opening tag '{}' not closed on line {}".
                format(self.tag, self._find_line_number())
            )
        elif candidate == self.GREATER_THAN:
            self._text_position = ptr + 1
            return self._text_position
        else:
            raise DumbXmlSyntaxError(u"Something went wrong")

    @property
    def text(self):
        """ The text of the tag (up until the first child tag).

            <atag>Some <b>text</b></atag>
                  ^^^^^
        """

        if self._text is not self.NOT_CACHED:
            return self._text

        if self.text_position is None:
            self._text = None
            return self._text

        next_tag_position = self._find_next_lt(self.text_position)
        if next_tag_position == len(self.source):
            raise DumbXmlSyntaxError(
                u"Tag '{}' not closed on line {}".
                format(self.tag, self._find_line_number())
            )

        self._text = self.source[self.text_position:next_tag_position]
        return self._text

    def __iter__(self):
        if self.text is None or self.tag == self.COMMENT:
            return

        start = self.text_position + len(self.text)
        while True:
            if self.source[start + 1] == self.FORWARD_SLASH:
                # We found the closing tag
                self._content_end = start
                closing_tag = self.source[start + 2: start + 2 + len(self.tag)]
                if closing_tag != self.tag:
                    raise DumbXmlSyntaxError(
                        u"Closing tag '{}' does not match opening tag '{}' on "
                        u"line {}".
                        format(closing_tag, self.tag, self._find_line_number())
                    )
                for ptr in six.moves.xrange(start + 2 + len(self.tag),
                                            len(self.source)):
                    candidate = self.source[ptr]
                    if candidate.isspace():
                        continue
                    elif candidate == self.GREATER_THAN:
                        self._tail_position = ptr + 1
                        return
                    else:
                        raise DumbXmlSyntaxError(
                            u"Invalid closing of tag '{}' on line {}".
                            format(self.tag, self._find_line_number())
                        )
                raise DumbXmlSyntaxError(
                    u"Invalid closing of tag '{}' on line {}".
                    format(self.tag, self._find_line_number())
                )
            else:
                # Use `self.__class__` in case this is a subclass (eg to handle
                # HTML)
                inner = self.__class__(self.source, start)
                yield inner
                start = inner.end

    @property
    def content_end(self):
        """ The end of all contents of a tag (both text and children tags)

            <a>goobye <b>cruel</b> world</a>
                                        ^
        """

        if self._content_end is not self.NOT_CACHED:
            return self._content_end
        if self.text_position is None:
            return None
        for _ in self:
            pass
        return self._content_end

    @property
    def content(self):
        """ All the contents of a tag (both text and children tags)

            <a>goobye <b>cruel</b> world</a>
               ^^^^^^^^^^^^^^^^^^^^^^^^^
        """

        if self.tag == self.COMMENT:
            return self.text
        if self.content_end is None:
            return None
        return self.source[self.text_position:self.content_end]

    @property
    def tail_position(self):
        """ The position just after the tag.

            <atag>Some text</atag> newlines etc <anothertag>...
                                  ^
        """

        if self._tail_position is not self.NOT_CACHED:
            return self._tail_position

        # _tail_position might be set when getting 'text_position'
        self.text_position
        if self._tail_position is not self.NOT_CACHED:
            return self._tail_position

        # Otherwise, it should be set after iterating
        for _ in self:
            pass
        return self._tail_position

    @property
    def tail(self):
        """The text that follows the tag untill the start of a news one.

            <atag>Some text</atag> newlines etc <anothertag>...
                                  ^^^^^^^^^^^^^^
        """

        if self._tail is not self.NOT_CACHED:
            return self._tail

        self._tail = self.source[self.tail_position:
                                 self._find_next_lt(self.tail_position)]
        return self.tail

    @property
    def end(self):
        """The starting position of the next tag.

            <atag>Some text</atag> newlines etc <anothertag>...
                                                ^
        """

        return self.tail_position + len(self.tail)

    def find_children(self, *tags):
        for child in self:
            if not tags or child.tag in tags:
                yield child

    def find_descendants(self, *tags):
        for child in self:
            if not tags or child.tag in tags:
                yield child
            for inner in child.find_descendants(*tags):
                yield inner

    def _find_next_lt(self, start):
        in_cdata = False
        for ptr in six.moves.xrange(start, len(self.source)):
            candidate = self.source[ptr]
            if in_cdata:
                if (candidate == ']' and
                        self.source[ptr:ptr + len("]]>")] == "]]>"):
                    in_cdata = False
            else:
                if candidate == self.LESS_THAN:
                    # Check against CDATA
                    if self.source[ptr:ptr + len("<![CDATA[")] == "<![CDATA[":
                        in_cdata = True
                    else:
                        return ptr
        # We reached the end of the string, lets return accordingly
        return len(self.source)

    def _process_comment(self):
        # We already know position and tag

        self._attrib_string = ""
        self._attributes = []
        self._attrib = {}

        self._text_position = self.position + len("<!--")
        for ptr in six.moves.xrange(self._text_position, len(self.source)):
            candidate = self.source[ptr]
            if candidate == '-' and self.source[ptr:ptr + len("-->")] == "-->":
                self._content_end = ptr
                self._text = self.source[self.text_position:ptr]
                self._tail_position = ptr + len("-->")
                return
        raise DumbXmlSyntaxError(u"Comment not closed on line {}".
                                 format(self._find_line_number()))

    def _find_line_number(self, ptr=None):
        ptr = ptr or self.position
        return self.source[:ptr].count(self.NEWLINE) + 1


for symbol in (NewDumbXml.BACKSLASH, NewDumbXml.FORWARD_SLASH,
               NewDumbXml.SINGLE_QUOTE, NewDumbXml.DOUBLE_QUOTES,
               NewDumbXml.LESS_THAN, NewDumbXml.GREATER_THAN,
               NewDumbXml.NEWLINE, NewDumbXml.EQUAL_SIGN):
    assert len(symbol) == 1
